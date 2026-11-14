# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Account Adjustment: a numbered credit note (CN-...) or debit note (DN-...) that corrects a consumer
account, linked to the bill it corrects (FS-REC-16), with a reason code (FS-CFG-26). For example a
surcharge waiver is a credit note on the Surcharge balance.

Approved through "Account Adjustment Approval": a Billing Officer or Billing Manager makes it, a
General Manager approves it, and only then is it posted to the account."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import flt, nowdate

from utility_billing.utility_billing import account, approval

APPROVER_ROLE = "General Manager"
PREFIX = {"Credit Note": "CN", "Debit Note": "DN"}


class AccountAdjustment(Document):
	def autoname(self):
		self.name = make_autoname(f"{PREFIX[self.note_type]}-.YYYY.-.#####", doc=self)

	def validate(self):
		conn = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"], as_dict=True)
		self.consumer_name, self.society = conn.consumer_name, conn.society
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Enter the amount."))
		account.check_reason(self.reason_code, "Waiver / Adjustment")
		if self.against_bill:
			bill = frappe.db.get_value("Utility Bill", self.against_bill, ["connection", "docstatus"], as_dict=True)
			if not bill or bill.connection != self.connection or bill.docstatus != 1:
				frappe.throw(_("Bill {0} isn't a posted bill of account {1}.").format(self.against_bill, self.connection))
		self.adjustment_title = f"{self.note_type} {self.connection} Rs {self.amount:,.0f}"
		approval.validate_transition(self)
		if self.workflow_state == approval.PENDING and self.note_type == "Credit Note":
			self.check_credit_note()

	def check_credit_note(self):
		bucket = account.BUCKET_OF[self.bucket]
		available = flt(account.balances(self.connection)[bucket])
		if self.amount > available:
			frappe.throw(_("A credit note can't exceed the {0} balance of Rs {1}.").format(_(self.bucket), available))

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A credit or debit note is approved through its workflow, not submitted directly."))

	def on_update(self):
		approval.notify(self, APPROVER_ROLE)

	def on_submit(self):
		entry = account.post_note(self)
		self.db_set({"ledger_entry": entry.name, "posting_date": nowdate()})
		approval.notify(self, APPROVER_ROLE)

	def on_cancel(self):
		account.reverse_entry(self.ledger_entry, "Account Adjustment", self.name,
		                      remarks=_("{0} {1} cancelled").format(self.note_type, self.name))
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()
