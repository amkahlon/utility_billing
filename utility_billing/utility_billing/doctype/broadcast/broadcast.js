// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Broadcast", {
	setup(frm) {
		frm.set_query("feeder", () => ({ filters: { society: frm.doc.society } }));
		frm.set_query("transformer", () => ({ filters: { society: frm.doc.society } }));
	},
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Draft") return;
		frm.add_custom_button(__("Send Now"), () =>
			frappe.confirm(__("Send this message to every active connection in the audience?"), () =>
				frm.call("send").then((r) => {
					frm.reload_doc();
					frappe.show_alert({ message: __("{0} messages queued.", [r.message]), indicator: "green" });
				})
			)
		).addClass("btn-primary");
	},
});
