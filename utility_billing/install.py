# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Default records created when the app is installed on a site."""

import frappe

# name, unit of measure, reference code (first digit of consumer reference numbers)
UTILITIES = [
	("Electricity", "kWh", 1),
	("Water", "m³", 2),
	("Gas", "m³", 3),
]


# Why a meter couldn't be read (FS-RDG-06): code, description, billing treatment
EXCEPTION_CODES = [
	("Defective", "Meter is faulty or not recording", "Estimate"),
	("Burnt", "Meter is burnt", "Estimate"),
	("Stuck", "Dial or register is stuck", "Estimate"),
	("Display Off", "Display is blank or unreadable", "Estimate"),
	("Reversed", "Meter runs backwards", "Estimate"),
	("Tampered", "Signs of tampering; refer to revenue protection", "Estimate"),
	("No Access", "Reader couldn't reach the meter", "Estimate"),
	("Locked", "Premises or meter box locked", "Estimate"),
	("Meter Missing", "No meter found at the premises", "Estimate"),
	("Vacant", "Premises vacant and supply unused", "Bill zero"),
	("Other", "Other reason; explain in the review note", "Estimate"),
]


def after_install():
	create_default_utilities()
	create_default_exception_codes()


def create_default_exception_codes():
	for code, description, treatment in EXCEPTION_CODES:
		if not frappe.db.exists("Reading Exception Code", code):
			frappe.get_doc(
				{"doctype": "Reading Exception Code", "code": code, "description": description,
				 "billing_treatment": treatment, "enabled": 1}
			).insert(ignore_permissions=True)


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
