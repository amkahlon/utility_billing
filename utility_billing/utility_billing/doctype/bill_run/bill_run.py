# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill Run: one month's bills for a society and utility (FS-RUN-01..05).

Draft: calculate as often as needed -> Submit for Approval (Billing Officer) -> Approve (Billing
Manager), which posts every bill. Maker-checker through the shared approval module."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate

from utility_billing.utility_billing import approval, billing, gl, notify, progress
from utility_billing.utility_billing.readings import month_start

APPROVER_ROLE = "Billing Manager"  # used only when the run has no active approval workflow
RECALC_FIELDS = ("issue_date", "exclusions")


class BillRun(Document):
	def validate(self):
		self.billing_month = month_start(self.billing_month)
		days = cint(frappe.db.get_value("Society", self.society, "days_to_due_date")) or 14
		self.due_date = add_days(self.issue_date, days)
		self.run_title = f"{self.society} {self.utility} {getdate(self.billing_month).strftime('%b %Y')}"
		self.check_duplicate()
		seen = set()
		for row in self.exclusions:
			if row.connection in seen:
				frappe.throw(_("Connection {0} is excluded twice.").format(row.connection))
			seen.add(row.connection)
		self.mark_stale_calculation()
		self.check_ready_for_approval()
		approval.validate_transition(self)

	def check_duplicate(self):
		"""One main run per month; a supplementary run bills the accounts left out of it (FS-RUN-03), once the main run is posted."""
		filters = {"society": self.society, "utility": self.utility, "billing_month": self.billing_month, "docstatus": ["<", 2], "name": ["!=", self.name]}
		if not cint(self.get("supplementary")):
			other = frappe.db.get_value("Bill Run", {**filters, "supplementary": 0}, "name")
			if other:
				frappe.throw(_("Bill run {0} already covers this month. Tick Supplementary to bill the accounts left out of it.").format(other))
			return
		main = frappe.db.get_value("Bill Run", {**filters, "supplementary": 0}, ["name", "docstatus"], as_dict=True)
		if not main or main.docstatus != 1:
			frappe.throw(_("A supplementary run follows the month's main run once it is approved."))
		open_supp = frappe.db.get_value("Bill Run", {**filters, "supplementary": 1, "docstatus": 0}, "name")
		if open_supp:
			frappe.throw(_("Supplementary run {0} is still open for this month.").format(open_supp))

	def mark_stale_calculation(self):
		"""Changing the issue date or exclusions after calculating means the bills must be recalculated."""
		if self.is_new() or self.flags.from_calculation:
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if str(before.issue_date) != str(self.issue_date) or sorted(
			(r.connection, r.reason) for r in before.exclusions
		) != sorted((r.connection, r.reason) for r in self.exclusions):
			self.calculation_status = "Not calculated"

	def check_ready_for_approval(self):
		before = None if self.is_new() else self.get_doc_before_save()
		submitting = self.workflow_state == approval.PENDING and (before and before.workflow_state != approval.PENDING)
		if submitting:
			if self.calculation_status != "Calculated":
				frappe.throw(_("Calculate the bills before submitting the run for approval."))
			if not cint(self.bill_count):
				frappe.throw(_("This run has no bills."))

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A bill run is approved through its workflow, not submitted directly."))

	def on_update(self):
		approval.notify(self, APPROVER_ROLE)

	def on_submit(self):
		approval.notify(self, APPROVER_ROLE)
		tracked = frappe.db.has_column("Bill Run", "posting_status")  # (before the migration that adds it, post as before)
		if tracked and cint(self.bill_count) > billing.BACKGROUND_THRESHOLD and not frappe.flags.in_test and not frappe.flags.post_in_foreground:
			# FS-RUN-07: thousands of bills take minutes, longer than a web request may run; the approval returns at once
			self.db_set({"posting_status": "Posting", "posting_error": None})
			queue_posting(self.name, cint(self.bill_count))
			return
		post_now(self, tracked)

	def before_cancel(self):
		# Checked before Frappe marks the run cancelled, so a refused cancel changes nothing.
		if len((self.cancellation_reason or "").strip()) < 5:
			frappe.throw(_("Give the reason for reversing this run (FS-RUN-06)."))
		billing.check_run_cancellable(self)

	def on_cancel(self):
		billing.cancel_run_bills(self)
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()

	def on_trash(self):
		for name in frappe.get_all("Utility Bill", filters={"bill_run": self.name, "docstatus": 0}, pluck="name"):
			frappe.delete_doc("Utility Bill", name, force=True, ignore_permissions=True)


# ---------------------------------------------------------------- posting (FS-RUN-07: a large run posts in the background)
def post_now(run, tracked=True):
	billing.post_run(run)
	progress.step(_("Making the journal"))
	gl.after_run_posted(run)
	progress.step(_("Preparing the notices"))
	notify.after_run_posted(run)
	if tracked:
		run.db_set({"posting_status": "Posted", "posting_error": None})


POSTING = "posting"  # the run's posting in the shared progress store (increment 54: a progress bar, not "Reload in a few minutes")


def queue_posting(run, total):
	progress.tell(POSTING, run, "Queued", 0, total, _("Waiting to start"))
	frappe.enqueue(post_in_background, queue="long", timeout=7200, run=run, enqueue_after_commit=True)


def post_in_background(run):
	"""Post an approved run's bills (committed every 500, each bill with its ledger entry), then its journal and notices. On an error
	the bills not yet posted stay drafts and "Post Again" carries on with them; the journal and notices come once all are posted."""
	doc = frappe.get_doc("Bill Run", run)
	if doc.docstatus != 1 or doc.posting_status == "Posted":
		return
	frappe.flags.post_batch = 500
	try:
		with progress.reporting(POSTING, run):
			post_now(doc)
		frappe.db.commit()
		progress.tell(POSTING, run, "Done", doc.bill_count, doc.bill_count, _("{0} bills posted.").format(cint(doc.bill_count)))
	except Exception as e:
		frappe.db.rollback()
		said = (frappe.utils.strip_html(str(e)) if isinstance(e, frappe.ValidationError)
		        else _("Posting stopped with an error ({0}).").format(e.__class__.__name__))
		frappe.db.set_value("Bill Run", run, {"posting_status": "Posting failed", "posting_error": frappe.get_traceback()[-1000:]})
		frappe.db.commit()
		progress.tell(POSTING, run, "Failed", error=said)
		raise
	finally:
		frappe.flags.post_batch = None


@frappe.whitelist()
def posting_progress(run: str):
	"""How far the run's background posting is: {state, done, total, text, error}. A posting silent for 20 minutes is taken as stopped,
	and the run then offers Post Again. With no news kept (Redis restarted), the bills already posted are counted instead."""
	doc = frappe.get_doc("Bill Run", run)
	doc.check_permission("read")
	p = progress.get(POSTING, run, stopped=_("Posting stopped: nothing was heard from it for {0} minutes. Post Again carries on with the "
	                                         "bills not yet posted.").format(progress.STALE_AFTER // 60))
	if doc.posting_status == "Posting":
		if not p:
			done = frappe.db.count("Utility Bill", {"bill_run": run, "docstatus": 1})
			return {"state": "Running", "done": done, "total": cint(doc.bill_count), "text": _("Posting the bills")}
		if p.get("state") == "Failed":
			frappe.db.set_value("Bill Run", run, {"posting_status": "Posting failed", "posting_error": p.get("error")}, update_modified=False)
		elif p.get("state") == "Done":
			p = {**p, "state": "Running"}  # the job's commit of "Posted" is a moment behind its news
		return p
	if p and progress.running(p):  # the run says it isn't posting: older news
		p = None
	if not p and doc.posting_status in ("Posted", "Posting failed"):
		p = {"state": "Done" if doc.posting_status == "Posted" else "Failed", "done": cint(doc.bill_count), "total": cint(doc.bill_count),
		     "error": doc.posting_error and _("Posting stopped before every bill was posted.")}
	return p


@frappe.whitelist(methods=["POST"])
def retry_posting(run: str):
	"""A run whose background posting stopped carries on, once the cause is fixed, with the bills not yet posted."""
	if not frappe.has_permission("Bill Run", "submit"):  # whoever the role permissions let submit (approve) bill runs
		raise frappe.PermissionError(_("You can't post bill runs."))
	doc = frappe.get_doc("Bill Run", run)
	if doc.docstatus != 1 or doc.posting_status != "Posting failed":
		frappe.throw(_("Run {0} isn't waiting to be posted again.").format(run))
	if progress.running(progress.get(POSTING, run)):  # a job still at work: a second one would post the same bills
		frappe.throw(_("Run {0} is still being posted. Wait for it to finish.").format(run))
	doc.db_set({"posting_status": "Posting", "posting_error": None})
	queue_posting(run, cint(doc.bill_count))
	return {"queued": True}


REVERSING, REVERSAL_STOPPED, REVERSED = "Reversing", "Reversal stopped", "Reversed"
REVERSE_BATCH = 500


def cancel_in_background(run: str) -> dict:
	"""The workflow's Cancel (reverse the run), from a background job with a progress bar (increment 55).
	Increment 58: the bills are reversed and committed REVERSE_BATCH at a time (all of a society's bills in one transaction were more
	than the database takes). A stop leaves the run Approved and part-reversed (Reversal stopped); starting it again (Reverse Again)
	carries on with the bills still posted. Once none is left, the workflow's Cancel closes the run. The workflow's rules (who may
	cancel, the reason, a closed month, entries posted after the bills) are checked before anything is reversed."""
	from frappe.model.workflow import apply_workflow  # noqa: PLC0415

	doc = frappe.get_doc("Bill Run", run)
	if doc.docstatus == 2:
		return {"workflow_state": doc.workflow_state, "bills": cint(doc.bill_count)}
	check_may_reverse(doc)
	doc = reverse_in_batches(doc, lambda: apply_workflow(frappe.get_doc("Bill Run", run), "Cancel"))  # no bills are left for its on_cancel
	return {"workflow_state": doc.workflow_state, "bills": cint(doc.bill_count)}


def reverse_in_batches(doc, close):
	"""Reverse a posted run's bills, committed a batch at a time, then `close()` cancels the run itself and returns it. Used by the
	workflow's Cancel and by a snapshot restore. On a stop the run is marked Reversal stopped and a ValidationError says how far it got."""
	run = doc.name
	doc.before_cancel()  # the reason, a closed month, entries after the bills: refused with nothing changed
	commits = not frappe.flags.in_test or frappe.flags.reverse_batch  # a test runs inside its own transaction unless it sets a batch
	frappe.flags.reverse_batch = frappe.flags.reverse_batch or (REVERSE_BATCH if commits else None)
	doc.db_set({"reversal_status": REVERSING, "reversal_error": None}, update_modified=False)
	if commits:
		frappe.db.commit()
	try:
		billing.cancel_run_bills(doc)
		progress.step(_("Closing the run"))
		doc = close()
		doc.db_set("reversal_status", REVERSED, update_modified=False)
	except Exception as e:
		if not commits:
			raise
		frappe.db.rollback()
		left = frappe.db.count("Utility Bill", {"bill_run": run, "docstatus": 1})
		cause = frappe.utils.strip_html(str(e)) if isinstance(e, frappe.ValidationError | frappe.PermissionError) else e.__class__.__name__
		said = _("Reversing stopped with {0} of {1} bills still posted ({2}). The bills reversed so far stay reversed; Reverse Again on "
		         "the run carries on with the rest.").format(left, cint(doc.bill_count), cause)
		frappe.db.set_value("Bill Run", run, {"reversal_status": REVERSAL_STOPPED, "reversal_error": said}, update_modified=False)
		frappe.db.commit()
		raise frappe.ValidationError(said) from e
	finally:
		frappe.flags.reverse_batch = None
	return doc


def check_may_reverse(doc):
	"""Whether this user may reverse the run: the workflow's Cancel is open to them (or, with no workflow, the cancel permission)."""
	from frappe.model.workflow import get_transitions  # noqa: PLC0415

	if approval.workflow_active(doc.doctype):
		if "Cancel" not in {t.get("action") for t in get_transitions(doc)}:
			raise frappe.PermissionError(_("You can't reverse bill run {0}.").format(doc.name))
	else:
		doc.check_permission("cancel")
