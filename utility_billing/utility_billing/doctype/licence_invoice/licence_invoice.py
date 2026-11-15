# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Licence Invoice: SoftVerx's invoice to a client for a month's active accounts (increment 24; see licensing.py)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from utility_billing.utility_billing import licensing


class LicenceInvoice(Document):
	def validate(self):
		c = frappe.get_doc("Licence Client", self.client)
		self.month = licensing.month_start(self.month)
		if frappe.db.exists("Licence Invoice", {"client": self.client, "month": self.month, "docstatus": ["<", 2], "name": ["!=", self.name or ""]}):
			frappe.throw(_("{0} already has an invoice for {1}.").format(self.client, self.month.strftime("%B %Y")))
		self.accounts = max(cint(self.accounts), 0)
		self.rate, self.currency, self.tax_percent = flt(c.rate_per_account), c.currency, flt(c.tax_percent)
		self.amount = round(self.accounts * self.rate, 2)
		self.minimum_applied = 1 if flt(c.monthly_minimum) > self.amount else 0
		self.net_amount = flt(c.monthly_minimum) if self.minimum_applied else self.amount
		self.tax_amount = round(self.net_amount * self.tax_percent / 100, 2)
		self.total = round(self.net_amount + self.tax_amount, 2)
		self.due_date = licensing.due_date_for(self.invoice_date, self.client)
		if self.docstatus == 0:
			self.paid_amount, self.outstanding, self.status = 0, self.total, "Draft"

	def on_submit(self):
		self.db_set({"status": licensing.invoice_status(self.total, self.total, self.due_date), "outstanding": self.total, "paid_amount": 0})
		if self.usage:
			frappe.db.set_value("Licence Usage", self.usage, {"status": "Invoiced", "invoice": self.name})

	def before_cancel(self):
		if flt(self.paid_amount) > 0.005:
			frappe.throw(_("Invoice {0} has payments; cancel those payments first.").format(self.name))

	def on_cancel(self):
		self.db_set({"status": "Cancelled", "outstanding": 0})
		if self.usage:
			frappe.db.set_value("Licence Usage", self.usage, {"status": "Received", "invoice": None})
