# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Revenue Target: the society's billing and collection targets month by month, for revenue against target (FS-RPT-38)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from utility_billing.utility_billing.readings import month_start


class RevenueTarget(Document):
	def validate(self):
		seen = set()
		for r in self.months:
			r.month = month_start(r.month)
			if getdate(r.month).year != int(self.target_year):
				frappe.throw(_("Row {0}: {1} isn't in {2}.").format(r.idx, getdate(r.month).strftime("%B %Y"), self.target_year))
			key = (str(r.month), r.utility or "")
			if key in seen:
				frappe.throw(_("Row {0}: {1} is entered twice.").format(r.idx, getdate(r.month).strftime("%B %Y")))
			seen.add(key)
			if flt(r.billed) < 0 or flt(r.collected) < 0:
				frappe.throw(_("Row {0}: a target can't be negative.").format(r.idx))
		self.total_billed = sum(flt(r.billed) for r in self.months)
		self.total_collected = sum(flt(r.collected) for r in self.months)
		self.target_title = f"{self.society} {self.target_year} target"
