// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Credit Balances and Advance Payments"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "consumer_category",
  "label": "Category",
  "fieldtype": "Link",
  "options": "Consumer Category"
 },
 {
  "fieldname": "block",
  "label": "Block",
  "fieldtype": "Data"
 }
],
};
