// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Shared approval UI for documents using utility_billing.utility_billing.approval:
// renders the stored Approval Log as a readable, wrapping history (newest first).

frappe.provide("utility_billing");

utility_billing.render_approval_history = function (frm, html_field = "approval_history_html") {
	const field = frm.fields_dict[html_field];
	if (!field) return;
	const rows = (frm.doc.approval_log || []).slice().reverse();
	if (!rows.length) {
		field.$wrapper.html(
			`<p class="text-muted small">${__("No approval steps yet.")}</p>`
		);
		return;
	}
	const esc = frappe.utils.escape_html;
	const badge = {
		Approve: "green",
		Reject: "red",
		Withdraw: "orange",
		Cancel: "gray",
	};
	const items = rows
		.map((r) => {
			const who = frappe.user.full_name(r.user) || r.user;
			const remarks = r.remarks
				? `<div style="white-space: pre-wrap; margin-top: 4px;">${esc(r.remarks)}</div>`
				: "";
			return `<div style="padding: 10px 0; border-bottom: 1px solid var(--border-color);">
				<span class="indicator-pill ${badge[r.action] || "blue"}">${esc(__(r.action))}</span>
				<b style="margin-left: 6px;">${esc(who)}</b>
				<span class="text-muted small" style="margin-left: 6px;">${esc(frappe.datetime.str_to_user(r.on))}</span>
				${remarks}
			</div>`;
		})
		.join("");
	field.$wrapper.html(`<div>${items}</div>`);
};
