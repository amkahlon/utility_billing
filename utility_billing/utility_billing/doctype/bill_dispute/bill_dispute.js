// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Bill Dispute", {
	setup(frm) {
		frm.set_query("bill", () => ({ filters: { connection: frm.doc.connection, docstatus: 1 } }));
	},

	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Open") return;
		frm.add_custom_button(__("Decide"), () => {
			frappe.prompt(
				[
					{ fieldname: "outcome", fieldtype: "Select", label: __("Outcome"), reqd: 1, options: "Released\nBill revised\nWithdrawn",
					  description: __("Released: the consumer pays and the surcharge runs again. Bill revised: settled by an approved bill revision.") },
					{ fieldname: "revision", fieldtype: "Link", options: "Bill Revision", label: __("Bill Revision"), depends_on: "eval:doc.outcome=='Bill revised'",
					  get_query: () => ({ filters: { connection: frm.doc.connection, docstatus: 1 } }) },
					{ fieldname: "remarks", fieldtype: "Small Text", label: __("Decision and reason"), reqd: 1 },
				],
				(v) => frm.call("resolve", { outcome: v.outcome, remarks: v.remarks, revision: v.revision }).then(() => frm.reload_doc()),
				__("Decide the dispute")
			);
		}).addClass("btn-primary");
		frm.dashboard.set_headline(__("While this is open, the surcharge on the bill is held and Rs {0} is left out of recovery.", [format(frm.doc.disputed_amount)]));
	},
});

function format(v) {
	return Math.round(Number(v || 0)).toLocaleString("en-US");
}
