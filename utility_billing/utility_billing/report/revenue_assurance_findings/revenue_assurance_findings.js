// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Revenue Assurance Findings"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "from_month",
  "label": "From Month",
  "fieldtype": "Date"
 },
 {
  "fieldname": "to_month",
  "label": "To Month",
  "fieldtype": "Date"
 },
 {
  "fieldname": "view",
  "label": "Show",
  "fieldtype": "Select",
  "options": "By Check\nBy Officer\nOpen Findings",
  "default": "By Check"
 }
],
};
