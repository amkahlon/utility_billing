// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
frappe.ui.form.on("Run Snapshot", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.dashboard.clear_headline();
		if (frm.doc.status === "Restored") {
			frm.dashboard.set_headline_alert(frm.doc.restore_result || __("Restored."), "green");
			return;
		}
		frm.dashboard.set_headline_alert(
			__("Kept just before the bill run was posted. Restoring reverses the run and checks every account is back to its balance."),
			"blue"
		);
		if (utility_billing.can("snapshot.restore")) {
			frm.add_custom_button(__("Restore Run"), () => {
				frappe.prompt(
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Why is the run being restored?"), reqd: 1 }],
					(v) =>
						frappe
							.call({
								method: "utility_billing.utility_billing.snapshots.restore",
								args: { snapshot: frm.doc.name, reason: v.reason },
								freeze: true,
							})
							.then(({ message: r }) => {
								frappe.msgprint({ title: __("Run restored"), message: r.result, indicator: r.different.length ? "orange" : "green" });
								frm.reload_doc();
							}),
					__("Restore bill run {0}", [frm.doc.bill_run]),
					__("Restore")
				);
			});
		}
	},
});
