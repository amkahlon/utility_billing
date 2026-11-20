// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Due Date Extension", {
	setup(frm) {
		frm.set_query("reason_code", () => ({ filters: { applies_to: "Due Date Extension", enabled: 1 } }));
	},

	refresh(frm) {
		if (frm.is_new()) {
			frm.dashboard.set_headline(__("The latest bill is reissued as a revised bill with the new due date; the surcharge follows the new date."));
		}
		if (frm.doc.revised_bill) {
			frm.add_custom_button(__("Revised Bill"), () => frappe.set_route("Form", "Utility Bill", frm.doc.revised_bill));
		}
	},
});
