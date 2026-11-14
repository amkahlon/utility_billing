# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Helpers for the QA site only (never call these on a client site)."""

import frappe


def mark_setup_complete():
	"""Skip the setup wizard on the QA site so the desk opens directly."""
	if "qa" not in (frappe.local.site or ""):
		frappe.throw("mark_setup_complete is for the QA site only.")
	frappe.db.set_value("Installed Application", {"app_name": ["in", ["frappe", "utility_billing"]]}, "is_setup_complete", 1)
	frappe.db.set_single_value(
		"System Settings",
		{
			"setup_complete": 1,
			"country": "Pakistan",
			"time_zone": "Asia/Karachi",
			"currency": "PKR",
			"date_format": "dd-mm-yyyy",
		},
	)
	# The real setup wizard also stores these as site-wide defaults; formatdate() reads them.
	for key, value in (("date_format", "dd-mm-yyyy"), ("currency", "PKR"), ("country", "Pakistan"),
	                   ("time_zone", "Asia/Karachi")):
		frappe.db.set_default(key, value)
	for user in frappe.get_all("User", filters={"name": ["!=", "Guest"]}, pluck="name"):
		frappe.db.set_value("User", user, "time_zone", "Asia/Karachi", update_modified=False)
	frappe.db.commit()
	print("setup marked complete on", frappe.local.site)
