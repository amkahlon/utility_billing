// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Transformer", {
	setup(frm) {
		frm.set_query("feeder", () => ({ filters: { society: frm.doc.society, enabled: 1 } }));
	},
});
