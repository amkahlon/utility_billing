// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Reading Completion by Route and Reader"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "reading_cycle",
  "label": "Reading Cycle",
  "fieldtype": "Link",
  "options": "Reading Cycle"
 },
 {
  "fieldname": "group_by",
  "label": "Group By",
  "fieldtype": "Select",
  "options": "Route\nReader",
  "default": "Route"
 }
],
};
