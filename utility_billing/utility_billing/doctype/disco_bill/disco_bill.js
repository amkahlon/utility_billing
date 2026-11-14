// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("DISCO Bill", {
	setup(frm) {
		frm.set_query("feeder", "inputs", () => ({ filters: { society: frm.doc.society, enabled: 1 } }));
		frm.set_query("transformer", "inputs", () => ({ filters: { society: frm.doc.society, enabled: 1 } }));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.society) {
			frm.add_custom_button(__("Add All Feeders"), () => add_rows(frm, "Feeder", "feeder", "feeder_name"));
			frm.add_custom_button(__("Add All Transformers"), () => add_rows(frm, "Transformer", "transformer", "transformer_code"));
		}
		const total = (frm.doc.inputs || []).reduce((t, r) => t + (r.feeder ? flt(r.input_kwh) : 0), 0);
		if (total && frm.doc.units_kwh) {
			frm.dashboard.set_headline_alert(
				__("Feeder input units add up to {0} of the {1} kWh on the bill ({2}%)", [
					format_number(total, null, 0),
					format_number(frm.doc.units_kwh, null, 0),
					flt((total / frm.doc.units_kwh) * 100, 1),
				]),
				total > frm.doc.units_kwh * 1.01 ? "orange" : "blue"
			);
		}
	},

	society(frm) {
		frm.clear_table("inputs");
		frm.refresh_field("inputs");
	},
});

// Start the input table with every feeder (or transformer) of the society that isn't in it yet; fill in the kWh.
function add_rows(frm, doctype, field, label) {
	frappe.db.get_list(doctype, { filters: { society: frm.doc.society, enabled: 1 }, fields: ["name", label], limit: 500 }).then((rows) => {
		const have = new Set((frm.doc.inputs || []).map((r) => r[field]).filter(Boolean));
		const fresh = rows.filter((r) => !have.has(r.name));
		fresh.forEach((r) => frm.add_child("inputs", { [field]: r.name }));
		frm.refresh_field("inputs");
		frappe.show_alert({ message: __("{0} added", [fresh.length]), indicator: fresh.length ? "green" : "blue" });
	});
}
