// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

const RDG = "utility_billing.utility_billing.readings";

frappe.ui.form.on("Reading Cycle", {
	refresh(frm) {
		if (frm.doc.status === "Closed") {
			frm.disable_form(); // closed cycles are locked; the server refuses changes too
			frm.dashboard.set_headline_alert(__("Closed: readings in this cycle can't change."), "gray");
		}
		if (frm.is_new() || frm.doc.status !== "Open") return;
		frm.add_custom_button(__("Create Reading Sheet"), () =>
			frappe.call({ method: `${RDG}.create_reading_sheet`, args: { cycle: frm.doc.name }, freeze: true }).then(({ message: r }) => {
				frappe.show_alert({ message: __("{0} readings added ({1} in total)", [r.created, r.total]), indicator: "green" });
				frm.reload_doc();
			})
		);
		// increment 47: every account of the cycle on one screen, only the reading typed
		frm.add_custom_button(__("Reading Entry"), () => {
			frappe.route_options = { society: frm.doc.society, cycle: frm.doc.name };
			frappe.set_route("ub-reading-entry");
		});
		frm.add_custom_button(__("Upload Readings"), () => {
			// where the file is from decides what it may do: only the meter data system's readings are taken for smart meters
			frappe.prompt(
				[{ fieldname: "source", fieldtype: "Select", label: __("The file is from"), reqd: 1, default: "Import",
				   options: [{ value: "Import", label: __("An office spreadsheet") }, { value: "Mobile", label: __("The mobile reading app") },
				             { value: "MDM", label: __("The meter data system (smart meters)") }],
				   description: __("Columns: Reference No or Meter No, Present Reading, and if needed Reading Date, Exception Code, MDI, Export Reading, Mute. Excel, CSV or text.") }],
				(v) => upload_readings(frm, v.source),
				__("Upload readings"),
				__("Choose the File")
			);
		});
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

function upload_readings(frm, source) {
	new frappe.ui.FileUploader({
		doctype: frm.doctype,
		docname: frm.doc.name,
		restrictions: { allowed_file_types: [".xlsx", ".csv", ".txt"] },
		on_success(file) {
			frappe
				.call({ method: `${RDG}.upload_readings`, args: { cycle: frm.doc.name, file_url: file.file_url, source: source || "Import" },
				        freeze: true, freeze_message: __("Checking and applying the readings…") })
				.then(({ message: r }) => {
					const n = (v) => format_number(v || 0, null, 0);
					const SHOWN = 50; // a file can have hundreds of rows that weren't applied: the first are listed, the rest counted
					const rows = (r.errors || []).slice(0, SHOWN)
						.map((e) => `<tr><td>${e.row}</td><td>${frappe.utils.escape_html(e.reference_no)}</td><td>${frappe.utils.escape_html(e.error)}</td></tr>`)
						.join("");
					const more = (r.errors || []).length - SHOWN;
					// what to do next (the user, 2026-11-24: after the success message "what is the next step? i pressed save button but it
					// says no change in data"): the readings are in the cycle already, so nothing is saved here
					const next = [];
					if (r.flagged) next.push(__("Review the {0} flagged readings: Reading Entry (tick Show exceptions only) or Review Queue.", [n(r.flagged)]));
					if (r.to_enter) next.push(__("{0} accounts have no reading yet: upload or type them, or leave them for Estimate Unread.", [n(r.to_enter)]));
					if (r.not_read) next.push(__("{0} are marked as not read: Estimate Unread works out their units.", [n(r.not_read)]));
					next.push(r.to_enter || r.not_read ? __("Then, under Close: Estimate Unread, and Close Cycle.") : __("Then, under Close: Close Cycle."));
					frappe.msgprint({
						title: __("Readings uploaded"),
						indicator: r.errors.length ? "orange" : "green",
						message:
							`<p><b>${r.updated === 1 ? __("1 reading applied.") : __("{0} readings applied.", [n(r.updated)])}</b> ${__("They are in the cycle now; there is nothing to save.")}</p>` +
							(rows
								? `<p>${__("{0} rows were not applied:", [n(r.errors.length)])}</p><table class="table table-bordered"><tr><th>${__("Row")}</th><th>${__("Reference No")}</th><th>${__("Reason")}</th></tr>${rows}</table>` +
								  (more > 0 ? `<p>${__("…and {0} more, for the same kinds of reason.", [n(more)])}</p>` : "")
								: "") +
							`<p><b>${__("What to do next")}</b></p><ol>${next.map((x) => `<li>${x}</li>`).join("")}</ol>`,
					});
					frm.reload_doc();
				});
		},
	});
}
