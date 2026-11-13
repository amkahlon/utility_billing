# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Transformer: distribution transformer on a feeder (FS-MST-08)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class Transformer(Document):
	def validate(self):
		feeder_society = frappe.db.get_value("Feeder", self.feeder, "society")
		if feeder_society and feeder_society != self.society:
			frappe.throw(_("Feeder {0} belongs to society {1}.").format(self.feeder, feeder_society))
		if flt(self.kva_rating) < 0:
			frappe.throw(_("Rating can't be negative."))
