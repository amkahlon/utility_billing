// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Bill Delivery Status"] = {
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
  "label": "Issued From",
  "fieldtype": "Date",
  "default": frappe.datetime.month_start()
 },
 {
  "fieldname": "to_date",
  "label": "Issued To",
  "fieldtype": "Date",
  "default": frappe.datetime.get_today()
 },
 {
  "fieldname": "status",
  "label": "Delivery Status",
  "fieldtype": "Select",
  "options": "\nNot recorded\nPrinted\nHanded to Distributor\nDelivered\nSent by Message\nReturned\nNot Delivered"
 }
],
};
