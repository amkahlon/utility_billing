// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Counter flow (FS-PAY-01): scan or type the reference number -> the account and amount due fill in ->
// Ctrl+S saves, Ctrl+S again submits -> the receipt PDF opens.

const ACCOUNT = "utility_billing.utility_billing.account";
const RECEIPT_COLORS = { Draft: "orange", Posted: "green", Reversed: "red" };

frappe.ui.form.on("Payment Receipt", {
	onload(frm) {
		if (frm.is_new() && frm.doc.payment_channel === "Counter") {
			frappe.call(`utility_billing.utility_billing.doctype.cashier_day.cashier_day.my_open_day`).then(({ message }) => {
				if (!message) {
					frm.dashboard.set_headline_alert(__("Open your cashier day before taking payments."), "red");
				} else {
					frm.dashboard.set_headline_alert(__("Cashier day {0} at counter {1}", [message.name, message.cash_counter]), "blue");
				}
			});
		}
	},

	refresh(frm) {
		frm.page.set_indicator(__(frm.doc.status), RECEIPT_COLORS[frm.doc.status] || "gray");
		if (frm.is_new()) {
			setTimeout(() => frm.get_field("reference_no").$input && frm.get_field("reference_no").$input.focus(), 300);
		}
		if (!frm.is_new()) {
			frm.add_custom_button(__("Receipt PDF"), () => open_receipt(frm.doc.name));
		}
		if (frm.doc.docstatus === 1 && utility_billing.can("payments.reverse")) {
			frm.add_custom_button(__("Reverse Payment"), () => reverse_dialog(frm, "Payment Receipt", "Payment Reversal"));
		}
	},

	reference_no(frm) {
		const ref = (frm.doc.reference_no || "").trim();
		if (ref.length < 6) return;
		frappe
			.call({ method: `${ACCOUNT}.enquiry`, args: { reference_no: ref } })
			.then(({ message: e }) => {
				frm.set_value({
					connection: e.connection,
					consumer_name: e.consumer_name,
					society: e.society,
					latest_bill: e.bill,
					bill_due_date: e.due_date,
					payable_within_due: e.payable_within_due,
					payable_after_due: e.payable_after_due,
					balance_before: e.balance,
				});
				if (!frm.doc.amount) frm.set_value("amount", e.amount_due);
				frm.get_field("amount").$input && frm.get_field("amount").$input.focus().select();
			});
	},

	on_submit(frm) {
		open_receipt(frm.doc.name);
	},
});

function open_receipt(name) {
	window.open(`/api/method/utility_billing.utility_billing.account_print.download_receipt?${new URLSearchParams({ name })}`);
}

// Shared with Fee Charge: reverse with a reason code (FS-PAY-12).
function reverse_dialog(frm, doctype, used_for) {
	const d = new frappe.ui.Dialog({
		title: __("Reverse {0} {1}", [__(doctype), frm.doc.name]),
		fields: [
			{
				fieldname: "reason_code",
				fieldtype: "Link",
				options: "Reason Code",
				label: __("Reason"),
				reqd: 1,
				get_query: () => ({ filters: { applies_to: used_for, enabled: 1 } }),
			},
			{ fieldname: "remarks", fieldtype: "Small Text", label: __("Explanation"), reqd: 1 },
		],
		primary_action_label: __("Reverse"),
		primary_action(values) {
			frappe
				.call({
					method: `${ACCOUNT}.reverse_document`,
					args: { doctype, name: frm.doc.name, ...values },
					freeze: true,
				})
				.then(() => {
					d.hide();
					frm.reload_doc();
				});
		},
	});
	d.show();
}
