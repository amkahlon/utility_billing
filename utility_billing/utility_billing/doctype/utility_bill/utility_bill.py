# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Utility Bill: one connection's bill for one month. Created, posted and cancelled only by its
bill run; a posted bill is never edited (FS-BIL-10) — corrections come as revised bills or
adjustments (later increments)."""

import frappe
from frappe import _
from frappe.model.document import Document


class UtilityBill(Document):
	def validate(self):
		if not self.flags.from_bill_run:
			frappe.throw(_("Bills are created and changed only by a bill run."))

	def on_submit(self):
		self.db_set("status", "Posted")

	def before_cancel(self):
		# Checked before Frappe marks the bill cancelled, so a refused cancel changes nothing.
		if not self.flags.from_bill_run:
			frappe.throw(_("Bills are cancelled through their bill run."))

	def on_cancel(self):
		# A bill replaced by a revised bill is Superseded, and stays visible beside its replacement (FS-BIL-11)
		self.db_set({"status": "Superseded" if self.flags.superseding else "Cancelled", "outstanding_amount": 0})
