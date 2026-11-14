# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Common Area Facility: streetlights, parks, lifts, pumps and the like, with the meter that measures them and how their cost is
shared (FS-MST-25). See network.allocation."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import network


class CommonAreaFacility(Document):
	def validate(self):
		network.check_society(self, "Connection", self.connection, "Account")
		self.meter = frappe.db.get_value("Connection", self.connection, "current_meter") if self.connection else None
		if self.allocation_method != network.CATEGORY:
			self.allocation_category = None
		elif not self.allocation_category:
			frappe.throw(_("Choose the category that pays."))
		if self.connection:
			other = frappe.db.get_value("Common Area Facility", {"connection": self.connection, "name": ["!=", self.name or ""]})
			if other:
				frappe.throw(_("Account {0} already carries the meter of {1}.").format(self.connection, other))
		network.check_location(self)
