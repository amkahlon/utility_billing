// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Recovery Run", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Prepare List"), () =>
				frm.call("prepare").then((r) => {
					frm.reload_doc();
					frappe.show_alert({ message: __("{0} notices proposed.", [r.message]), indicator: "blue" });
				})
			);
			if (frm.doc.items && frm.doc.items.length) {
				frm.add_custom_button(__("Issue Notices"), () =>
					frappe.confirm(__("Issue the ticked notices? Each stage and date is recorded against the account."), () =>
						frm.call("issue").then((r) => {
							frm.reload_doc();
							frappe.show_alert({ message: __("{0} notices issued.", [r.message]), indicator: "green" });
						})
					)
				).addClass("btn-primary");
			}
			frm.dashboard.set_headline(__("Untick any account you don't want to send a notice to, then press Issue Notices."));
		}
		if (frm.doc.status === "Issued") {
			frm.add_custom_button(__("Print Notices"), () => {
				window.open(
					`/api/method/utility_billing.utility_billing.recovery.download_notices?run=${encodeURIComponent(frm.doc.name)}`
				);
			});
			frm.add_custom_button(__("Notices"), () => frappe.set_route("List", "Recovery Notice", { recovery_run: frm.doc.name }));
		}
	},
});
