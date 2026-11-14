// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Service Requests and Service Standards"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "from_date",
  "label": "Requested From",
  "fieldtype": "Date",
  "default": frappe.datetime.month_start()
 },
 {
  "fieldname": "to_date",
  "label": "Requested To",
  "fieldtype": "Date",
  "default": frappe.datetime.get_today()
 }
],
};
