// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Bill Template", {
	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (!frm.is_new()) {
			frm.add_custom_button(frm.doc.docstatus === 0 ? __("Open in Bill Designer") : __("View in Bill Designer"), () =>
				frappe.set_route("ub-bill-designer", { template: frm.doc.name }));
		}
		const layout = typeof frm.doc.layout === "string" ? JSON.parse(frm.doc.layout || "{}") : frm.doc.layout || {};
		const sections = layout.sections || [];
		const blocks = sections.reduce((n, s) => n + (s.rows || []).reduce((m, r) => m + (r.cells || []).reduce((k, c) => k + (c.blocks || []).length, 0), 0), 0);
		frm.get_field("layout_summary").$wrapper.html(
			`<div class="text-muted">${__("{0} sections, {1} blocks.", [sections.length, blocks])} ${sections
				.map((s) => frappe.utils.escape_html(s.label || s.copy_label || __("Section")))
				.join(" · ")}</div>`
		);
	},

	before_workflow_action(frm) {
		return utility_billing.before_workflow_action(frm);
	},
});
