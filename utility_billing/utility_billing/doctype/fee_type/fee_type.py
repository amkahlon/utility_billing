# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Fee Type: one line of a society's fee schedule (FS-CFG-24), for example Duplicate Bill or Reconnection."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class FeeType(Document):
	def before_insert(self):
		self.fee_name = (self.fee_name or "").strip()

	def validate(self):
		self.amount = flt(self.amount)
		if self.amount <= 0:
			frappe.throw(_("Enter the fee amount."))
