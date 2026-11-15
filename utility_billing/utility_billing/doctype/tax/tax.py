# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tax: a tax or duty a society charges on its bills (FS-CFG-14): the utilities whose bills carry it (user, 2026-11-15: for example
on electricity but not on water or gas), rate, the charges it applies to, included or added, calculation
order and tax on tax, categories and exemptions, rates by tax status, rounding, account and effective dates. Several can be in
force at once; none is the default. Use *Impact Preview* before switching one on: it shows what it would have added to last
cycle's bills."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate


class Tax(Document):
	def validate(self):
		if not 0 <= flt(self.rate) <= 100:
			frappe.throw(_("The rate must be between 0 and 100%."))
		for r in self.status_rates:
			if not 0 <= flt(r.rate) <= 100:
				frappe.throw(_("Row {0}: the rate must be between 0 and 100%.").format(r.idx))
		if len({r.tax_status for r in self.status_rates}) != len(self.status_rates):
			frappe.throw(_("Rate by Tax Status: each status can be listed once."))
		self.tax_code = (self.tax_code or "").strip().upper()
		other = frappe.db.get_value("Tax", {"society": self.society, "tax_code": self.tax_code, "name": ["!=", self.name]})
		if other:
			frappe.throw(_("{0} already uses the code {1}.").format(other, self.tax_code))
		if self.effective_from and self.effective_to and getdate(self.effective_to) < getdate(self.effective_from):
			frappe.throw(_("Effective To is before Effective From."))
		utilities = [r.utility for r in self.utilities if r.utility]
		if not utilities:
			frappe.throw(_("Choose the utilities whose bills carry this tax (for example Electricity)."))
		if len(set(utilities)) != len(utilities):
			frappe.throw(_("Utilities: each utility can be listed once."))
		if self.applies_to == "Charge groups" and not any(r.charge_group for r in self.charge_rows):
			frappe.throw(_("List the charge groups this tax applies to."))
		if self.applies_to == "Charge types" and not any(r.charge_type for r in self.charge_rows):
			frappe.throw(_("List the charge types this tax applies to."))
		for r in self.on_taxes:
			t = frappe.db.get_value("Tax", r.tax, ["society", "calculation_order"], as_dict=True)
			if r.tax == self.name or not t or t.society != self.society:
				frappe.throw(_("Row {0}: choose another tax of this society.").format(r.idx))
			if cint(t.calculation_order) >= cint(self.calculation_order):
				frappe.throw(_("Row {0}: {1} must be calculated before this tax (a lower calculation order).").format(r.idx, r.tax))
		both = {c.consumer_category for c in self.categories} & {c.consumer_category for c in self.exempt_categories}
		if both:
			frappe.throw(_("A category can't be both included and exempt: {0}.").format(", ".join(sorted(both))))
		self.tax_title = f"{self.tax_name} {flt(self.rate):g}%" + (" (included)" if cint(self.inclusive) else "")
