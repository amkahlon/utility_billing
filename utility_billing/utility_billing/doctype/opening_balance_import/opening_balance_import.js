// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Opening Balance Import: load the file, see the problems, download them to fix at source (gap 9).
const OB = "utility_billing.utility_billing.opening.";

frappe.ui.form.on("Opening Balance Import", {
	refresh(frm) {
		frm.add_custom_button(__("Download Template"), () => window.open(`/api/method/${OB}template`), __("File"));
		if (!frm.is_new() && frm.doc.docstatus === 0 && ["Draft", "Rejected", undefined, ""].includes(frm.doc.workflow_state)) {
			frm.add_custom_button(__("Load File"), () => {
				frm.save().then(() =>
					frappe.call({ method: OB + "load_file", args: { name: frm.doc.name }, freeze: true, freeze_message: __("Loading and checking…") })
						.then((r) => {
							frm.reload_doc();
							const m = r.message;
							frappe.show_alert({ message: __("{0} accounts loaded, {1} with problems", [m.rows, m.errors]), indicator: m.errors ? "orange" : "green" });
						}));
			}, __("File"));
		}
		if (frm.doc.error_count) {
			frm.add_custom_button(__("Download Problems"), () => window.open(`/api/method/${OB}error_report?name=${encodeURIComponent(frm.doc.name)}`), __("File"));
			frm.dashboard.set_headline(__("{0} of {1} rows have problems: fix them in the file and load it again.", [frm.doc.error_count, frm.doc.row_count]), "orange");
		} else if (frm.doc.row_count && frm.doc.docstatus === 0) {
			frm.dashboard.set_headline(__("All {0} accounts checked. Submit for approval when the totals agree with the previous system.", [frm.doc.row_count]), "green");
		}
	},
});
