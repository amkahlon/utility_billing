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

	// the pool worked out as the figures are typed (the server works it out again on save)
	residents_import_units: (frm) => figures(frm),
	disco_import_units: (frm) => figures(frm),
	residents_export_units: (frm) => figures(frm),
	disco_export_units: (frm) => figures(frm),
});

function figures(frm) {
	const d = frm.doc;
	const pool = Math.max((d.residents_import_units || 0) - (d.disco_import_units || 0), 0);
	const inside = (d.residents_export_units || 0) - (d.disco_export_units || 0);
	frm.set_value({
		pool_units: pool,
		solar_used_inside: inside,
		losses_units: inside - pool,
		credit_factor: d.residents_export_units > 0 ? Math.round((pool / d.residents_export_units) * 1e6) / 1e6 : 0,
	});
}
