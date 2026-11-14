// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Consumption Analytics"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "month",
  "label": "Month",
  "fieldtype": "Date",
  "default": frappe.datetime.month_start()
 },
 {
  "fieldname": "show",
  "label": "Show",
  "fieldtype": "Select",
  "options": "Theft Suspicion\nSeasonality\nRevenue Forecast",
  "default": "Theft Suspicion"
 }
],
};
