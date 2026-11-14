// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Defaulters"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "as_of",
  "label": "As Of",
  "fieldtype": "Date",
  "default": frappe.datetime.get_today()
 },
 {
  "fieldname": "min_amount",
  "label": "Owing At Least",
  "fieldtype": "Currency",
  "default": "1"
 },
 {
  "fieldname": "min_days",
  "label": "Overdue At Least (days)",
  "fieldtype": "Int",
  "default": "1"
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
