// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Bill Revision", {
	setup(frm) {
		frm.set_query("original_bill", () => ({ filters: { docstatus: 1, status: "Posted" } }));
		frm.set_query("reason_code", () => ({ filters: { applies_to: "Revised Bill", enabled: 1 } }));
	},

	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			const d = frm.doc.difference || 0;
			frm.dashboard.set_headline_alert(
				__("The current bill changes by {0}: from {1} to {2}. Save to refresh this after changing the correction.", [
					format_currency(d), format_currency(frm.doc.old_current), format_currency(frm.doc.new_current),
				]),
				d === 0 ? "orange" : d > 0 ? "red" : "green"
			);
		}
		if (frm.doc.revised_bill) {
			frm.add_custom_button(__("Revised Bill"), () => frappe.set_route("Form", "Utility Bill", frm.doc.revised_bill));
		}
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
