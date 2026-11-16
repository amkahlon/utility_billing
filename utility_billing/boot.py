# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Adds the browser files' version (see ui_version.py) and the user's landing page to the page's boot information."""

import frappe

from utility_billing.ui_version import ui_version


def home_page(roles=None) -> str:
	"""Where the app opens for this user: the dashboard for those who may see it, else the screen their role works in. Meter readers
	and cashiers used to land on the dashboard and get "No permission for Page" (bug report 2026-11-16)."""
	from utility_billing.utility_billing.dashboard import ALLOWED  # noqa: PLC0415

	roles = set(roles if roles is not None else frappe.get_roles())
	if roles & ALLOWED or "System Manager" in roles:
		return "ub-dashboard"
	if "Cashier" in roles:
		return "ub-cash"
	if "Meter Reader" in roles:
		return "ub-readings"
	return "ub-consumer-360" if roles & {"Billing Auditor"} else "List/Connection"


def boot_session(bootinfo):
	bootinfo.ub_ui_version = ui_version()
	bootinfo.ub_home = home_page()
