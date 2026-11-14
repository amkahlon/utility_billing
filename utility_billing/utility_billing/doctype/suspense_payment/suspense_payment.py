# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Suspense Payment: money received with a wrong or missing reference number, held until it is matched to an account or
returned (FS-PAY-11)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from utility_billing.utility_billing import settlement


class SuspensePayment(Document):
	def validate(self):
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Enter the amount."))
		self.channel_txn_id = (self.channel_txn_id or "").strip()
		other = frappe.db.get_value("Suspense Payment", {"society": self.society, "mode": self.mode, "channel_txn_id": self.channel_txn_id,
		                                                 "status": ["!=", "Returned to Payer"], "name": ["!=", self.name]})
		if other:
			frappe.throw(_("Transaction {0} is already held as {1}: a channel transaction can post only once.").format(self.channel_txn_id, other))
		if frappe.db.exists("Payment Receipt", {"channel_txn_id": self.channel_txn_id, "mode": self.mode, "society": self.society, "docstatus": 1}) \
		        and self.status == "Held":
			frappe.throw(_("Transaction {0} is already recorded as a payment.").format(self.channel_txn_id))
		before = self.get_doc_before_save()
		if before and before.status != "Held" and not self.flags.resolving:
			frappe.throw(_("This payment is {0} and can't be changed.").format(before.status.lower()))
		self.suspense_title = f"{self.channel_txn_id} · Rs {self.amount:,.0f} · {getdate(self.payment_date).strftime('%d-%m-%Y')}"

	@frappe.whitelist()
	def match(self, connection):
		settlement.match_suspense(self, connection)
		self.flags.resolving = True
		self.save()
		return self.payment_receipt

	@frappe.whitelist()
	def return_to_payer(self, remarks):
		settlement.return_suspense(self, remarks)
		self.flags.resolving = True
		self.save()
		return self.status
