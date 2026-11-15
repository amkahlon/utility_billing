"""A tax now names the utilities whose bills carry it (user, 2026-11-15). Taxes set up before then were for electricity bills
(the only metered utility billed so far), so they get Electricity."""
import frappe


def execute():
	frappe.reload_doc("utility_billing", "doctype", "tax_utility_row")
	frappe.reload_doc("utility_billing", "doctype", "tax")
	if not frappe.db.exists("Utility Type", "Electricity"):
		return
	for name in frappe.get_all("Tax", pluck="name"):
		if not frappe.db.exists("Tax Utility Row", {"parent": name, "parenttype": "Tax"}):
			frappe.get_doc({"doctype": "Tax Utility Row", "parent": name, "parenttype": "Tax", "parentfield": "utilities", "idx": 1,
			                "utility": "Electricity"}).db_insert()
