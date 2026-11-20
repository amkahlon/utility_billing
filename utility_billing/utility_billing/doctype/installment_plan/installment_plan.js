// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Installment Plan", {
	setup(frm) {
		if (frm.is_new() && !frm.doc.first_month) {
			frm.set_value("first_month", frappe.datetime.add_months(frappe.datetime.month_start(), 1));
		}
	},

	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.doc.revised_bill) {
			frm.add_custom_button(__("Revised Bill"), () => frappe.set_route("Form", "Utility Bill", frm.doc.revised_bill));
		}
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.dashboard.set_headline_alert(
				__("The account owes {0} in arrears, surcharge and fees. Down payment {1}, then {2} installments of {3}. Save to refresh after changing the terms.", [
					format_currency(frm.doc.owed_now), format_currency(frm.doc.down_payment), frm.doc.installments, format_currency(frm.doc.installment_amount),
				]),
				"blue"
			);
		}
		if (frm.doc.docstatus === 1) {
			const s = frm.doc.plan_status;
			frm.dashboard.set_headline_alert(
				s === "Active" ? __("Active: each bill adds one installment; a missed one cancels the plan and the full arrears fall due.") : __("Plan status: {0}", [__(s)]),
				s === "Active" ? "green" : s === "Defaulted" ? "red" : "gray"
			);
		}
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
