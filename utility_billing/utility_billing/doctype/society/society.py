# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Society: one client (housing society, compound or estate) with its profile, branding,
bulk supply and billing parameters (FS-CLT-01..08, FS-CFG-01, 15, 16, 17)."""

import re

import frappe
from frappe import _
from frappe.model.document import Document


class Society(Document):
	def validate(self):
		self.short_name = (self.short_name or "").strip().upper()
		if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,19}", self.short_name):
			frappe.throw(_("Short Name: 2 to 20 letters, digits or hyphens, for example DHA-LHR."))
		if not (self.enable_electricity or self.enable_water or self.enable_gas or self.enable_fixed_charges):
			frappe.throw(_("Turn on at least one module."))
		if (self.days_to_due_date or 0) < 1:
			frappe.throw(_("Days from Issue to Due Date must be at least 1."))
		if (self.grace_days or 0) < 0 or (self.reading_window_days or 0) < 0:
			frappe.throw(_("Grace days and reading window can't be negative."))
		if self.surcharge_basis == "Percentage of current bill" and not 0 <= (self.surcharge_rate or 0) <= 100:
			frappe.throw(_("Surcharge Rate must be between 0 and 100%."))
		if self.surcharge_basis == "Fixed amount" and (self.surcharge_fixed_amount or 0) <= 0:
			frappe.throw(_("Enter the fixed Surcharge Amount."))
		if (self.surcharge_cap or 0) < 0 or (self.minimum_bill or 0) < 0:
			frappe.throw(_("Surcharge cap and minimum bill can't be negative."))
		self.validate_logo()

	def validate_logo(self):
		if self.logo and not re.search(r"\.(png|svg|jpe?g|webp)$", self.logo.split("?")[0], re.I):
			frappe.throw(_("Logo must be a PNG, SVG, JPG or WebP image."))
