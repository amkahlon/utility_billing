// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
frappe.ui.form.on("Billing Period", {
	refresh(frm) {
		if (frm.is_new()) return;
		const open = frm.doc.status === "Open";
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline_alert(
			open
				? __("Open: bills, payments and adjustments can still be dated in this month.")
				: __("Closed and locked: nothing can be dated in this month. Corrections go through adjustments in the open period."),
			open ? "blue" : "green"
		);
		if (open) {
			frm.add_custom_button(__("Show Checklist"), () => {
				utility_billing.run_job("period_checklist", { period: frm.doc.name }, __("Month-end checklist"))
					.then((rows) => show_checks(rows || [], __("Month-end checklist"), false));
			});
			frm.add_custom_button(__("Close Month"), () => {
				frappe.confirm(__("Close {0}? Nothing can be dated in it afterwards.", [frm.doc.period_title]), () => {
					utility_billing.run_job("period_close", { period: frm.doc.name }, __("Closing {0}", [frm.doc.period_title])).then(() => frm.reload_doc());
				});
			}).addClass("btn-primary");
		} else if (utility_billing.can("period.reopen")) {
			frm.add_custom_button(__("Reopen"), () => {
				frappe.prompt(
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Why is it being reopened?"), reqd: 1 }],
					(v) => {
						utility_billing.run_job("period_reopen", { period: frm.doc.name, reason: v.reason }, __("Reopening {0}", [frm.doc.period_title]))
							.then(() => frm.reload_doc());
					},
					__("Reopen {0}", [frm.doc.period_title]),
					__("Reopen")
				);
			});
		}
	},
});

function show_checks(rows, title) {
	const color = { Passed: "green", Warning: "orange", Failed: "red" };
	const html = rows
		.map(
			(r) =>
				`<div style="margin-bottom:8px"><span class="indicator-pill ${color[r.result]}">${__(r.result)}</span> <b>${frappe.utils.escape_html(r.check)}</b><div class="text-muted small">${frappe.utils.escape_html(r.detail)}</div></div>`
		)
		.join("");
	frappe.msgprint({ title, message: html, wide: true });
}
