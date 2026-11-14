// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Make Journals, Export and Reconcile from the journal list (FS-GL-02-04).

const GL = "utility_billing.utility_billing.gl";

frappe.listview_settings["GL Journal"] = {
	onload(listview) {
		if (!frappe.user.has_role(["Accounts Officer", "Billing Manager", "General Manager", "System Manager"])) return;
		listview.page.add_inner_button(__("Make Journals"), () =>
			ask_society(__("Make Journals"), [], (v) =>
				frappe.call({ method: `${GL}.make_journals`, args: { society: v.society }, freeze: true }).then(({ message }) => {
					frappe.show_alert({ message: __("{0} journals made", [message.length]), indicator: "green" });
					listview.refresh();
				})
			)
		);
		listview.page.add_inner_button(__("Export"), () =>
			ask_society(
				__("Export Journals"),
				[
					{ fieldname: "from_date", fieldtype: "Date", label: __("From"), reqd: 1, default: frappe.datetime.month_start() },
					{ fieldname: "to_date", fieldtype: "Date", label: __("To"), reqd: 1, default: frappe.datetime.get_today() },
					{ fieldname: "layout", fieldtype: "Link", options: "Journal Export Layout", label: __("Layout"), reqd: 1, default: "Standard CSV" },
					{ fieldname: "journal_type", fieldtype: "Select", label: __("Journals"), options: ["", "Bill Run", "Daily Transactions"] },
				],
				(v) => open_url_post(`/api/method/${GL}.export_journals`, v)
			)
		);
		listview.page.add_inner_button(__("Reconcile"), () =>
			ask_society(__("Receivables Reconciliation"), [], (v) =>
				frappe.call({ method: `${GL}.reconcile_now`, args: { society: v.society }, freeze: true }).then(({ message: r }) => {
					frappe.msgprint({
						title: __("Receivables Reconciliation: {0}", [__(r.status)]),
						indicator: r.status === "Matched" ? "green" : r.status === "Difference" ? "red" : "orange",
						message: `<table class="table table-sm">
							<tr><td>${__("Customer accounts total")}</td><td class="text-right">${format_currency(r.customer_accounts_total)}</td></tr>
							<tr><td>${__("Receivables in journals")}</td><td class="text-right">${format_currency(r.receivables_posted)}</td></tr>
							<tr><td>${__("Difference")}</td><td class="text-right">${format_currency(r.difference)}</td></tr>
							<tr><td>${__("Entries not yet in a journal")}</td><td class="text-right">${r.not_journaled_count} · ${format_currency(r.not_journaled_amount)}</td></tr>
							<tr><td><b>${__("Unexplained difference")}</b></td><td class="text-right"><b>${format_currency(r.unexplained)}</b></td></tr>
						</table>`,
					});
				})
			)
		);
	},
};

function ask_society(title, fields, action) {
	const d = new frappe.ui.Dialog({
		title,
		fields: [{ fieldname: "society", fieldtype: "Link", options: "Society", label: __("Society"), reqd: 1 }, ...fields],
		primary_action_label: __("Go"),
		primary_action(v) {
			d.hide();
			action(v);
		},
	});
	frappe.db.get_list("Society", { limit: 2 }).then((rows) => rows.length === 1 && d.set_value("society", rows[0].name));
	d.show();
}
