// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Society Onboarding: the system's checks, worked out live, with links to where each is set up (FS-CLT-10).
frappe.ui.form.on("Society Onboarding", {
	refresh(frm) {
		if (!frm.doc.society) return;
		frappe.call({ method: "utility_billing.utility_billing.onboarding.checklist", args: { society: frm.doc.society } }).then((r) => {
			const m = r.message;
			const e = frappe.utils.escape_html;
			let area = null;
			const rows = m.checks.map((c) => {
				const head = c.area !== area ? `<tr><td colspan="3" style="padding: 10px 0 4px; font-weight: 600; color: var(--text-muted);">${e(__(c.area))}</td></tr>` : "";
				area = c.area;
				const mark = c.done
					? `<span style="color: var(--green-600, #2e7d32); font-weight: 700;">✓</span>`
					: `<span style="color: var(--red-600, #c0392b); font-weight: 700;">✗</span>`;
				const link = c.route ? `<a href="#" data-route='${e(JSON.stringify(c.route))}'>${e(c.check)}</a>` : e(c.check);
				return `${head}<tr><td style="width: 24px; padding: 3px 0;">${mark}</td><td style="padding: 3px 8px;">${link}</td>
					<td style="padding: 3px 0; color: var(--text-muted);">${e(c.detail || "")}</td></tr>`;
			});
			const html = `<div style="margin-bottom: 6px;">${__("{0} of {1} checks pass", [m.done, m.total])}</div><table style="width: 100%;">${rows.join("")}</table>`;
			frm.fields_dict.checklist_html.$wrapper.html(html);
			frm.fields_dict.checklist_html.$wrapper.find("a[data-route]").on("click", function (ev) {
				ev.preventDefault();
				frappe.set_route(...JSON.parse(this.dataset.route));
			});
		});
		frm.add_custom_button(__("Open Society"), () => frappe.set_route("Form", "Society", frm.doc.society));
	},
});
