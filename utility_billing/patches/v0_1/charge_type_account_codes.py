# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 8: a charge type's account code became a link to the chart of accounts. Codes typed in
before then become Ledger Accounts (type Income), so no mapping is lost."""

import frappe


def execute():
	for ct in frappe.get_all("Charge Type", filters={"account_code": ["is", "set"]}, fields=["name", "account_code", "charge_name"]):
		code = ct.account_code.strip()
		if not frappe.db.exists("Ledger Account", code):
			frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": ct.charge_name or ct.name,
			                "account_type": "Income"}).insert(ignore_permissions=True)
