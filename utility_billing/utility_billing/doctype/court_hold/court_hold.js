// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Court Hold", {
	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.doc.docstatus === 0 && !frm.is_new() && frm.doc.amount) {
			frm.dashboard.set_headline_alert(
				frm.doc.bill
					? __("On approval {0} leaves what the account owes, and bill {1} is reissued without it: payable {2}, due date {3} (unchanged). Save to refresh this after changing the amount.", [
						format_currency(frm.doc.amount), frm.doc.bill, format_currency(frm.doc.payable_after), frappe.datetime.str_to_user(frm.doc.new_due_date),
					])
					: __("On approval {0} leaves what the account owes. The account has no posted bill, so no revised bill is issued.", [format_currency(frm.doc.amount)]),
				"orange"
			);
		}
		if (frm.doc.held_amount) {
			frm.add_custom_button(__("Held Amount"), () => frappe.set_route("Form", "Held Amount", frm.doc.held_amount));
		}
		if (frm.doc.revised_bill) {
			frm.add_custom_button(__("Revised Bill"), () => frappe.set_route("Form", "Utility Bill", frm.doc.revised_bill));
		}
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
