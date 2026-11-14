// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Fee Charge", {
	setup(frm) {
		frm.set_query("fee_type", () => ({ filters: { society: frm.doc.society, enabled: 1 } }));
	},

	refresh(frm) {
		const colors = { Draft: "orange", Posted: "blue", Reversed: "red" };
		frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
		if (frm.doc.docstatus === 1 && frappe.user.has_role(["Billing Manager", "System Manager"])) {
			frm.add_custom_button(__("Reverse Fee"), () => {
				const d = new frappe.ui.Dialog({
					title: __("Reverse fee {0}", [frm.doc.name]),
					fields: [
						{
							fieldname: "reason_code",
							fieldtype: "Link",
							options: "Reason Code",
							label: __("Reason"),
							reqd: 1,
							get_query: () => ({ filters: { applies_to: "Fee Reversal", enabled: 1 } }),
						},
						{ fieldname: "remarks", fieldtype: "Small Text", label: __("Explanation"), reqd: 1 },
					],
					primary_action_label: __("Reverse"),
					primary_action(values) {
						frappe
							.call({
								method: "utility_billing.utility_billing.account.reverse_document",
								args: { doctype: "Fee Charge", name: frm.doc.name, ...values },
								freeze: true,
							})
							.then(() => {
								d.hide();
								frm.reload_doc();
							});
					},
				});
				d.show();
			});
		}
	},

	connection(frm) {
		if (!frm.doc.connection) return;
		frappe.db.get_value("Connection", frm.doc.connection, ["society", "consumer_name"]).then(({ message }) => {
			frm.set_value({ society: message.society, consumer_name: message.consumer_name });
		});
	},

	fee_type(frm) {
		if (!frm.doc.fee_type) return;
		frappe.db.get_value("Fee Type", frm.doc.fee_type, "amount").then(({ message }) => frm.set_value("amount", message.amount));
	},
});
