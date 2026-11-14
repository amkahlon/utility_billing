// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Connection", {
	setup(frm) {
		frm.set_query("property", () => ({ filters: { society: frm.doc.society } }));
		frm.set_query("consumer_category", () => ({
			filters: { utility: frm.doc.utility, enabled: 1 },
		}));
		frm.set_query("transformer", () => ({ filters: { society: frm.doc.society, enabled: 1 } }));
		// Meters of this society and utility that are free to install (or already on this connection).
		frm.set_query("meter", "meters", () => ({
			filters: {
				society: frm.doc.society,
				utility: frm.doc.utility,
				status: ["in", ["In Store", "Removed", "Installed"]],
			},
		}));
	},

	refresh(frm) {
		if (frm.is_new()) return;
		show_account(frm);
		const group = __("Account");
		frm.add_custom_button(__("Ledger"), () => frappe.set_route("List", "Customer Ledger Entry", { connection: frm.doc.name }), group);
		frm.add_custom_button(__("Statement"), () => statement_dialog(frm), group);
		if (frappe.model.can_create("Payment Receipt")) {
			frm.add_custom_button(
				__("Receive Payment"),
				() => frappe.new_doc("Payment Receipt", { reference_no: frm.doc.name }),
				group
			);
		}
		if (frappe.model.can_create("Fee Charge")) {
			frm.add_custom_button(__("Charge Fee"), () => frappe.new_doc("Fee Charge", { connection: frm.doc.name }), group);
		}
	},

	society(frm) {
		frm.set_value({ property: null, transformer: null });
	},

	utility(frm) {
		frm.set_value("consumer_category", null);
	},

	property(frm) {
		// Suggest the billed occupant of the property as the consumer.
		if (!frm.doc.property || frm.doc.consumer) return;
		frappe.db.get_doc("Property", frm.doc.property).then((p) => {
			const today = frappe.datetime.get_today();
			const billed = (p.occupants || []).find(
				(o) => o.is_billed && o.from_date <= today && (!o.to_date || o.to_date >= today)
			);
			if (billed) frm.set_value("consumer", billed.consumer);
		});
	},
});

// Customer account balances (FS-REC-11), shown at the top of the form.
function show_account(frm) {
	frappe
		.call({ method: "utility_billing.utility_billing.account.account_summary", args: { connection: frm.doc.name } })
		.then(({ message: s }) => {
			if (!s.entries) return;
			const b = s.balances;
			const owed = s.balance < 0 ? __("In credit {0}", [format_currency(-s.balance)]) : __("Owes {0}", [format_currency(s.balance)]);
			frm.dashboard.set_headline_alert(
				`<b>${owed}</b> — ${__("current bill {0}, arrears {1}, surcharge {2}, fees {3}, credit {4}", [
					format_currency(b.current),
					format_currency(b.arrears),
					format_currency(b.surcharge),
					format_currency(b.fees),
					format_currency(b.credit),
				])}`,
				s.balance > 0 ? "orange" : "green"
			);
		});
}

// Account statement for a date range (FS-REC-14).
function statement_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Account Statement"),
		fields: [
			{ fieldname: "from_date", fieldtype: "Date", label: __("From"), default: frappe.datetime.add_months(frappe.datetime.get_today(), -12) },
			{ fieldname: "to_date", fieldtype: "Date", label: __("To"), default: frappe.datetime.get_today() },
		],
		primary_action_label: __("Open PDF"),
		primary_action(v) {
			const args = new URLSearchParams({ connection: frm.doc.name, from_date: v.from_date || "", to_date: v.to_date || "" });
			window.open(`/api/method/utility_billing.utility_billing.account_print.download_statement?${args}`);
			d.hide();
		},
	});
	d.show();
}
