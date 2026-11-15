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
		billing.post_run(self)
		gl.after_run_posted(self)
		notify.after_run_posted(self)
		approval.notify(self, APPROVER_ROLE)

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
