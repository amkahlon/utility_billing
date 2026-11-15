// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// RA Finding: open the account or subject, and close with a result (FS-RA-09).
frappe.ui.form.on("RA Finding", {
	refresh(frm) {
		if (frm.doc.connection) {
			frm.add_custom_button(__("Open Account"), () => frappe.set_route("Form", "Connection", frm.doc.connection));
		}
		if (frm.doc.status !== "Closed" && !frm.is_new()) {
			frm.add_custom_button(__("Close with Result"), () => {
				frappe.prompt([
					{ fieldname: "result", fieldtype: "Select", label: __("Result"), reqd: 1,
					  options: "Detection bill\nBill revision\nCorrection\nMaster data fixed\nNo issue\nOther" },
					{ fieldname: "result_doctype", fieldtype: "Link", label: __("Settled By (Type)"), options: "DocType" },
					{ fieldname: "result_document", fieldtype: "Dynamic Link", label: __("Settled By"), options: "result_doctype" },
					{ fieldname: "amount_recovered", fieldtype: "Currency", label: __("Amount Recovered") },
					{ fieldname: "notes", fieldtype: "Small Text", label: __("What was done") },
				], (v) => {
					frm.set_value({ ...v, status: "Closed", notes: [frm.doc.notes, v.notes].filter(Boolean).join("\n") });
					frm.save();
				}, __("Close the finding"), __("Close"));
			});
		}
	},
});
