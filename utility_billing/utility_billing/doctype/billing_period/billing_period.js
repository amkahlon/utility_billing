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
				frappe.call({
					method: "utility_billing.utility_billing.periods.preview",
					args: { period: frm.doc.name },
					callback: (r) => show_checks(r.message || [], __("Month-end checklist"), false),
				});
			});
			frm.add_custom_button(__("Close Month"), () => {
				frappe.confirm(__("Close {0}? Nothing can be dated in it afterwards.", [frm.doc.period_title]), () => {
					frappe.call({
						method: "utility_billing.utility_billing.periods.close",
						args: { period: frm.doc.name },
						freeze: true,
						callback: () => frm.reload_doc(),
						error: () => frm.reload_doc(),
					});
				});
			}).addClass("btn-primary");
		} else if (frappe.user.has_role(["General Manager", "System Manager"])) {
			frm.add_custom_button(__("Reopen"), () => {
				frappe.prompt(
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Why is it being reopened?"), reqd: 1 }],
					(v) => {
						frappe.call({
							method: "utility_billing.utility_billing.periods.reopen",
							args: { period: frm.doc.name, reason: v.reason },
							freeze: true,
							callback: () => frm.reload_doc(),
						});
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
