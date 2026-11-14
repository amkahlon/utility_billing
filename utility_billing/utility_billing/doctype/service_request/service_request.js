// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Service requests are done step by step with the buttons here (FS-WF-00); the change is made when the last step is done.
frappe.ui.form.on("Service Request", {
	refresh(frm) {
		frm.dashboard.clear_headline();
		if (frm.is_new()) return;
		const status = frm.doc.status;
		const color = { Completed: "green", Rejected: "red", Cancelled: "red" }[status] || "blue";
		const step = (frm.doc.steps || []).find((s) => s.idx === frm.doc.current_step);
		let text = status === "Completed" ? frm.doc.result_note || __("Completed.") : status;
		if (step && ["Open", "In Progress"].includes(status)) {
			const late = step.due_date && frappe.datetime.get_diff(frappe.datetime.get_today(), step.due_date) > 0;
			text = __("Step {0} of {1}: {2}, by a {3}{4}.", [step.idx, frm.doc.steps.length, step.step_name, step.role, late ? __(" (late)") : ""]);
		}
		frm.dashboard.set_headline_alert(frappe.utils.escape_html(text), color);
		if (["Completed", "Rejected", "Cancelled"].includes(status)) frm.disable_form();
		if (!step || !["Open", "In Progress"].includes(status)) return;
		frm.add_custom_button(__("Done: {0}", [step.step_name]), () => do_step(frm, step, 0)).addClass("btn-primary");
		if (step.step_kind === "Approval") frm.add_custom_button(__("Reject"), () => do_step(frm, step, 1));
		frm.add_custom_button(__("Cancel Request"), () =>
			frappe.prompt([{ fieldname: "reason", fieldtype: "Small Text", label: __("Why?"), reqd: 1 }], (v) =>
				frappe.call({ method: "utility_billing.utility_billing.servicereq.cancel", args: { request: frm.doc.name, reason: v.reason }, freeze: true })
					.then(() => frm.reload_doc())
			)
		);
	},
});

function do_step(frm, step, reject) {
	const fields = [{ fieldname: "remarks", fieldtype: "Small Text", label: reject ? __("Why is it rejected?") : __("Remarks"), reqd: reject }];
	if (step.step_kind === "Field" && !reject) {
		fields.push(
			{ fieldname: "reading", fieldtype: "Float", label: __("Meter reading") },
			{ fieldname: "photo", fieldtype: "Attach Image", label: __("Photo") },
			{ fieldname: "latitude", fieldtype: "Float", label: __("Latitude"), precision: 6 },
			{ fieldname: "longitude", fieldtype: "Float", label: __("Longitude"), precision: 6 }
		);
	}
	frappe.prompt(
		fields,
		(v) =>
			frappe
				.call({ method: "utility_billing.utility_billing.servicereq.advance", args: { request: frm.doc.name, step: step.idx, reject, ...v }, freeze: true })
				.then(() => frm.reload_doc()),
		reject ? __("Reject {0}", [frm.doc.name]) : step.step_name,
		reject ? __("Reject") : __("Done")
	);
}
