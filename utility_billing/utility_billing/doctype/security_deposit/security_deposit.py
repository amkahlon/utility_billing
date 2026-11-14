# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Security Deposit: a deposit received, refunded or offset against what the account owes, held as a liability (FS-MST-10).

The deposit held on a connection is the balance of its approved Security Deposit documents (kept on Connection.security_deposit).
An Accounts Officer, Billing Officer or Billing Manager makes one; the approver the Society's levels name for its amount (default a
General Manager) approves it. A refund can't be paid while the account owes anything: offset the deposit against it first.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import account, approval, gl

DEFAULT_APPROVER = "General Manager"
RECEIVED, REFUNDED, OFFSET = "Received", "Refunded", "Offset Against Arrears"


def held(connection, exclude=None) -> float:
	"""The deposit held on the connection: received less refunded and offset (approved documents only)."""
	rows = frappe.get_all("Security Deposit", filters={"connection": connection, "docstatus": 1, "name": ["!=", exclude or ""]},
	                      fields=["deposit_type", "amount"])
	return round(sum(flt(r.amount) * (1 if r.deposit_type == RECEIVED else -1) for r in rows), 2)


class SecurityDeposit(Document):
	def validate(self):
		conn = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"], as_dict=True)
		self.consumer_name, self.society = conn.consumer_name, conn.society
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Enter the amount."))
		if self.deposit_type != OFFSET and not self.mode:
			frappe.throw(_("Say how the deposit was received or is paid back."))
		if self.deposit_type == OFFSET:
			self.mode = None
		if self.docstatus == 0:
			self.check_amount()
		self.deposit_title = f"{_(self.deposit_type)} {self.consumer_name or self.connection} Rs {self.amount:,.0f}"
		approval.validate_transition(self)

	def check_amount(self):
		have = held(self.connection, exclude=self.name)
		self.balance_before = have
		if self.deposit_type == RECEIVED:
			self.balance_after = have + self.amount
			return
		if self.amount > have + 0.005:
			frappe.throw(_("The account holds a deposit of Rs {0}; that is the most that can be {1}.").format(f"{have:,.2f}", _(self.deposit_type).lower()))
		owed = account.balance(self.connection)
		if self.deposit_type == REFUNDED and owed > 0.005:
			frappe.throw(_("The account owes Rs {0}. Offset the deposit against it before refunding the rest.").format(f"{owed:,.2f}"))
		if self.deposit_type == OFFSET:
			if owed <= 0.005:
				frappe.throw(_("The account owes nothing, so there is nothing to offset the deposit against."))
			if self.amount > owed + 0.005:
				frappe.throw(_("The account owes Rs {0}; the offset can't be more than that.").format(f"{owed:,.2f}"))
		self.balance_after = have - self.amount

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A security deposit is approved through its workflow, not submitted directly."))
		self.check_amount()  # again at approval: payments or other deposits may have changed the picture

	def on_update(self):
		approval.notify(self, DEFAULT_APPROVER)

	def on_submit(self):
		values = {"balance_before": self.balance_before, "balance_after": self.balance_after}
		if self.deposit_type == OFFSET:
			values["ledger_entry"] = account.post_deposit_offset(self).name
		else:
			values["gl_journal"] = self.journal()
		self.db_set(values)
		frappe.db.set_value("Connection", self.connection, "security_deposit", held(self.connection), update_modified=False)
		approval.notify(self, DEFAULT_APPROVER)

	def journal(self, reverse=False):
		frappe.db.savepoint("deposit_journal")
		try:
			return gl.deposit_journal(self, reverse=reverse).name
		except gl.MissingAccount as e:
			frappe.db.rollback(save_point="deposit_journal")
			frappe.msgprint(_("The deposit is recorded, but its journal waits: {0} Then use Make Journals.").format(e), indicator="orange", alert=True)
			return None

	def before_cancel(self):
		if self.deposit_type == RECEIVED and held(self.connection, exclude=self.name) < -0.005:
			frappe.throw(_("Part of this deposit was already refunded or offset; cancel those first."))

	def on_cancel(self):
		if self.deposit_type == OFFSET and self.ledger_entry:
			account.reverse_entry(self.ledger_entry, "Security Deposit", self.name, remarks=_("Deposit offset {0} cancelled").format(self.name))
		elif self.gl_journal:
			self.journal(reverse=True)
		frappe.db.set_value("Connection", self.connection, "security_deposit", held(self.connection), update_modified=False)
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()
