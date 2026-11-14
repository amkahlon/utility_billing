// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Highest-Loss Feeders and Loss Trend"] = {
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
  "fieldname": "group_by",
  "label": "Group By",
  "fieldtype": "Select",
  "options": "Feeder\nTransformer",
  "default": "Feeder"
 },
 {
  "fieldname": "show",
  "label": "Show",
  "fieldtype": "Select",
  "options": "Ranking\n12-Month Trend",
  "default": "Ranking"
 }
],
};
