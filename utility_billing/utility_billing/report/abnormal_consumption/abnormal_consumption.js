// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Abnormal Consumption"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "month",
  "label": "Billing Month",
  "fieldtype": "Date",
  "default": frappe.datetime.month_start()
 },
 {
  "fieldname": "show",
  "label": "Show",
  "fieldtype": "Select",
  "options": "\nUnusually low\nUnusually high"
 }
],
};
