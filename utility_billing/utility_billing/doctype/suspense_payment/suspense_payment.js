// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Suspense Payment", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Held") return;
		frm.add_custom_button(__("Match to Account"), () =>
			frappe.prompt(
				{ fieldname: "connection", fieldtype: "Link", options: "Connection", label: __("Account"), reqd: 1,
				  get_query: () => ({ filters: { society: frm.doc.society } }) },
				(v) => frm.call("match", { connection: v.connection }).then(() => frm.reload_doc()),
				__("Post this payment to which account?")
			)
		).addClass("btn-primary");
		frm.add_custom_button(__("Return to Payer"), () =>
			frappe.prompt({ fieldname: "remarks", fieldtype: "Small Text", label: __("How was it returned, or why?"), reqd: 1 },
				(v) => frm.call("return_to_payer", { remarks: v.remarks }).then(() => frm.reload_doc()))
		);
	},
});
