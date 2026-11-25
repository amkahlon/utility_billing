// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Opening Balance Import: load the file, see the problems, download them to fix at source (gap 9).
// A large import (increment 48): the form carries its first rows only, and Submit for Approval, Approve, Reject and Withdraw run
// in a background job whose progress is shown here, instead of a greyed screen (user, 2026-11-24).
const OB = "utility_billing.utility_billing.opening.";
const WORKING = __("Working on the opening balances");

function shown_of(frm) {
	const o = frm.doc.__onload || {};
	return o.rows_total ? { shown: o.rows_shown, total: o.rows_total } : null;
}

frappe.ui.form.on("Opening Balance Import", {
	refresh(frm) {
		const part = shown_of(frm);
		frm.add_custom_button(__("Download Template"), () => window.open(`/api/method/${OB}template`), __("File"));
		if (!frm.is_new() && frm.doc.docstatus === 0 && ["Draft", "Rejected", undefined, ""].includes(frm.doc.workflow_state)) {
			frm.add_custom_button(__("Load File"), () => {
				const load = () =>
					utility_billing.run_job("opening_load", { name: frm.doc.name, file_url: frm.doc.import_file }, __("Loading and checking the file"))
						.then((m) => {  // in the background with a progress bar (increment 55)
							frm.reload_doc();
							frappe.show_alert({ message: __("{0} accounts loaded, {1} with problems", [m.rows, m.errors]), indicator: m.errors ? "orange" : "green" });
						});
				// a large import isn't saved from the form (it shows the first rows only): the file is handed over with the call
				return part ? load() : frm.save().then(load);
			}, __("File"));
		}
		if (part) {
			frm.disable_save();
			frm.set_df_property("rows", "read_only", 1);
			frm.set_df_property("rows", "description",
				__("The first {0} of {1} accounts are shown. The totals and the problem count are of all of them.", [format_number(part.shown, null, 0), format_number(part.total, null, 0)]));
		}
		if (frm.doc.error_count) {
			frm.add_custom_button(__("Download Problems"), () => window.open(`/api/method/${OB}error_report?name=${encodeURIComponent(frm.doc.name)}`), __("File"));
			frm.dashboard.set_headline(__("{0} of {1} rows have problems: fix them in the file and load it again.", [frm.doc.error_count, frm.doc.row_count]), "orange");
		} else if (frm.doc.row_count && frm.doc.docstatus === 0 && ["Draft", "Rejected", undefined, ""].includes(frm.doc.workflow_state)) {
			frm.dashboard.set_headline(__("All {0} accounts checked. Submit for approval when the totals agree with the previous system.", [frm.doc.row_count]), "green");
		}
		if (utility_billing.setup_approval_form) utility_billing.setup_approval_form(frm);
		show_step(frm, (frm.doc.__onload || {}).progress);
	},

	before_workflow_action(frm) {
		const shared = utility_billing.before_workflow_action ? utility_billing.before_workflow_action(frm) : null; // a reason for a rejection, approvals in turn
		return Promise.resolve(shared).then(() => {
			if (!shown_of(frm)) return; // a small import: the step runs at once, as before
			const action = frm.selected_workflow_action;
			return frappe.xcall(OB + "act", { name: frm.doc.name, action }).then(
				(r) => {
					frappe.dom.unfreeze();
					if (r.queued) show_step(frm, { state: "Queued", action, text: __("Waiting to start"), done: 0, total: 0 });
					else frm.reload_doc();
					return Promise.reject(); // the workflow's own call, which would send the form's rows, doesn't run
				},
				(e) => {
					frappe.dom.unfreeze();
					return Promise.reject(e);
				}
			);
		});
	},
});

// The step running on the import: a progress bar while it runs, the outcome when it ends.
function show_step(frm, p) {
	clearTimeout(frm.ub_ob_timer);
	if (!p) return;
	const name = frm.doc.name;
	const again = () => {
		frm.ub_ob_timer = setTimeout(() => {
			if (!cur_frm || cur_frm.doctype !== "Opening Balance Import" || cur_frm.doc.name !== name) return; // the user went elsewhere
			frappe.xcall(OB + "progress", { name }).then((now) => show_step(frm, now || p), again);
		}, 2000);
	};
	if (p.state === "Queued" || p.state === "Running") {
		frm.ub_ob_running = true;
		const percent = p.total ? Math.max(3, Math.round((p.done / p.total) * 100)) : p.state === "Queued" ? 2 : 5;
		const count = p.total ? `: ${format_number(p.done, null, 0)} ${__("of")} ${format_number(p.total, null, 0)}` : "";
		frm.dashboard.show_progress(WORKING, percent, `${__(p.action)} · ${p.text || ""}${count}`);
		frm.page.clear_actions_menu(); // one step at a time
		again();
	} else if (frm.ub_ob_running) {
		frm.ub_ob_running = false;
		frm.dashboard.hide_progress(WORKING);
		if (p.state === "Done") frappe.show_alert({ message: p.text, indicator: "green" }, 10);
		else frappe.msgprint({ title: __("{0} didn't complete", [__(p.action)]), indicator: "red", message: `${frappe.utils.escape_html(p.error || "")}<br><br>${__("Nothing was changed.")}` });
		frm.reload_doc();
	} else if (p.state === "Failed") {
		frm.dashboard.set_headline_alert(`${__("{0} didn't complete", [__(p.action)])}: ${frappe.utils.escape_html(p.error || "")}`, "red");
	}
}
