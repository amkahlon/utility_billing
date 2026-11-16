// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

const RDG_REVIEW = "utility_billing.utility_billing.readings";

frappe.ui.form.on("Meter Reading", {
	refresh(frm) {
		const s = frm.doc.status;
		const color = { Flagged: "orange", "Re-read Requested": "orange", Estimated: "blue", Valid: "green", Approved: "green" }[s];
		if (frm.doc.checks && color) {
			frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.checks).replace(/\n/g, "<br>"), color);
		}
		if (frm.is_new()) return;
		// Readings in a closed cycle are locked: read-only form and no review actions.
		frappe.db.get_value("Reading Cycle", frm.doc.reading_cycle, "status").then(({ message }) => {
			if (message && message.status === "Closed") {
				frm.disable_form();
			} else {
				add_review_buttons(frm);
			}
		});
	},
});

function add_review_buttons(frm) {
	{
		const s = frm.doc.status;
		const reviewer = utility_billing.can("readings.review");
		if (!reviewer) return;
		if (s === "Flagged") {
			frm.add_custom_button(__("Approve as Read"), () =>
				ask(__("Approve reading"), "note", __("Why is this reading correct?"), (v) =>
					call(frm, "approve_reading", { name: frm.doc.name, note: v.note })
				),
				__("Review")
			);
		}
		if (["Flagged", "Valid"].includes(s)) {
			frm.add_custom_button(__("Correct Reading"), () => {
				const d = new frappe.ui.Dialog({
					title: __("Correct reading"),
					fields: [
						{ fieldname: "present_reading", fieldtype: "Float", label: __("Correct Present Reading"), reqd: 1, default: frm.doc.present_reading },
						{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
					],
					primary_action_label: __("Correct"),
					primary_action(v) {
						d.hide();
						call(frm, "correct_reading", { name: frm.doc.name, present_reading: v.present_reading, reason: v.reason });
					},
				});
				d.show();
			}, __("Review"));
			frm.add_custom_button(__("Request Re-read"), () =>
				ask(__("Request re-read"), "note", __("What should the reader check?"), (v) =>
					call(frm, "request_reread", { name: frm.doc.name, note: v.note })
				),
				__("Review")
			);
		}
	}
}

function ask(title, fieldname, label, then) {
	frappe.prompt({ fieldname, fieldtype: "Small Text", label, reqd: 1 }, then, title, __("Save"));
}

function call(frm, method, args) {
	frappe.call({ method: `${RDG_REVIEW}.${method}`, args, freeze: true }).then(() => frm.reload_doc());
}
