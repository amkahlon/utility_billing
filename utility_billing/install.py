# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Default records created when the app is installed on a site."""

import frappe

# name, unit of measure, reference code (first digit of consumer reference numbers)
UTILITIES = [  # name, unit, reference code, metered
	("Electricity", "kWh", 1, 1),
	("Water", "m³", 2, 1),
	("Gas", "m³", 3, 1),
	("Maintenance", "month", 4, 0),  # billed without a meter: fixed, per area or by plot size (increment 22)
]


# Why a meter couldn't be read (FS-RDG-06): code, description, billing treatment
EXCEPTION_CODES = [
	("Defective", "Meter is faulty or not recording", "Defective meter"),  # FS-WF-08 (increment 22)
	("Burnt", "Meter is burnt", "Defective meter"),
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
	("CHANNEL-REV", "Reversed by the payment channel (bank, 1Link, Kuickpay) the same day", "Payment Reversal"),
	("FEE-ERROR", "Fee charged in error", "Fee Reversal"),
	("NON-PAY", "Disconnected for non-payment", "Disconnection"),
	("CONS-REQ", "Disconnected at the consumer's request", "Disconnection"),
	("SAFETY", "Disconnected for safety", "Disconnection"),
	("IRRECOV", "Irrecoverable after recovery steps", "Write-off"),
	("REFUND-CLOSE", "Credit refunded on closing the account", "Refund"),
	("REFUND-EXCESS", "Excess payment refunded", "Refund"),
	("READ-WRONG", "Meter reading was wrong", "Revised Bill"),
	("CAT-WRONG", "Wrong consumer category", "Revised Bill"),
	("EXT-HARDSHIP", "Due date extended: hardship", "Due Date Extension"),
	("EXT-BANK-DELAY", "Due date extended: bank or channel delay", "Due Date Extension"),
	("OUTAGE-COMP", "Compensation for a long outage", "Bulk Adjustment"),
	("BULK-CORRECTION", "Group correction approved by management", "Bulk Adjustment"),
	("DISPUTE", "Bill disputed by the consumer", "Bill Dispute"),
	("METER-TEST", "Meter found fast or slow when tested", "Waiver / Adjustment"),
	("DETECTION", "Detection bill: theft or tampering assessed", "Waiver / Adjustment"),
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


# KPI dictionary (FS-RPT-45): code, name, area, what it means, formula, data source, refresh, split by, drill-down, report
KPIS = [
	("BILLED", "Billed", "Billing", "Current bills issued for a billing month, before arrears and surcharge.",
	 "Sum of Current Bill of posted utility bills", "Utility Bill", "Live", "Month, category, block, charge type",
	 "Management Dashboard → Billing Summary by Cycle → Bill Register", "Billing Summary by Cycle"),
	("PAYABLE", "Payable within due date", "Billing", "What the bills issued for a month ask consumers to pay by the due date (current bill plus arrears).",
	 "Sum of Payable Within Due Date of posted utility bills", "Utility Bill", "Live", "Month, category, block",
	 "Bill Register", "Bill Register"),
	("COLLECTED", "Collected", "Collections", "Money received in a period, on any bill, at the counter or through a bank or channel. Reversed receipts are not counted.",
	 "Sum of Amount of posted payment receipts, by payment date", "Payment Receipt", "Live", "Day, counter, cashier, payment mode, channel, category, block",
	 "Collections → Cashier Closing", "Collections"),
	("COLLECTION_RATE", "Collection rate", "Collections", "How much of what was asked for in a month was received in that month. Above 100% means arrears were recovered.",
	 "Collected in the month ÷ Payable within due date of the bills issued that month × 100", "Payment Receipt, Utility Bill", "Live",
	 "Month, category, block", "Management Dashboard → Collection vs Billing by Category and Block", "Collection vs Billing by Category and Block"),
	("OWED", "Total owed", "Receivables", "What all consumers owe now: current bills, arrears, surcharge and fees, accounts in credit left out.",
	 "Sum of the balance of every account whose balance is above zero", "Customer Ledger Entry (latest entry of each account)", "Live",
	 "Block, category", "Arrears Ageing → Defaulters", "Arrears Ageing"),
	("ARREARS", "Arrears, surcharge and fees", "Receivables", "What is owed beyond the current bill.",
	 "Sum of the arrears, surcharge and fees balances of all accounts", "Customer Ledger Entry", "Live", "Block, category",
	 "Arrears Ageing", "Arrears Ageing"),
	("ACCOUNTS_IN_ARREARS", "Accounts in arrears", "Receivables", "Number of accounts that owe money from before their current bill.",
	 "Count of accounts with an arrears balance above zero", "Customer Ledger Entry", "Live", "Block, category", "Defaulters", "Defaulters"),
	("CREDIT_HELD", "Credit held", "Receivables", "Money consumers have paid in advance or in excess, to be used by their next bills.",
	 "Sum of the credit balance of all accounts", "Customer Ledger Entry", "Live", "Block, category", "Credit Balances and Advance Payments",
	 "Credit Balances and Advance Payments"),
	("AGEING", "Owed by age", "Receivables", "What is owed, grouped by how many days past the due date it is: 0-30, 31-60, 61-90, 91-180, over 180.",
	 "Balance of each account allocated to its newest charges (payments clear the oldest first), aged from the charge's due date or posting date",
	 "Customer Ledger Entry, Utility Bill", "Live", "Block, category", "Arrears Ageing → Defaulters", "Arrears Ageing"),
	("AVG_RATE", "Average rate per unit", "Billing", "What consumers paid per unit of energy on average.",
	 "Energy charge ÷ units billed", "Utility Bill, Bill Line", "Live", "Category, tariff", "Sales by Category and Tariff", "Sales by Category and Tariff"),
	("SURCHARGE_NET", "Net surcharge", "Receivables", "Late payment surcharge actually kept: levied less waived and reversed.",
	 "Levied − waived by credit note − reversed because paid in time", "Customer Ledger Entry", "Live", "Month", "Surcharge Register",
	 "Surcharge Register"),
	("READING_COMPLETION", "Reading completion", "Readings", "Share of meters in a reading cycle with an accepted reading.",
	 "Valid, approved or estimated readings ÷ meters in the cycle × 100", "Meter Reading", "Live", "Route, reader", "Reading Completion by Route and Reader → Reading Exceptions",
	 "Reading Completion by Route and Reader"),
	("ESTIMATED_RUNNING", "Consumers estimated 2+ months running", "Readings", "Consumers billed on an estimate for two or more months in a row, who need a real reading.",
	 "Count of connections with consecutive estimated readings ending in the month, 2 or more", "Meter Reading", "Live", "Block", "Estimated Bills",
	 "Estimated Bills"),
	("CASH_DIFFERENCE", "Cash difference", "Control", "Cash counted when a cashier closes the day against the cash expected.",
	 "Cash counted − (opening cash + cash received − cash reversed)", "Cashier Day", "Live", "Counter, cashier", "Cashier Closing", "Cashier Closing"),
	("LINE_LOSS", "Line loss", "Losses", "Units that entered the society (the DISCO's bulk meter) and weren't billed, as a share of those that came in.",
	 "(Input kWh − billed kWh) ÷ input kWh × 100", "DISCO Bill, Utility Bill", "Live", "Month, feeder, transformer", "Line Losses by Feeder and Transformer",
	 "Line Losses by Feeder and Transformer"),
	("BILLING_EFFICIENCY", "Billing efficiency", "Losses", "How much of the energy that entered the society was billed to consumers.",
	 "Billed kWh ÷ input kWh × 100", "DISCO Bill, Utility Bill", "Live", "Month", "AT&C Losses", "AT&C Losses"),
	("COLLECTION_EFFICIENCY", "Collection efficiency", "Losses", "How much of what a month's bills asked for was collected in that month, never more than 100%: old arrears don't make up for losing this month's revenue.",
	 "Collected in the month ÷ payable within the due date on that month's bills, capped at 100%", "Payment Receipt, Utility Bill", "Live", "Month",
	 "AT&C Losses", "AT&C Losses"),
	("ATC", "AT&C losses", "Losses", "Aggregate technical and commercial losses: energy lost, and revenue not collected on what was billed.",
	 "1 − (billing efficiency × collection efficiency)", "DISCO Bill, Utility Bill, Payment Receipt", "Live", "Month", "Management Dashboard → AT&C Losses → Highest-Loss Feeders and Loss Trend",
	 "AT&C Losses"),
	("TRANSFORMER_LOADING", "Transformer loading", "Losses", "Sanctioned load on a transformer against what it can carry. An upper bound: peak load isn't metered.",
	 "Sanctioned kW of active connections ÷ (kVA rating × 0.9) × 100", "Transformer, Connection", "Live", "Feeder, transformer", "Transformer Loading",
	 "Transformer Loading"),
	("DISCO_MARGIN", "Margin over the DISCO's bill", "Billing", "What the society billed residents for electricity less what the DISCO billed the society.",
	 "Electricity-group bill lines (energy, FPA, QTA, per-unit add-ons) − total payable on the DISCO's bill", "DISCO Bill, Utility Bill", "Live", "Month",
	 "DISCO Bill vs Resident Billing", "DISCO Bill vs Resident Billing"),
	("FPA_RECOVERY", "FPA recovered from residents", "Billing", "How much of the FPA the DISCO charged the society was billed to residents.",
	 "FPA billed to residents ÷ FPA on the DISCO's bill × 100", "DISCO Bill, Utility Bill, Tariff Adjustment", "Live", "Month",
	 "DISCO Bill vs Resident Billing", "DISCO Bill vs Resident Billing"),
	("APPROVAL_WAIT", "Days waiting for approval", "Control", "How long a tariff, adjustment, bill run or credit/debit note has waited for its approver.",
	 "Today − date submitted for approval", "Tariff, Tariff Adjustment, Bill Run, Account Adjustment", "Live", "Transaction type", "Pending Approvals",
	 "Pending Approvals"),
]


def create_default_kpis():
	for code, name, area, definition, formula, source, refresh, dims, drill, report in KPIS:
		if not frappe.db.exists("KPI Definition", code):
			frappe.get_doc({"doctype": "KPI Definition", "kpi_code": code, "kpi_name": name, "area": area, "definition": definition,
			                "formula": formula, "data_source": source, "refresh": refresh, "dimensions": dims, "drill_down": drill,
			                "report": report if frappe.db.exists("Report", report) else None}).insert(ignore_permissions=True)


def after_install():
	ensure_roles_and_workflows()
	create_default_utilities()
	create_default_exception_codes()
	create_default_reason_codes()
	create_default_export_layouts()
	create_default_kpis()
	create_default_flags()


def create_default_flags():
	from utility_billing.utility_billing import flags  # noqa: PLC0415

	flags.create_default_flags()


def after_migrate():
	"""Defaults added by later increments reach sites that were installed before them."""
	create_default_utilities()  # Maintenance, billed without a meter (increment 22)
	create_default_flags()
	create_default_reason_codes()
	from utility_billing.utility_billing import servicereq  # noqa: PLC0415

	for society in frappe.get_all("Society", pluck="name"):
		servicereq.ensure_types(society)  # the standard service request types (FS-WF-00)
	ensure_indexes()
	ensure_roles_and_workflows()


def ensure_roles_and_workflows():
	"""The app's roles and approval workflows, and the starting roles of each action right: created where missing and never
	changed afterwards, so what a society sets on the Role, Workflow and Role Rights screens stays (increment 25). A change to a
	default that must reach existing sites is a patch."""
	import json  # noqa: PLC0415
	import os  # noqa: PLC0415

	folder = os.path.join(os.path.dirname(__file__), "setup_data")
	for row in json.load(open(os.path.join(folder, "roles.json"))):
		if not frappe.db.exists("Role", row["name"]):
			doc = {k: v for k, v in row.items() if k not in ("modified", "docstatus")}
			frappe.get_doc(doc).insert(ignore_permissions=True)
	for row in json.load(open(os.path.join(folder, "workflows.json"))):
		if not frappe.db.exists("Workflow", row["name"]):
			doc = {k: v for k, v in row.items() if k not in ("modified", "docstatus")}
			for table in ("states", "transitions"):
				doc[table] = [{k: v for k, v in r.items() if k not in ("name", "parent", "modified", "creation")} for r in doc.get(table) or []]
			frappe.get_doc(doc).insert(ignore_permissions=True)
	from utility_billing.utility_billing import rights  # noqa: PLC0415

	rights.ensure_defaults()


# Database indexes for the lookups made per account (increment 23, FS-RUN-07 and FS-NFR-02): without them every bill, ledger and reading
# lookup reads the whole table, which grows by tens of thousands of rows a month.
INDEXES = (
	("Utility Bill", ("connection", "billing_month")), ("Utility Bill", ("bill_run",)), ("Utility Bill", ("society", "billing_month")),
	("Customer Ledger Entry", ("connection", "seq")), ("Customer Ledger Entry", ("society", "posted_on")),
	("Customer Ledger Entry", ("voucher_no",)),
	("Meter Reading", ("reading_cycle", "connection")), ("Meter Reading", ("connection", "billing_month")),
	("Payment Receipt", ("connection", "payment_date")), ("Payment Receipt", ("billing_account",)),
	("Fee Charge", ("connection",)), ("Security Deposit", ("connection",)), ("Service Request", ("connection",)),
	("Installment Plan", ("connection",)), ("Former Occupant Balance", ("connection",)), ("Message Log", ("connection",)),
	("Account Adjustment", ("connection",)), ("Check Meter Reading", ("meter", "billing_month")),
	("Unique Key", ("key_group", "key_value")), ("Unique Key", ("ref_doctype", "ref_name")),
)


def ensure_indexes():
	for doctype, fields in INDEXES:
		meta = frappe.get_meta(doctype)
		if all(meta.has_field(f) for f in fields):
			frappe.db.add_index(doctype, list(fields), index_name="ub_" + "_".join(fields))


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
	for name, uom, code, metered in UTILITIES:
		if not frappe.db.exists("Utility Type", name):
			frappe.get_doc(
				{
					"doctype": "Utility Type",
					"utility_name": name,
					"unit_of_measure": uom,
					"is_metered": metered,
					"reference_code": code,
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.get_value("Utility Type", name, "reference_code"):
			frappe.db.set_value("Utility Type", name, "reference_code", code)
