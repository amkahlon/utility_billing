# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Grid Station: a grid station or substation of the society's network, with its capacity and incoming supply (FS-MST-23)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import network


class GridStation(Document):
	def validate(self):
		if flt(self.capacity_mva) < 0:
			frappe.throw(_("Capacity can't be negative."))
		network.check_location(self)
