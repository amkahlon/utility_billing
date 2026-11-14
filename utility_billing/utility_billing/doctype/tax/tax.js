// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Tax", {
	refresh(frm) {
		utility_billing.add_impact_button(frm, "tax_impact");
		if (frm.is_new()) {
			frm.dashboard.set_headline(__("A society with no tax has none on its bills. Use Impact Preview, after saving, to see what this tax would add before switching it on."));
		}
	},
});
