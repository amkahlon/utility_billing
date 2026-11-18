// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Parallel Run Month: the previous system's bills and payments of a month beside ours (increment 29).
const PRM = "utility_billing.utility_billing.doctype.parallel_run_month.parallel_run_month.";

frappe.ui.form.on("Parallel Run Month", {
	setup(frm) {
		frm.set_query("bill_run", () => ({ filters: { society: frm.doc.society, billing_month: frm.doc.billing_month, docstatus: ["<", 2] } }));
		frm.set_query("profile", () => ({ filters: { society: frm.doc.society } }));
	},
	refresh(frm) {
		if (frm.is_new()) return;
		const run = (method, message, done) => frm.save().then(() =>
			frappe.call({ method: PRM + method, args: { name: frm.doc.name }, freeze: true, freeze_message: message })
				.then((r) => { frm.reload_doc(); done(r.message); }));
		frm.add_custom_button(__("Load Payments"), () => run("load_payments", __("Recording payments…"), (m) =>
			frappe.show_alert({ message: __("{0} payments recorded, {1} skipped", [m.recorded, m.skipped]), indicator: "green" })), __("Parallel Run"));
		frm.add_custom_button(__("Compare"), () => run("compare", __("Comparing…"), (m) =>
			frappe.show_alert({ message: __("{0} of {1} accounts match", [m.matched, m.compared]), indicator: m.different ? "orange" : "green" })), __("Parallel Run"));
		const d = frm.doc;
		if (d.comparison_status === "Clean") {
			frm.dashboard.set_headline(__("All {0} accounts match the previous system this month.", [d.accounts_compared]), "green");
		} else if (d.comparison_status === "Compared") {
			frm.dashboard.set_headline(__("{0} of {1} accounts match; {2} differ, {3} not in our run, {4} not in their bills. Total difference {5}.",
				[d.accounts_matched, d.accounts_compared, d.accounts_different, d.missing_here, d.missing_there,
					format_currency(d.total_difference)]), "orange");
		}
	},
});
