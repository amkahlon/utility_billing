# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Feeders and transformers had two check-meter fields: the older free-text Check Meter No and the Check Meter link that readings and
line losses use. The text one is now hidden; where it names a meter of the society and the link is empty, the link is filled."""

import frappe


def execute():
	for dt in ("Feeder", "Transformer"):
		if not frappe.db.has_column(dt, "check_meter_no"):
			continue
		for r in frappe.get_all(dt, filters={"check_meter_no": ["is", "set"], "check_meter": ["is", "not set"]}, fields=["name", "check_meter_no", "society"]):
			meter = frappe.db.get_value("Meter", {"name": r.check_meter_no}) or frappe.db.get_value("Meter", {"serial_no": r.check_meter_no})
			if meter:
				frappe.db.set_value(dt, r.name, "check_meter", meter, update_modified=False)
