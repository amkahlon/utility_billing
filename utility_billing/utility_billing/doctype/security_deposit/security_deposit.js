// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
frappe.ui.form.on("Security Deposit", {
	refresh(frm) {
		utility_billing.setup_approval_form && utility_billing.setup_approval_form(frm);
		frm.dashboard.clear_headline();
		if (frm.doc.docstatus === 1) {
			const rs = (v) => Math.round(Number(v || 0)).toLocaleString("en-US");
			frm.dashboard.set_headline_alert(
				__("Deposit held on this account: Rs {0} (was Rs {1}).", [rs(frm.doc.balance_after), rs(frm.doc.balance_before)]),
				"green"
			);
		} else if (frm.doc.deposit_type === "Refunded") {
			frm.dashboard.set_headline_alert(__("A refund is paid only when the account owes nothing; offset the deposit against arrears first."), "blue");
		}
	},
});
