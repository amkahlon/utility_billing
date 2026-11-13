// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

const RDG = "utility_billing.utility_billing.readings";

frappe.ui.form.on("Reading Cycle", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Open") return;
		frm.add_custom_button(__("Create Reading Sheet"), () =>
			frappe.call({ method: `${RDG}.create_reading_sheet`, args: { cycle: frm.doc.name }, freeze: true }).then(({ message: r }) => {
				frappe.show_alert({ message: __("{0} readings added ({1} in total)", [r.created, r.total]), indicator: "green" });
				frm.reload_doc();
			})
		);
		frm.add_custom_button(__("Upload Readings"), () => upload_readings(frm));
		frm.add_custom_button(__("Open Readings"), () =>
			frappe.set_route("List", "Meter Reading", { reading_cycle: frm.doc.name })
		);
		frm.add_custom_button(__("Review Queue"), () =>
			frappe.set_route("List", "Meter Reading", { reading_cycle: frm.doc.name, status: "Flagged" })
		);
		frm.add_custom_button(
			__("Estimate Unread"),
			() =>
				frappe.confirm(__("Estimate every reading that is still pending or not read?"), () =>
					frappe.call({ method: `${RDG}.estimate_unread`, args: { cycle: frm.doc.name }, freeze: true }).then(({ message: r }) => {
						frappe.show_alert({ message: __("{0} readings estimated", [r.estimated]), indicator: "orange" });
						frm.reload_doc();
					})
				),
			__("Close")
		);
		frm.add_custom_button(
			__("Close Cycle"),
			() =>
				frappe.confirm(__("Close this reading cycle? Its readings can't be changed afterwards."), () =>
					frappe.call({ method: `${RDG}.close_cycle`, args: { cycle: frm.doc.name } }).then(() => frm.reload_doc())
				),
			__("Close")
		);
	},
});

function upload_readings(frm) {
	new frappe.ui.FileUploader({
		doctype: frm.doctype,
		docname: frm.doc.name,
		restrictions: { allowed_file_types: [".xlsx", ".csv"] },
		on_success(file) {
			frappe
				.call({ method: `${RDG}.upload_readings`, args: { cycle: frm.doc.name, file_url: file.file_url }, freeze: true })
				.then(({ message: r }) => {
					const rows = (r.errors || [])
						.map((e) => `<tr><td>${e.row}</td><td>${frappe.utils.escape_html(e.reference_no)}</td><td>${frappe.utils.escape_html(e.error)}</td></tr>`)
						.join("");
					frappe.msgprint({
						title: __("Readings uploaded"),
						indicator: r.errors.length ? "orange" : "green",
						message:
							`<p>${__("{0} readings applied.", [r.updated])}</p>` +
							(rows
								? `<p>${__("These rows were not applied:")}</p><table class="table table-bordered"><tr><th>${__("Row")}</th><th>${__("Reference No")}</th><th>${__("Reason")}</th></tr>${rows}</table>`
								: ""),
					});
					frm.reload_doc();
				});
		},
	});
}
