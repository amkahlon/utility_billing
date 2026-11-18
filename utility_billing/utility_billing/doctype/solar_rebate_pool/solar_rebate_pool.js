// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Solar Rebate Pool", {
	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Get Figures"), () =>
				frappe.call({
					method: "utility_billing.utility_billing.doctype.solar_rebate_pool.solar_rebate_pool.get_figures",
					args: { name: frm.doc.name },
					freeze: true,
					callback: () => frm.reload_doc(),
				})
			);
		}
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
