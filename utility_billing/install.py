# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Default records created when the app is installed on a site."""

import frappe

# name, unit of measure, reference code (first digit of consumer reference numbers)
UTILITIES = [
	("Electricity", "kWh", 1),
	("Water", "m³", 2),
	("Gas", "m³", 3),
]


# Why a meter couldn't be read (FS-RDG-06): code, description, billing treatment
EXCEPTION_CODES = [
	("Defective", "Meter is faulty or not recording", "Estimate"),
	("Burnt", "Meter is burnt", "Estimate"),
	("Stuck", "Dial or register is stuck", "Estimate"),
	("Display Off", "Display is blank or unreadable", "Estimate"),
	("Reversed", "Meter runs backwards", "Estimate"),
	("Tampered", "Signs of tampering; refer to revenue protection", "Estimate"),
	("No Access", "Reader couldn't reach the meter", "Estimate"),
	("Locked", "Premises or meter box locked", "Estimate"),
	("Meter Missing", "No meter found at the premises", "Estimate"),
	("Vacant", "Premises vacant and supply unused", "Bill zero"),
	("Other", "Other reason; explain in the review note", "Estimate"),
]


# Why money was reversed or waived (FS-CFG-26): code, description, used for
REASON_CODES = [
	("SUR-WAIVE", "Surcharge waived by management", "Waiver / Adjustment"),
	("BILL-ERROR", "Billing error corrected", "Waiver / Adjustment"),
	("READ-ERROR", "Wrong meter reading corrected", "Waiver / Adjustment"),
	("GOODWILL", "Goodwill adjustment approved by management", "Waiver / Adjustment"),
	("CHQ-BOUNCE", "Cheque or pay order dishonoured", "Payment Reversal"),
	("WRONG-ACC", "Payment entered on the wrong account", "Payment Reversal"),
	("WRONG-AMT", "Payment entered with the wrong amount", "Payment Reversal"),
	("DUP-PAY", "Payment entered twice", "Payment Reversal"),
	("FEE-ERROR", "Fee charged in error", "Fee Reversal"),
	("NON-PAY", "Disconnected for non-payment", "Disconnection"),
	("CONS-REQ", "Disconnected at the consumer's request", "Disconnection"),
	("SAFETY", "Disconnected for safety", "Disconnection"),
	("IRRECOV", "Irrecoverable after recovery steps", "Write-off"),
]


# Journal export layouts (FS-GL-03): name, format, date format, columns, notes. The accounting
# software's import screen can usually map columns, but check them with the society's accountant.
EXPORT_LAYOUTS = [
	("Standard CSV", "CSV", "dd-mm-yyyy",
	 [("Journal No", "Journal No"), ("Date", "Date"), ("Account Code", "Account Code"), ("Account Name", "Account Name"),
	  ("Debit", "Debit"), ("Credit", "Credit"), ("Narration", "Narration")],
	 "One row per journal line; debit and credit in separate columns."),
	("Standard Excel", "Excel", "dd-mm-yyyy",
	 [("Journal No", "Journal No"), ("Date", "Date"), ("Account Code", "Account Code"), ("Account Name", "Account Name"),
	  ("Debit", "Debit"), ("Credit", "Credit"), ("Narration", "Narration")],
	 "Same as Standard CSV, as an Excel file."),
	("QuickBooks journal (CSV)", "CSV", "mm/dd/yyyy",
	 [("Journal No", "Journal No"), ("Journal Date", "Date"), ("Account", "Account Name"), ("Debits", "Debit"),
	  ("Credits", "Credit"), ("Description", "Narration")],
	 "For QuickBooks' journal entry import. QuickBooks matches accounts by name: the account names here must match its chart of accounts."),
	("Sage 50 / Peachtree general journal (CSV)", "CSV", "mm/dd/yyyy",
	 [("Date", "Date"), ("Reference", "Journal No"), ("Number of Distributions", "Line Count"), ("GL Account", "Account Code"),
	  ("Description", "Narration"), ("Amount", "Amount (debit +, credit -)")],
	 "For Sage 50 (Peachtree) General Journal import: one amount per line, debits positive and credits negative. Check the field order in Sage's import template."),
]


def after_install():
	create_default_utilities()
	create_default_exception_codes()
	create_default_reason_codes()
	create_default_export_layouts()


def create_default_export_layouts():
	for name, file_format, date_format, columns, notes in EXPORT_LAYOUTS:
		if not frappe.db.exists("Journal Export Layout", name):
			frappe.get_doc({"doctype": "Journal Export Layout", "layout_name": name, "file_format": file_format,
			                "date_format": date_format, "include_header": 1, "description": notes,
			                "columns": [{"header": h, "source": s} for h, s in columns]}).insert(ignore_permissions=True)


def create_default_reason_codes():
	for code, description, used_for in REASON_CODES:
		if not frappe.db.exists("Reason Code", code):
			frappe.get_doc({"doctype": "Reason Code", "code": code, "description": description,
			                "applies_to": used_for, "enabled": 1}).insert(ignore_permissions=True)


def create_default_exception_codes():
	for code, description, treatment in EXCEPTION_CODES:
		if not frappe.db.exists("Reading Exception Code", code):
			frappe.get_doc(
				{"doctype": "Reading Exception Code", "code": code, "description": description,
				 "billing_treatment": treatment, "enabled": 1}
			).insert(ignore_permissions=True)


def create_default_utilities():
	for name, uom, code in UTILITIES:
		if not frappe.db.exists("Utility Type", name):
			frappe.get_doc(
				{
					"doctype": "Utility Type",
					"utility_name": name,
					"unit_of_measure": uom,
					"is_metered": 1,
					"reference_code": code,
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.get_value("Utility Type", name, "reference_code"):
			frappe.db.set_value("Utility Type", name, "reference_code", code)
