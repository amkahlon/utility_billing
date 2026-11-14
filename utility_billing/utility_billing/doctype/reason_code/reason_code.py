# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Reason Code: why an adjustment, waiver, reversal, revised bill, disconnection or write-off was
made (FS-CFG-26). Required wherever money is reversed or waived."""

import re

import frappe
from frappe import _
from frappe.model.document import Document


class ReasonCode(Document):
	def before_insert(self):
		self.code = (self.code or "").strip().upper()

	def validate(self):
		if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,19}", self.code or ""):
			frappe.throw(_("Code: 2 to 20 letters, digits or hyphens, for example CHQ-BOUNCE."))
