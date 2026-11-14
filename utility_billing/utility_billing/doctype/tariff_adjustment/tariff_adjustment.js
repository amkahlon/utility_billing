// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Tariff Adjustment", {
	setup(frm) {
		frm.set_query("consumer_category", "exempt_categories", () => ({ filters: { utility: frm.doc.utility } }));
	},
	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		utility_billing.add_impact_button(frm, "adjustment_impact");
	},
	billing_month(frm) {
		// Default reference month: 2 months before the billing month (the DISCO's lag).
		if (frm.doc.billing_month && !frm.doc.reference_month) {
			const d = frappe.datetime.str_to_obj(frm.doc.billing_month);
			frm.set_value("reference_month", frappe.datetime.obj_to_str(new Date(d.getFullYear(), d.getMonth() - 2, 1)));
		}
	},
	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
