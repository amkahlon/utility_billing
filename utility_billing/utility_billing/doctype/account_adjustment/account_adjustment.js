// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Account Adjustment", {
	setup(frm) {
		frm.set_query("reason_code", () => ({ filters: { applies_to: "Waiver / Adjustment", enabled: 1 } }));
		frm.set_query("against_bill", () => ({ filters: { connection: frm.doc.connection, docstatus: 1 } }));
	},

	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		show_balances(frm);
	},

	connection(frm) {
		frm.set_value("against_bill", null);
		show_balances(frm);
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});

function show_balances(frm) {
	if (!frm.doc.connection || frm.doc.docstatus !== 0) return;
	frappe
		.call({ method: "utility_billing.utility_billing.account.account_summary", args: { connection: frm.doc.connection } })
		.then(({ message: s }) => {
			const b = s.balances;
			frm.dashboard.set_headline_alert(
				__("Account balances now: current bill {0}, arrears {1}, surcharge {2}, fees {3}, credit {4}", [
					format_currency(b.current),
					format_currency(b.arrears),
					format_currency(b.surcharge),
					format_currency(b.fees),
					format_currency(b.credit),
				]),
				"blue"
			);
		});
}
