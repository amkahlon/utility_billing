// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Audit Log and User Activity"] = {
	filters: [
 {
  "fieldname": "show",
  "label": "Show",
  "fieldtype": "Select",
  "options": "Changes to Documents\nLogins and Logouts",
  "default": "Changes to Documents"
 },
 {
  "fieldname": "from_date",
  "label": "From",
  "fieldtype": "Date",
  "default": frappe.datetime.month_start()
 },
 {
  "fieldname": "to_date",
  "label": "To",
  "fieldtype": "Date",
  "default": frappe.datetime.get_today()
 },
 {
  "fieldname": "user",
  "label": "User (email)",
  "fieldtype": "Data"
 },
 {
  "fieldname": "doctype",
  "label": "Document Type",
  "fieldtype": "Select",
  "options": "\nSociety\nBill Template\nSolar Rebate Pool\nMigration Batch\nMigration Profile\nTariff\nTariff Adjustment\nBill Run\nAccount Adjustment\nRefund\nBill Revision\nBulk Adjustment\nInstallment Plan\nWrite Off\nDue Date Extension\nBill Dispute\nSettlement File\nSuspense Payment\nNotification Template\nMessaging Provider\nFee Type\nCharge Type\nConsumer Category\nCash Counter\nReason Code\nLedger Account\nJournal Export Layout\nConsumer\nProperty\nConnection\nMeter\nReading Cycle\nMeter Reading\nCashier Day\nKPI Definition"
 }
],
};
