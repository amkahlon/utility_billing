# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Cash Counter: a place where cashiers take payments. Its code starts every receipt number taken
there, so receipts run in one unbroken sequence per counter (FS-PAY-13)."""

import re

import frappe
from frappe import _
from frappe.model.document import Document


class CashCounter(Document):
	def before_insert(self):
		self.counter_code = (self.counter_code or "").strip().upper()

	def validate(self):
		if not re.fullmatch(r"[A-Z0-9]{2,8}", self.counter_code or ""):
			frappe.throw(_("Counter Code: 2 to 8 letters or digits, for example CB1."))
