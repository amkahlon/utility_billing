// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// The Role form opens the app's Role Rights screen for this role (increment 25): which actions the role may do.
frappe.ui.form.on("Role", {
	refresh(frm) {
		if (frm.is_new() || !(utility_billing.can("rights.manage") || utility_billing.can("users.manage"))) return;
		frm.add_custom_button(__("Action Rights of this Role"), () => frappe.set_route("ub-rights", { role: frm.doc.name }));
	},
});
