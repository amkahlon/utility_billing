// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
frappe.ui.form.on("Year End Close", {
	refresh(frm) {
		if (frm.is_new()) return;
		const method = "utility_billing.utility_billing.yearend";
		frm.dashboard.clear_headline();
		if (frm.doc.status === "Closed") {
			frm.dashboard.set_headline_alert(__("Year closed: every month is locked and the balances below are carried forward."), "green");
			return;
		}
		frm.dashboard.set_headline_alert(__("Work out the figures, check them, then close the year. Every month of the year must be closed first."), "blue");
		const run = (fn, done) => () =>
			frappe.call({ method: `${method}.${fn}`, args: { name: frm.doc.name }, freeze: true }).then(() => {
				frm.reload_doc();
				if (done) frappe.show_alert({ message: done, indicator: "green" });
			});
		frm.add_custom_button(__("Work Out Figures"), run("prepare", __("Figures worked out.")));
		frm.add_custom_button(__("Close Year"), () => frappe.confirm(__("Close this year? It can't be undone."), run("close", __("Year closed."))))
			.addClass("btn-primary");
	},
});
