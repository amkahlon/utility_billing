# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Line Section: a stretch of a feeder with its route, length and estimated technical loss (FS-MST-24)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import network


class LineSection(Document):
	def validate(self):
		network.check_society(self, "Feeder", self.feeder, "Feeder")
		if flt(self.length_km) < 0:
			frappe.throw(_("Length can't be negative."))
		if not 0 <= flt(self.estimated_loss_percent) <= 100:
			frappe.throw(_("The estimated loss is a percentage from 0 to 100."))
