// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Open -> receipts -> Close Day (cash count, deposit slip) -> Approve Day by a supervisor (FS-PAY-03).

const DAY = "utility_billing.utility_billing.doctype.cashier_day.cashier_day";

frappe.ui.form.on("Cashier Day", {
	onload(frm) {
		// the cashier's usual counter: the one of their last day, else their only counter in User Access
		if (frm.is_new() && !frm.doc.cash_counter) {
			frappe.xcall("utility_billing.utility_billing.choices.my_counter").then((c) => c && frm.set_value("cash_counter", c));
		}
	},
	refresh(frm) {
		const colors = { Open: "green", Closed: "orange", Approved: "blue" };
		frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
		if (frm.is_new()) return;
		frm.add_custom_button(__("Receipts"), () => frappe.set_route("List", "Payment Receipt", { cashier_day: frm.doc.name }));
		if (frm.doc.status === "Open" && frm.doc.cashier === frappe.session.user) {
			frm.add_custom_button(__("New Receipt"), () => frappe.new_doc("Payment Receipt", { payment_channel: "Counter" })).addClass(
				"btn-primary"
			);
			frm.add_custom_button(__("Close Day"), () => close_dialog(frm));
		}
		if (
			frm.doc.status === "Closed" &&
			frm.doc.cashier !== frappe.session.user &&
			utility_billing.can("cash.approve_day")
		) {
			frm.add_custom_button(__("Approve Day"), () =>
				frappe.call({ method: `${DAY}.approve_day`, args: { name: frm.doc.name }, freeze: true }).then(() => frm.reload_doc())
			).addClass("btn-primary");
		}
	},
});

function close_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Close Day {0}", [frm.doc.name]),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p>${__("Count the cash in the drawer. Expected: opening cash + cash received − cash reversed.")}</p>`,
			},
			{ fieldname: "cash_counted", fieldtype: "Currency", label: __("Cash Counted"), reqd: 1 },
			{ fieldname: "deposit_slip_no", fieldtype: "Data", label: __("Bank Deposit Slip No") },
			{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks"), description: __("Required if the count differs") },
		],
		primary_action_label: __("Close Day"),
		primary_action(values) {
			frappe.call({ method: `${DAY}.close_day`, args: { name: frm.doc.name, ...values }, freeze: true }).then(() => {
				d.hide();
				frm.reload_doc();
			});
		},
	});
	d.show();
}
