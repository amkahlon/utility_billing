// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Bulk Adjustment", {
	setup(frm) {
		frm.set_query("reason_code", () => ({ filters: { applies_to: "Bulk Adjustment", enabled: 1 } }));
		frm.set_query("feeder", () => ({ filters: { society: frm.doc.society } }));
		frm.set_query("transformer", () => ({ filters: { society: frm.doc.society } }));
	},

	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.dashboard.set_headline_alert(
				__("{0} accounts would take this adjustment, Rs {1} in all. Save to refresh the list after changing the audience or amount.", [
					frm.doc.accounts || 0, format_currency(frm.doc.total_amount),
				]),
				frm.doc.accounts ? "blue" : "orange"
			);
		}
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
