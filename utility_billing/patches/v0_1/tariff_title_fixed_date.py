# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Re-title existing tariffs with the fixed date format ("Residential from 01 Jul 2026")."""

import frappe

from utility_billing.utility_billing.doctype.tariff.tariff import tariff_title


def execute():
	for t in frappe.get_all("Tariff", fields=["name", "consumer_category", "effective_from"]):
		frappe.db.set_value(
			"Tariff", t.name, "tariff_title", tariff_title(t.consumer_category, t.effective_from), update_modified=False
		)
