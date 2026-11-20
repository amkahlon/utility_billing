// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Held Amount (increment 35): Release (Billing Manager, General Manager) or Write Off (General Manager) once the court decides.

frappe.ui.form.on("Held Amount", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Held") return;
		const ask = (title, primary, method, note) =>
			frappe.prompt(
				[{ fieldname: "remarks", fieldtype: "Small Text", label: __("Decision and Reason"), reqd: 1, description: note }],
				(v) =>
					frm.call(method, { remarks: v.remarks }).then(() => {
						frm.reload_doc();
						frappe.show_alert({ message: __("Done"), indicator: "green" });
					}),
				title,
				primary
			);
		if (frappe.user.has_role(["Billing Manager", "General Manager", "System Manager"])) {
			frm.add_custom_button(__("Release"), () =>
				ask(__("Release the held amount"), __("Release"), "release",
					__("The amount moves into the account's arrears and is billed with the next bill."))
			);
		}
		if (frappe.user.has_role(["General Manager", "System Manager"])) {
			frm.add_custom_button(__("Write Off"), () =>
				ask(__("Write off the held amount"), __("Write Off"), "write_off",
					__("The amount is written off to the society's write-off expense account. It can't be undone."))
			);
		}
	},
});
