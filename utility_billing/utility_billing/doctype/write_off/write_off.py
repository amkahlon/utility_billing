# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Write Off: arrears, surcharge and fees the society can't recover are written off, with a board resolution and a record of what
was tried (FS-REC-09, FS-WF-14). An Accounts Officer or Billing Manager makes it; the approver the Society's levels name for its
amount (default a General Manager) approves it, and only then is it posted: arrears first, then surcharge, then fees. The
amount is booked to the society's write-off expense account."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import account, approval, plans

DEFAULT_APPROVER = "General Manager"


class WriteOff(Document):
	def validate(self):
		conn = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"], as_dict=True)
		self.consumer_name, self.society = conn.consumer_name, conn.society
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Enter the amount to write off."))
		account.check_reason(self.reason_code, "Write-off")
		if len((self.resolution_no or "").strip()) < 1:
			frappe.throw(_("Enter the board resolution number."))
		if len((self.remarks or "").strip()) < 10:
			frappe.throw(_("Describe the recovery steps that were tried (at least 10 characters)."))
		if self.docstatus == 0:
			self.owed_now = round(plans.owed_arrears(self.connection), 2)
			if self.amount > self.owed_now + 0.005:
				frappe.throw(_("The account owes Rs {0} in arrears, surcharge and fees; that is the most that can be written off.").format(f"{self.owed_now:,.2f}"))
		self.writeoff_title = f"Write off {self.consumer_name or self.connection} Rs {self.amount:,.0f}"
		approval.validate_transition(self)

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A write-off is approved through its workflow, not submitted directly."))

	def on_update(self):
		approval.notify(self, DEFAULT_APPROVER)

	def on_submit(self):
		entry = account.post_write_off(self)
		self.db_set({"ledger_entry": entry.name, "posting_date": frappe.utils.nowdate()})
		approval.notify(self, DEFAULT_APPROVER)

	def on_cancel(self):
		account.reverse_entry(self.ledger_entry, "Write Off", self.name, remarks=_("Write-off {0} cancelled").format(self.name))
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()
