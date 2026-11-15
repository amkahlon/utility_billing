// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
frappe.ui.form.on("Licence Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		frm.add_custom_button(__("Download PDF"), () =>
			window.open(`/api/method/utility_billing.utility_billing.licensing.download_invoice?name=${encodeURIComponent(frm.doc.name)}`));
		if (frm.doc.outstanding > 0) {
			frm.add_custom_button(__("Record Payment"), () => frappe.new_doc("Licence Payment", { client: frm.doc.client, amount: frm.doc.outstanding }));
		}
	},
});
