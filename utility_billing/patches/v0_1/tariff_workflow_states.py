# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Give tariffs created before the approval workflow a workflow state that matches their docstatus."""

import frappe

STATE_FOR_DOCSTATUS = {0: "Draft", 1: "Approved", 2: "Cancelled"}


def execute():
	if not frappe.db.has_column("Tariff", "workflow_state"):
		return
	for t in frappe.get_all("Tariff", filters={"workflow_state": ["is", "not set"]}, fields=["name", "docstatus"]):
		frappe.db.set_value(
			"Tariff", t.name, "workflow_state", STATE_FOR_DOCSTATUS[t.docstatus], update_modified=False
		)
