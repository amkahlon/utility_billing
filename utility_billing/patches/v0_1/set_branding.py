"""Sets the site's name and logo so the sign-in page and the browser show the product, not 'Frappe'. Safe to run again."""
import frappe


def execute():
	ws = frappe.get_single("Website Settings")
	changed = False
	for field, value in (("app_name", "Utility Management System"), ("app_logo", "/assets/utility_billing/images/utility-billing-logo.svg"),
	                     ("brand_html", None), ("copyright", "SoftVerx")):
		if value is not None and ws.get(field) != value:
			ws.set(field, value)
			changed = True
	if changed:
		ws.flags.ignore_validate = True
		ws.save(ignore_permissions=True)
