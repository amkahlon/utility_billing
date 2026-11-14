# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Refund: pays back a consumer's credit balance, with a reason, the payment details, and approval (FS-PAY-16, FS-WF-13).

Approved through "Refund Approval": a Billing Officer, Billing Manager or Accounts Officer makes it, a General Manager
approves it (never the maker), and only then is it posted to the account as a Refund entry that reduces the credit.
The deposit refund and account closure come with the service request workflow."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from utility_billing.utility_billing import account, approval

APPROVER_ROLE = "General Manager"
INSTRUMENT_MODES = ("Cheque", "Pay Order")


class Refund(Document):
	def validate(self):
		conn = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"], as_dict=True)
		self.consumer_name, self.society = conn.consumer_name, conn.society
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Enter the amount to refund."))
		account.check_reason(self.reason_code, "Refund")
		if not (self.payee_name or "").strip():
			frappe.throw(_("Enter who the refund is paid to."))
		if self.refund_mode in INSTRUMENT_MODES and not (self.instrument_no or "").strip():
			frappe.throw(_("Enter the {0} number.").format(_(self.refund_mode).lower()))
		self.refund_title = f"Refund {self.connection} Rs {self.amount:,.0f}"
		approval.validate_transition(self)
		if self.workflow_state == approval.PENDING:
			self.check_credit()

	def check_credit(self):
		credit = flt(account.balances(self.connection)["credit"])
		if self.amount > credit:
			frappe.throw(_("The account holds Rs {0} of credit; the refund can't be more than that.").format(f"{credit:,.2f}"))

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A refund is approved through its workflow, not submitted directly."))
		self.check_credit()

	def on_update(self):
		approval.notify(self, APPROVER_ROLE)

	def on_submit(self):
		entry = account.post_refund(self)
		self.db_set({"ledger_entry": entry.name, "posting_date": nowdate()})
		approval.notify(self, APPROVER_ROLE)

	def on_cancel(self):
		account.reverse_entry(self.ledger_entry, "Refund", self.name, remarks=_("Refund {0} cancelled").format(self.name))
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()
