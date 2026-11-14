// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

const BILL_COLORS = { Draft: "orange", Posted: "blue", Cancelled: "red" };

frappe.ui.form.on("Utility Bill", {
	refresh(frm) {
		// Show the bill's own status (Draft / Posted / Cancelled) instead of Frappe's "Submitted".
		frm.page.set_indicator(__(frm.doc.status), BILL_COLORS[frm.doc.status] || "gray");
		if (frm.doc.bill_run) {
			frm.add_custom_button(__("Bill Run"), () => frappe.set_route("Form", "Bill Run", frm.doc.bill_run));
		}
	},
});
