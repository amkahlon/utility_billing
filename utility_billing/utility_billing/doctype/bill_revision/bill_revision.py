# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill Revision: corrects an issued bill whose reading or category was wrong (FS-BIL-11, FS-WF-01, 06, 07).

A Billing Officer or Billing Manager makes it, a Billing Manager other than the maker approves it, and on approval the bill is
recalculated and replaced by a revised bill under the same reference number; the earlier version stays, marked Superseded.
This works only while nothing has been paid against the bill and its due date has not passed: after that, correct with a
credit or debit note (corrections.check_revisable says so)."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import account, approval, corrections

APPROVER_ROLE = "Billing Manager"


class BillRevision(Document):
	def validate(self):
		if not self.original_bill:
			frappe.throw(_("Choose the bill to correct."))
		account.check_reason(self.reason_code, "Revised Bill")
		if len((self.remarks or "").strip()) < 5:
			frappe.throw(_("Explain what was wrong (at least 5 characters)."))
		other = frappe.db.get_value("Bill Revision", {"original_bill": self.original_bill, "name": ["!=", self.name], "docstatus": ["<", 2],
		                                              "workflow_state": ["in", ["Draft", "Pending Approval", "Approved"]]})
		if other:
			frappe.throw(_("{0} already corrects bill {1}.").format(other, self.original_bill))
		if self.docstatus == 0:
			corrections.fill_preview(self)
		self.revision_title = f"{self.original_bill} · {self.kind}"
		approval.validate_transition(self)
		if self.workflow_state == approval.PENDING:
			corrections.check_revisable(frappe.get_doc("Utility Bill", self.original_bill))

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A bill revision is approved through its workflow, not submitted directly."))

	def on_update(self):
		approval.notify(self, APPROVER_ROLE)

	def on_submit(self):
		corrections.revise_bill(self)
		approval.notify(self, APPROVER_ROLE)

	def before_cancel(self):
		frappe.throw(_("A revised bill can't be undone. Correct it with another revision, or a credit or debit note."))
