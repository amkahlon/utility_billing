// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Bill Register"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "bill_run",
  "label": "Bill Run",
  "fieldtype": "Link",
  "options": "Bill Run"
 },
 {
  "fieldname": "from_date",
  "label": "From Month",
  "fieldtype": "Date",
  "default": frappe.datetime.month_start()
 },
 {
  "fieldname": "to_date",
  "label": "To Month",
  "fieldtype": "Date",
  "default": frappe.datetime.month_start()
 },
 {
  "fieldname": "show_lines",
  "label": "Show Charge Lines",
  "fieldtype": "Check"
 }
],
};
