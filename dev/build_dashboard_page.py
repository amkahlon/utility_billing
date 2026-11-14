"""Creates the standard Page "ub-dashboard" and puts Dashboard first in the module menu. Run from ~/frappe-bench/sites (dev site)."""
import frappe

frappe.init(site="ub.localhost", sites_path=".")
frappe.connect()
from utility_billing.utility_billing import dashboard

roles = sorted(dashboard.ALLOWED)
if not frappe.db.exists("Page", "ub-dashboard"):
	frappe.get_doc({"doctype": "Page", "page_name": "ub-dashboard", "title": "Dashboard", "module": "Utility Billing",
	                "standard": "Yes", "roles": [{"role": r} for r in roles]}).insert()
	print("page created")
else:
	page = frappe.get_doc("Page", "ub-dashboard")
	page.set("roles", [{"role": r} for r in roles])
	page.save()
	print("page updated")

sb = frappe.get_doc("Sidebar", "Utility Billing")
if not any(i.link_to == "ub-dashboard" for i in sb.items):
	items = list(sb.items)
	sb.items = []
	sb.append("items", {"type": "Link", "label": "Dashboard", "link_type": "Page", "link_to": "ub-dashboard", "child": 0})
	for i in items:
		sb.append("items", i.as_dict())
	sb.save()
	print("sidebar: Dashboard added first")
frappe.db.commit()
frappe.destroy()
