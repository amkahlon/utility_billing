// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
frappe.ui.form.on("Consumer", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Consumer 360"), () => {
			frappe.route_options = { consumer: frm.doc.name };
			frappe.set_route("ub-consumer-360");
		});
		frm.add_custom_button(__("Combined Statement"), () => {
			frappe.route_options = { consumer: frm.doc.name };
			frappe.set_route("query-report", "Consumer Statement (All Accounts)");
		});
	},
});
