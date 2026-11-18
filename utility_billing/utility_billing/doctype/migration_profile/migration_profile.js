// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Migration Profile: the column and value map of one society's previous system (increment 29).
frappe.ui.form.on("Migration Profile", {
	refresh(frm) {
		frm.add_custom_button(__("Download Template"), () => window.open("/api/method/utility_billing.utility_billing.doctype.migration_batch.migration_batch.template"));
		if (!frm.is_new()) {
			frm.add_custom_button(__("Fill SAP IS-U Columns"), () =>
				frm.save().then(() => frappe.call({
					method: "utility_billing.utility_billing.doctype.migration_profile.migration_profile.fill_sap_isu",
					args: { name: frm.doc.name },
				}).then((r) => {
					frm.reload_doc();
					frappe.show_alert({ message: __("{0} SAP IS-U columns added", [r.message]), indicator: "green" });
				})));
		}
	},
});
