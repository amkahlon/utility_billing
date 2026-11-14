// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Write Off", {
	setup(frm) {
		frm.set_query("reason_code", () => ({ filters: { applies_to: "Write-off", enabled: 1 } }));
	},

	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.dashboard.set_headline_alert(
				__("The account owes {0} in arrears, surcharge and fees. Writing off {1} needs the approval level the society set for that amount.", [
					format_currency(frm.doc.owed_now), format_currency(frm.doc.amount),
				]),
				"orange"
			);
		}
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
