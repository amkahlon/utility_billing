# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Fee Charge: a fee from the society's fee schedule charged to a consumer account (FS-CFG-24),
for example a duplicate bill or reconnection. Posted on submit; reversed by a Billing Manager with
a reason (account.reverse_document)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import account


class FeeCharge(Document):
	def validate(self):
		conn = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"], as_dict=True)
		self.consumer_name, self.society = conn.consumer_name, conn.society
		fee = frappe.db.get_value("Fee Type", self.fee_type, ["society", "amount", "enabled"], as_dict=True)
		if not fee or not fee.enabled:
			frappe.throw(_("Fee {0} isn't enabled.").format(self.fee_type))
		if fee.society != self.society:
			frappe.throw(_("Fee {0} belongs to another society.").format(self.fee_type))
		self.amount = flt(self.amount) or flt(fee.amount)
		if self.amount <= 0:
			frappe.throw(_("Enter the fee amount."))

	def on_submit(self):
		entry = account.post_fee(self)
		self.db_set({"ledger_entry": entry.name, "status": "Posted"})

	def before_cancel(self):
		if not self.flags.reversal:
			frappe.throw(_("A posted fee can't be cancelled. A Billing Manager reverses it with a reason."))

	def on_cancel(self):
		r = self.flags.reversal
		account.reverse_entry(self.ledger_entry, "Fee Charge", self.name,
		                      remarks=_("Fee {0} reversed: {1}").format(self.name, r["remarks"]))
		self.db_set({"status": "Reversed", "reversal_reason": r["reason_code"], "reversal_remarks": r["remarks"]})
