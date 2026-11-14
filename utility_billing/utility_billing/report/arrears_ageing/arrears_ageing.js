// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Arrears Ageing"] = {
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
