// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Due Date Extension", {
	setup(frm) {
		frm.set_query("reason_code", () => ({ filters: { applies_to: "Due Date Extension", enabled: 1 } }));
	},

	refresh(frm) {
		if (frm.is_new()) {
			frm.dashboard.set_headline(__("The bill itself is not changed: its extended due date is kept beside it and the surcharge follows the new date."));
		}
	},
});
