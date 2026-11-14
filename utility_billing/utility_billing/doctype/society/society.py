# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Society: one client (housing society, compound or estate) with its profile, branding,
bulk supply, billing parameters and bill layout (FS-CLT-01..08, FS-CFG-01, 15, 16, 17, 19)."""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class Society(Document):
	def before_insert(self):
		# The ID comes from short_name and is set before validate(), so normalise it here.
		self.normalise_short_name()

	def normalise_short_name(self):
		self.short_name = (self.short_name or "").strip().upper()

	def validate(self):
		# The form can send numbers as text (for example a default of "10"), so convert first.
		self.normalise_short_name()
		self.normalise_website()
		if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,19}", self.short_name):
			frappe.throw(_("Short Name: 2 to 20 letters, digits or hyphens, for example DHA-LHR."))
		if not any(
			cint(v) for v in (self.enable_electricity, self.enable_water, self.enable_gas, self.enable_fixed_charges)
		):
			frappe.throw(_("Turn on at least one module."))
		if cint(self.days_to_due_date) < 1:
			frappe.throw(_("Days from Issue to Due Date must be at least 1."))
		if cint(self.grace_days) < 0 or cint(self.reading_window_days) < 0:
			frappe.throw(_("Grace days and reading window can't be negative."))
		if self.surcharge_basis == "Percentage of current bill" and not 0 <= flt(self.surcharge_rate) <= 100:
			frappe.throw(_("Surcharge Rate must be between 0 and 100%."))
		if self.surcharge_basis == "Fixed amount" and flt(self.surcharge_fixed_amount) <= 0:
			frappe.throw(_("Enter the fixed Surcharge Amount."))
		if flt(self.surcharge_cap) < 0 or flt(self.minimum_bill) < 0:
			frappe.throw(_("Surcharge cap and minimum bill can't be negative."))
		if not 1 <= cint(self.bill_history_months) <= 24:
			frappe.throw(_("Months of History on Bill must be from 1 to 24."))
		self.validate_logo()

	def normalise_website(self):
		# Frappe's URL check needs a scheme; staff usually type "www.example.com".
		site = (self.website or "").strip()
		if site and not re.match(r"^https?://", site, re.I):
			site = "https://" + site
		self.website = site

	def validate_logo(self):
		if self.logo and not re.search(r"\.(png|svg|jpe?g|webp)$", self.logo.split("?")[0], re.I):
			frappe.throw(_("Logo must be a PNG, SVG, JPG or WebP image."))
