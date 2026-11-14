// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Consumer Statement (All Accounts)"] = {
	filters: [
 {
  "fieldname": "consumer",
  "label": "Consumer",
  "fieldtype": "Link",
  "options": "Consumer",
  "reqd": 1
 },
 {
  "fieldname": "from_date",
  "label": "From",
  "fieldtype": "Date"
 },
 {
  "fieldname": "to_date",
  "label": "To",
  "fieldtype": "Date",
  "default": frappe.datetime.get_today()
 }
],
};
