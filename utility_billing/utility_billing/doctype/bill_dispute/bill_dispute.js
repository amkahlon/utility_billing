// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Bill Dispute", {
	setup(frm) {
		frm.set_query("bill", () => ({ filters: { connection: frm.doc.connection, docstatus: 1 } }));
	},

	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.court_hold) {
			frm.add_custom_button(__("Court Hold"), () => frappe.set_route("Form", "Court Hold", frm.doc.court_hold));
		}
		if (frm.doc.status !== "Open") return;
		// the dispute has gone to court (increment 45): a court hold takes it over, filled in from here
		if (frappe.model.can_create("Court Hold")) {
			frappe.db.get_value("Court Hold", { bill_dispute: frm.doc.name, docstatus: 0 }, "name").then((r) => {
				const open = r && r.message && r.message.name;
				if (open) {
					frm.add_custom_button(__("Court Hold {0}", [open]), () => frappe.set_route("Form", "Court Hold", open));
				} else {
					frm.add_custom_button(__("Taken to Court"), () =>
						frappe.new_doc("Court Hold", {
							connection: frm.doc.connection,
							bill_dispute: frm.doc.name,
							amount: frm.doc.disputed_amount,
							remarks: __("Dispute {0}: {1}", [frm.doc.name, frm.doc.reason]),
						})
					);
				}
			});
		}
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
