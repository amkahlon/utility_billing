# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 6: societies created before the Bill Layout settings get their defaults
(12 months of history, office copy and QR code on)."""

import frappe


def execute():
	for name in frappe.get_all("Society", filters={"bill_history_months": ["in", [0, None]]}, pluck="name"):
		frappe.db.set_value("Society", name, {"bill_history_months": 12, "print_office_copy": 1, "show_qr_code": 1},
		                    update_modified=False)
