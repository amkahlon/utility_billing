# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Recovery Target: the monthly amount of arrears, surcharge and fees the society aims to recover (FS-REC-07)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from utility_billing.utility_billing.readings import month_start


class RecoveryTarget(Document):
	def validate(self):
		self.month = month_start(self.month)
		if flt(self.target_amount) <= 0:
			frappe.throw(_("The target must be more than zero."))
		other = frappe.db.get_value("Recovery Target", {"society": self.society, "month": self.month, "name": ["!=", self.name]})
		if other:
			frappe.throw(_("{0} already sets the target for {1}.").format(other, getdate(self.month).strftime("%B %Y")))
		self.target_title = f"{self.society} {getdate(self.month).strftime('%b %Y')} recovery target"
