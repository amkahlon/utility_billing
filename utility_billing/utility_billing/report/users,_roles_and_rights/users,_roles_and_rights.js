// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Users, Roles and Rights"] = {
	filters: [
 {
  "fieldname": "user",
  "label": "User (email)",
  "fieldtype": "Data"
 },
 {
  "fieldname": "role",
  "label": "Role",
  "fieldtype": "Select",
  "options": "\nSociety Admin\nBilling Manager\nGeneral Manager\nBilling Officer\nMeter Reader\nCashier\nRecovery Officer\nAccounts Officer\nManagement\nBilling Auditor\nSystem Manager"
 },
 {
  "fieldname": "doctype",
  "label": "Screen (name)",
  "fieldtype": "Data"
 }
],
};
