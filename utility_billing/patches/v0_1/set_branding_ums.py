"""The product is called Utility Management System (user's decision, 2026-11-15): the sign-in page, the browser title and the
two-factor issuer say so (the app title in hooks.py names it in the app list and the desk). The app's technical name (utility_billing),
the module and the desk's sidebar record (named by its title, which is part of every desk address) stay. Safe to run again."""
import frappe

NAME = "Utility Management System"


def execute():
	ws = frappe.get_single("Website Settings")
	if ws.app_name != NAME:
		ws.app_name = NAME
		ws.flags.ignore_validate = True
		ws.save(ignore_permissions=True)
	if frappe.db.get_single_value("System Settings", "otp_issuer_name") == "Utility Billing":
		frappe.db.set_single_value("System Settings", "otp_issuer_name", NAME)
