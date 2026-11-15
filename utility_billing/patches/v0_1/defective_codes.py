"""Defective and Burnt readings are billed by the society's defective-meter method (FS-WF-08, increment 22). Codes still on the old
default "Estimate" move to "Defective meter"; a code a society changed itself is left alone."""
import frappe


def execute():
	frappe.reload_doc("utility_billing", "doctype", "reading_exception_code")
	for code in ("Defective", "Burnt"):
		if frappe.db.get_value("Reading Exception Code", code, "billing_treatment") == "Estimate":
			frappe.db.set_value("Reading Exception Code", code, "billing_treatment", "Defective meter")
