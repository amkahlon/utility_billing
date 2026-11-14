// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

const BILL_COLORS = { Draft: "orange", Posted: "blue", Cancelled: "red" };
const BILL_PRINT = "utility_billing.utility_billing.bill_print";

frappe.ui.form.on("Utility Bill", {
	refresh(frm) {
		// Show the bill's own status (Draft / Posted / Cancelled) instead of Frappe's "Submitted".
		frm.page.set_indicator(__(frm.doc.status), BILL_COLORS[frm.doc.status] || "gray");
		if (frm.doc.bill_run) {
			frm.add_custom_button(__("Bill Run"), () => frappe.set_route("Form", "Bill Run", frm.doc.bill_run));
		}
		if (frm.is_new()) return;

		// PDF of the bill; a posted bill reprints exactly as issued (FS-BIL-16, FS-RUN-09).
		frm.add_custom_button(__("Bill PDF"), () => open_bill_pdf(frm.doc.name, 0), __("Print"));

		if (frm.doc.docstatus === 1) {
			// A duplicate for a consumer who lost the bill, marked DUPLICATE and recorded (FS-BIL-17).
			frm.add_custom_button(
				__("Duplicate Bill"),
				() =>
					frappe.confirm(
						__("Issue a duplicate of bill {0}? It will be marked DUPLICATE and recorded on the bill.", [frm.doc.name]),
						() =>
							frappe.call({ method: `${BILL_PRINT}.issue_duplicate`, args: { name: frm.doc.name } }).then(({ message }) => {
								open_bill_pdf(frm.doc.name, message);
								frm.reload_doc();
							})
					),
				__("Print")
			);
		}
	},
});

function open_bill_pdf(name, duplicate) {
	const args = new URLSearchParams({ name, duplicate: duplicate || 0 });
	window.open(`/api/method/${BILL_PRINT}.download_bill?${args}`);
}
