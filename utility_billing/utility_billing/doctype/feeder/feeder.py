# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Feeder: an 11 kV feeder of the society's network, with its grid station and check meter (FS-MST-08, 23)."""

from frappe.model.document import Document

from utility_billing.utility_billing import network


class Feeder(Document):
	def validate(self):
		network.check_society(self, "Grid Station", self.get("grid_station"), "Grid Station")
		network.check_location(self)
