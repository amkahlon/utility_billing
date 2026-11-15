"""The desk sidebar is now "Utility Management System" (user, 2026-11-15). The sync brings the new one; the old "Utility Billing" record goes."""
import frappe


def execute():
	if frappe.db.exists("Sidebar", "Utility Management System") and frappe.db.exists("Sidebar", "Utility Billing"):
		frappe.db.delete("Sidebar Item", {"parent": "Utility Billing", "parenttype": "Sidebar"})
		frappe.db.delete("Sidebar", {"name": "Utility Billing"})
