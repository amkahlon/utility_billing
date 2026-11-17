// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Pending Approvals"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "transaction",
  "label": "Transaction",
  "fieldtype": "Select",
  "options": "\nTariff\nTariff Adjustment\nBill Run\nAccount Adjustment\nRefund\nBill Revision\nBulk Adjustment\nInstallment Plan\nWrite Off\nBill Template"
 },
 {
  "fieldname": "older_than",
  "label": "Waiting More Than (days)",
  "fieldtype": "Int",
  "default": "0"
 }
],
};
