// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Tariff Adjustment", {
	setup(frm) {
		for (const table of ["exempt_categories", "units_limit_categories"]) {
			frm.set_query("consumer_category", table, () => ({ filters: { utility: frm.doc.utility } }));
		}
		// an FPA or QTA is its own per-unit charge, never the society's energy charge
		frm.set_query("charge_type", () => {
			const filters = { calculation_basis: "Per Unit" };
			if (frm._energy_charge) filters.name = ["!=", frm._energy_charge];
			return { filters };
		});
	},
	society(frm) {
		frm._energy_charge = null;
		if (frm.doc.society) frappe.db.get_value("Society", frm.doc.society, "energy_charge_type").then((r) => (frm._energy_charge = r.message && r.message.energy_charge_type));
	},
	adjustment_type(frm) {
		if (frm.doc.adjustment_type && !frm.doc.charge_type) {
			frappe.db.exists("Charge Type", frm.doc.adjustment_type).then((yes) => yes && frm.set_value("charge_type", frm.doc.adjustment_type));
		}
	},
	refresh(frm) {
		frm.trigger("society");
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
