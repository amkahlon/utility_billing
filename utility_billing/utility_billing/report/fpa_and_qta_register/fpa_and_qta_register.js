// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["FPA and QTA Register"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "from_date",
  "label": "From Month",
  "fieldtype": "Date",
  "default": frappe.datetime.year_start()
 },
 {
  "fieldname": "to_date",
  "label": "To Month",
  "fieldtype": "Date",
  "default": frappe.datetime.get_today()
 },
 {
  "fieldname": "adjustment_type",
  "label": "Type",
  "fieldtype": "Select",
  "options": "\nFPA\nQTA"
 }
],
};
