// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Former Occupant Balances"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "status",
  "label": "Status",
  "fieldtype": "Select",
  "options": "\nOpen\nSettled",
  "default": "Open"
 }
],
};
