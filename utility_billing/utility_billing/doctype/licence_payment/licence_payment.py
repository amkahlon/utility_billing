# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Licence Payment: a client's payment to SoftVerx, against the oldest unpaid invoices unless allocated by hand; anything over is
kept as an advance (increment 24; see licensing.py)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import licensing


class LicencePayment(Document):
	def validate(self):
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Enter the amount received."))
		if not self.allocations:
			left = self.amount
			for inv in licensing.outstanding_invoices(self.client):
				if left <= 0.005:
					break
				take = min(left, flt(inv.outstanding))
				self.append("allocations", {"invoice": inv.name, "amount": take})
				left = round(left - take, 2)
		total = 0.0
		for r in self.allocations:
			inv = frappe.db.get_value("Licence Invoice", r.invoice, ["client", "docstatus", "outstanding"], as_dict=True)
			if not inv or inv.client != self.client or inv.docstatus != 1:
				frappe.throw(_("Row {0}: {1} isn't a submitted invoice of {2}.").format(r.idx, r.invoice, self.client))
			if flt(r.amount) <= 0 or flt(r.amount) > flt(inv.outstanding) + 0.005:
				frappe.throw(_("Row {0}: {1} owes {2}.").format(r.idx, r.invoice, f"{flt(inv.outstanding):,.2f}"))
			total += flt(r.amount)
		if total > self.amount + 0.005:
			frappe.throw(_("The invoices take {0}, more than the {1} received.").format(f"{total:,.2f}", f"{self.amount:,.2f}"))
		self.unallocated = round(self.amount - total, 2)

	def on_submit(self):
		for r in self.allocations:
			licensing.apply_to_invoice(r.invoice, r.amount)

	def on_cancel(self):
		for r in self.allocations:
			licensing.apply_to_invoice(r.invoice, -flt(r.amount))
