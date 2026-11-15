// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Licence Client: make the key a society's site reports its usage with (shown once; increment 24).
frappe.ui.form.on("Licence Client", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(frm.doc.key_hint ? __("Replace Licence Key") : __("Make Licence Key"), () => {
			const go = () =>
				frappe.xcall("utility_billing.utility_billing.licensing.new_key", { client: frm.doc.name }).then((r) => {
					frappe.msgprint({
						title: __("Licence key for {0}", [frm.doc.client_name]),
						message: `<p>${__("Enter it in the society site's Licence Settings. It is shown only now; only its last four characters are kept.")}</p>
							<pre style="user-select:all">${frappe.utils.escape_html(r.key)}</pre>`,
					});
					frm.reload_doc();
				});
			frm.doc.key_hint ? frappe.confirm(__("The site's current key stops working. Make a new one?"), go) : go();
		});
		frm.add_custom_button(__("Invoices"), () => frappe.set_route("List", "Licence Invoice", { client: frm.doc.name }));
	},
});
