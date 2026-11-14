# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 7: bills posted before customer accounts existed get their ledger entries, oldest first,
so every account starts from its full bill history."""

import frappe


def execute():
	from utility_billing.utility_billing import account

	for name in frappe.get_all("Utility Bill", filters={"docstatus": 1, "ledger_entry": ["is", "not set"]},
	                           pluck="name", order_by="billing_month, name"):
		account.post_bill(frappe.get_doc("Utility Bill", name))
