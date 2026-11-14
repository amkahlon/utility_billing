// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Settlement File", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status === "Closed") return;
		frm.add_custom_button(__("Import and Match"), () =>
			frm.call("import_file").then((r) => {
				frm.reload_doc();
				frappe.show_alert({ message: __("{0} matched, {1} to look at.", [r.message.matched, r.message.flagged]), indicator: r.message.flagged ? "orange" : "green" });
			})
		).addClass(frm.doc.status === "Draft" ? "btn-primary" : "");
		if (frm.doc.status === "Matched") {
			if (frm.doc.not_recorded) {
				frm.add_custom_button(__("Post Unrecorded"), () =>
					frappe.confirm(
						__("Post each unrecorded row as a payment when its reference number is a real account, and hold the rest in suspense?"),
						() => frm.call("post_unrecorded").then((r) => {
							frm.reload_doc();
							frappe.show_alert({ message: __("{0} payments posted, {1} held in suspense.", [r.message.posted, r.message.suspense]), indicator: "green" });
						})
					)
				);
			}
			frm.add_custom_button(__("Close File"), () => frm.call("close_file").then(() => frm.reload_doc()));
			frm.dashboard.set_headline(__("Flagged rows need an action or a remark before the file can be closed."));
		}
	},
});
