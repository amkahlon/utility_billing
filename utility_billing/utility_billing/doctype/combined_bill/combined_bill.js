// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Combined Bill: download the one bill of a billing account's services (increment 22).
frappe.ui.form.on("Combined Bill", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Download PDF"), () =>
			window.open(`/api/method/utility_billing.utility_billing.billaccounts.download_combined?name=${encodeURIComponent(frm.doc.name)}`));
	},
});
