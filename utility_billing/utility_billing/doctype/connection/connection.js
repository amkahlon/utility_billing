// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Connection", {
	setup(frm) {
		frm.set_query("property", () => ({ filters: { society: frm.doc.society } }));
		frm.set_query("consumer_category", () => ({
			filters: { utility: frm.doc.utility, enabled: 1 },
		}));
		frm.set_query("transformer", () => ({ filters: { society: frm.doc.society, enabled: 1 } }));
		// Meters of this society and utility that are free to install (or already on this connection).
		frm.set_query("meter", "meters", () => ({
			filters: {
				society: frm.doc.society,
				utility: frm.doc.utility,
				status: ["in", ["In Store", "Removed", "Installed"]],
			},
		}));
	},

	society(frm) {
		frm.set_value({ property: null, transformer: null });
	},

	utility(frm) {
		frm.set_value("consumer_category", null);
	},

	property(frm) {
		// Suggest the billed occupant of the property as the consumer.
		if (!frm.doc.property || frm.doc.consumer) return;
		frappe.db.get_doc("Property", frm.doc.property).then((p) => {
			const today = frappe.datetime.get_today();
			const billed = (p.occupants || []).find(
				(o) => o.is_billed && o.from_date <= today && (!o.to_date || o.to_date >= today)
			);
			if (billed) frm.set_value("consumer", billed.consumer);
		});
	},
});
