// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Bill Run", {
	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.is_new()) return;
		frm.add_custom_button(__("Open Bills"), () => frappe.set_route("List", "Utility Bill", { bill_run: frm.doc.name }));
		const editable = frm.doc.docstatus === 0 && ["Draft", "Rejected", "", undefined].includes(frm.doc.workflow_state);
		if (editable) {
			frm.add_custom_button(__("Calculate Bills"), () => {
				const go = () =>
					frappe
						.call({
							method: "utility_billing.utility_billing.billing.calculate_run",
							args: { run: frm.doc.name },
							freeze: true,
							freeze_message: __("Calculating bills..."),
						})
						.then(({ message }) => {
							if (message.background) {
								frappe.show_alert({ message: __("Calculating in the background; refresh in a minute."), indicator: "blue" });
							}
							frm.reload_doc();
						});
				frm.is_dirty() ? frm.save().then(go) : go();
			}).addClass("btn-primary");
		}
		if (frm.doc.calculation_status !== "Calculated" && editable) {
			frm.dashboard.set_headline_alert(__("Bills need to be calculated before submitting for approval."), "orange");
		} else if ((frm.doc.problems || []).length) {
			frm.dashboard.set_headline_alert(
				__("{0} connections couldn't be billed; see Not Billed below.", [frm.doc.problems.length]),
				"orange"
			);
		}
	},
	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
