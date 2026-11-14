// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Estimated Bills"] = {
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
  "fieldname": "repeat_only",
  "label": "Only 2 or More Months Running",
  "fieldtype": "Check"
 }
],
};
