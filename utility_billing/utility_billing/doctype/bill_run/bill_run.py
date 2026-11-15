# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill Run: one month's bills for a society and utility (FS-RUN-01..05).

Draft: calculate as often as needed -> Submit for Approval (Billing Officer) -> Approve (Billing
Manager), which posts every bill. Maker-checker through the shared approval module."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate

from utility_billing.utility_billing import approval, billing, gl, notify
from utility_billing.utility_billing.readings import month_start

APPROVER_ROLE = "Billing Manager"
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
			frappe.enqueue(post_in_background, queue="long", timeout=7200, run=self.name, enqueue_after_commit=True)
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
	gl.after_run_posted(run)
	notify.after_run_posted(run)
	if tracked:
		run.db_set({"posting_status": "Posted", "posting_error": None})


def post_in_background(run):
	"""Post an approved run's bills (committed every 500, each bill with its ledger entry), then its journal and notices. On an error
	the bills not yet posted stay drafts and "Post Again" carries on with them; the journal and notices come once all are posted."""
	doc = frappe.get_doc("Bill Run", run)
	if doc.docstatus != 1 or doc.posting_status == "Posted":
		return
	frappe.flags.post_batch = 500
	try:
		post_now(doc)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.db.set_value("Bill Run", run, {"posting_status": "Posting failed", "posting_error": frappe.get_traceback()[-1000:]})
		frappe.db.commit()
		raise
	finally:
		frappe.flags.post_batch = None


@frappe.whitelist(methods=["POST"])
def retry_posting(run: str):
	"""A run whose background posting stopped carries on, once the cause is fixed, with the bills not yet posted."""
	if not set(frappe.get_roles()) & {"Billing Manager", "System Manager"}:
		raise frappe.PermissionError(_("Only a Billing Manager posts a run again."))
	doc = frappe.get_doc("Bill Run", run)
	if doc.docstatus != 1 or doc.posting_status != "Posting failed":
		frappe.throw(_("Run {0} isn't waiting to be posted again.").format(run))
	doc.db_set({"posting_status": "Posting", "posting_error": None})
	frappe.enqueue(post_in_background, queue="long", timeout=7200, run=run, enqueue_after_commit=True)
	return {"queued": True}
