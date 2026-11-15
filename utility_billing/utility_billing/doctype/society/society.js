// Copyright (c) 2026, SoftVerx and contributors
// For license information, please see license.txt

// Society: the go-live checklist (FS-CLT-10) is one click away.
frappe.ui.form.on("Society", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Onboarding Checklist"), () =>
			frappe
				.call({ method: "utility_billing.utility_billing.onboarding.open_for", args: { society: frm.doc.name } })
				.then((r) => frappe.set_route("Form", "Society Onboarding", r.message))
		);
	},
});
