// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Sign-in security status and the recommended policy (FS-ACC-09).
frappe.listview_settings["User Access"] = {
	onload(list) {
		list.page.add_inner_button(__("Sign-in Security"), () => {
			frappe.call("utility_billing.utility_billing.security.get_status").then(({ message: rows }) => {
				const html = rows
					.map((r) => `<div style="margin-bottom:6px"><span class="indicator-pill ${r.ok ? "green" : "orange"}">${r.ok ? __("OK") : __("To do")}</span>
						<b>${frappe.utils.escape_html(r.check)}</b> <span class="text-muted">(${frappe.utils.escape_html(r.now)})</span></div>`)
					.join("");
				const d = new frappe.ui.Dialog({
					title: __("Sign-in Security"),
					fields: [{ fieldtype: "HTML", options: html + `<p class="text-muted small">${__(
						"Before switching two-factor sign-in on, make sure each manager can set up an authenticator app on their phone."
					)}</p>` }],
				});
				if (frappe.user.has_role("System Manager")) {
					d.set_primary_action(__("Apply Recommended Policy"), () =>
						frappe.confirm(__("Apply strong passwords, a lock after 5 failed sign-ins and two-factor sign-in for managers?"), () =>
							frappe.call({ method: "utility_billing.utility_billing.security.apply_policy", freeze: true }).then(() => {
								d.hide();
								frappe.show_alert({ message: __("Policy applied."), indicator: "green" });
							})
						)
					);
				}
				d.show();
			});
		});
		list.page.add_inner_button(__("Approval Inbox"), () => frappe.set_route("ub-approvals"));
	},
};
