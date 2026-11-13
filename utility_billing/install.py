# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Default records created when the app is installed on a site."""

import frappe

# name, unit of measure, reference code (first digit of consumer reference numbers)
UTILITIES = [
	("Electricity", "kWh", 1),
	("Water", "m³", 2),
	("Gas", "m³", 3),
]


def after_install():
	create_default_utilities()


def create_default_utilities():
	for name, uom, code in UTILITIES:
		if not frappe.db.exists("Utility Type", name):
			frappe.get_doc(
				{
					"doctype": "Utility Type",
					"utility_name": name,
					"unit_of_measure": uom,
					"is_metered": 1,
					"reference_code": code,
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.get_value("Utility Type", name, "reference_code"):
			frappe.db.set_value("Utility Type", name, "reference_code", code)
