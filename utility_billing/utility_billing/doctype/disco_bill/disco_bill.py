# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""DISCO Bill: the electricity company's monthly bill to the society and the units that entered each feeder and
transformer (FS-RPT-08, 30, 31, 34, 38). One per society, utility and month. The line loss, AT&C and DISCO
reconciliation reports compare it with the society's own billing."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from utility_billing.utility_billing.readings import month_start


class DISCOBill(Document):
	def validate(self):
		self.billing_month = month_start(self.billing_month)
		self.units_kwh = flt(self.units_kwh)
		if self.units_kwh <= 0:
			frappe.throw(_("Enter the units on the DISCO's bill."))
		for f in ("energy_amount", "fpa_amount", "qta_amount", "other_amount"):
			self.set(f, flt(self.get(f)))
		if self.energy_amount < 0:
			frappe.throw(_("Energy Charges can't be negative."))
		# FPA, QTA and other charges can be credits (negative) on the DISCO's bill; the total is simply the sum.
		self.total_amount = flt(self.energy_amount) + flt(self.fpa_amount) + flt(self.qta_amount) + flt(self.other_amount)
		if self.due_date and getdate(self.due_date) < getdate(self.billing_month):
			frappe.throw(_("The due date is before the billing month."))
		self.bill_title = f"{self.society} {self.utility} {getdate(self.billing_month).strftime('%b %Y')} (DISCO)"
		self.check_unique()
		self.check_inputs()

	def check_unique(self):
		other = frappe.db.get_value("DISCO Bill", {"society": self.society, "utility": self.utility, "billing_month": self.billing_month,
		                                           "name": ["!=", self.name]})
		if other:
			frappe.throw(_("DISCO bill {0} already covers {1}.").format(other, getdate(self.billing_month).strftime("%B %Y")))

	def check_inputs(self):
		seen = set()
		for row in self.inputs:
			if bool(row.feeder) == bool(row.transformer):
				frappe.throw(_("Row {0}: choose a feeder or a transformer, not both and not neither.").format(row.idx))
			if flt(row.input_kwh) < 0:
				frappe.throw(_("Row {0}: input units can't be negative.").format(row.idx))
			key = ("feeder", row.feeder) if row.feeder else ("transformer", row.transformer)
			if key in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.feeder or row.transformer))
			seen.add(key)
			table, link = ("Feeder", row.feeder) if row.feeder else ("Transformer", row.transformer)
			if frappe.db.get_value(table, link, "society") != self.society:
				frappe.throw(_("Row {0}: {1} belongs to another society.").format(row.idx, link))
		for key in ("feeder", "transformer"):
			total = sum(flt(r.input_kwh) for r in self.inputs if r.get(key))
			if total > self.units_kwh * 1.01:
				frappe.msgprint(_("The {0} input units add up to {1:,.0f} kWh, more than the {2:,.0f} kWh on the DISCO's bill. Check the figures.").format(
					_(key), total, self.units_kwh), indicator="orange", alert=True)
