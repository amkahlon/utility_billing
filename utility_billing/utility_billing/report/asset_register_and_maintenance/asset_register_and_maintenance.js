// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.

frappe.query_reports["Asset Register and Maintenance"] = {
	filters: [
 {
  "fieldname": "society",
  "label": "Society",
  "fieldtype": "Link",
  "options": "Society"
 },
 {
  "fieldname": "asset_type",
  "label": "Kind",
  "fieldtype": "Select",
  "options": "\nMeter\nTransformer\nFeeder\nGrid Station\nLine Section\nPole\nCable\nPump\nStreetlight\nGenerator\nOther"
 },
 {
  "fieldname": "status",
  "label": "Status",
  "fieldtype": "Select",
  "options": "\nIn store\nIn service\nUnder repair\nRetired"
 }
],
};
