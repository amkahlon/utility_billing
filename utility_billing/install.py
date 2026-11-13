# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Default records created when the app is installed on a site."""

import frappe

UTILITIES = [
	("Electricity", "kWh"),
	("Water", "m³"),
	("Gas", "m³"),
]


def after_install():
	create_default_utilities()


def create_default_utilities():
	for name, uom in UTILITIES:
		if not frappe.db.exists("Utility Type", name):
			frappe.get_doc(
				{"doctype": "Utility Type", "utility_name": name, "unit_of_measure": uom, "is_metered": 1}
			).insert(ignore_permissions=True)
