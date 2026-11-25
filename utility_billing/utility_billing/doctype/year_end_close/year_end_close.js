// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
frappe.ui.form.on("Year End Close", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.dashboard.clear_headline();
		if (frm.doc.status === "Closed") {
			frm.dashboard.set_headline_alert(__("Year closed: every month is locked and the balances below are carried forward."), "green");
			return;
		}
		frm.dashboard.set_headline_alert(__("Work out the figures, check them, then close the year. Every month of the year must be closed first."), "blue");
		// in the background with a progress bar (increment 55)
		const run = (job, title, done) => () =>
			utility_billing.run_job(job, { name: frm.doc.name }, title).then(() => {
				frm.reload_doc();
				if (done) frappe.show_alert({ message: done, indicator: "green" });
			});
		frm.add_custom_button(__("Work Out Figures"), run("year_prepare", __("Working out the year's figures"), __("Figures worked out.")));
		frm.add_custom_button(__("Close Year"), () => frappe.confirm(__("Close this year? It can't be undone."), run("year_close", __("Closing the year"), __("Year closed."))))
			.addClass("btn-primary");
	},
});
