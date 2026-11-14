// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Refund", {
	setup(frm) {
		frm.set_query("reason_code", () => ({ filters: { applies_to: "Refund", enabled: 1 } }));
	},

	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		show_credit(frm);
	},

	connection(frm) {
		show_credit(frm);
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});

function show_credit(frm) {
	if (!frm.doc.connection || frm.doc.docstatus !== 0) return;
	frappe
		.call({ method: "utility_billing.utility_billing.account.account_summary", args: { connection: frm.doc.connection } })
		.then(({ message: s }) => {
			const credit = s.balances.credit;
			frm.dashboard.set_headline_alert(
				credit > 0 ? __("This account holds {0} of credit that can be refunded.", [format_currency(credit)]) : __("This account holds no credit to refund."),
				credit > 0 ? "blue" : "orange"
			);
		});
}
