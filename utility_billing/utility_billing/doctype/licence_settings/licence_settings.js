// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
frappe.ui.form.on("Licence Settings", {
	refresh(frm) {
		if (frm.doc.mode !== "A society's site" || !frm.doc.control_url) return;
		frm.add_custom_button(__("Send Last Month's Usage Now"), () =>
			frappe.xcall("utility_billing.utility_billing.licensing.send_now").then((r) => {
				frappe.show_alert({ message: r.accepted ? __("Accepted by the control site.") : __("Not accepted: see Last Error."), indicator: r.accepted ? "green" : "red" });
				frm.reload_doc();
			}));
	},
});
