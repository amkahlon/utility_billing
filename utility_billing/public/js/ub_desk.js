// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Desk-wide behaviour for the app's lists and forms (bug report 2026-11-16):
//  1. a workflow action on ticked records (Actions › Submit for Approval, Approve, ...) says what it did, clears the ticks and
//     refreshes the list; each action is offered once;
//  2. coming back to a list starts with nothing ticked;
//  3. a new or changed record has a Cancel button beside Save that leaves without saving (every form, the app's and Frappe's);
//  4. block, sector and office boxes offer the society's saved values (utility_billing.choices) instead of free typing.

// Action rights (increment 25): what the user may do, from frappe.boot.ub_rights (utility_billing.rights.mine).
frappe.provide("utility_billing");
utility_billing.can = (action) => frappe.user.has_role("System Manager") || (frappe.boot.ub_rights || []).includes(action);

(function () {
	const MODULE = "Utility Billing";
	const ours = (doctype) => (frappe.get_meta(doctype) || {}).module === MODULE;

	function report(r, doctype) {
		const label = __(r.action);
		const lines = [];
		if (r.done.length) lines.push(`<p><b>${__("{0}: {1} record(s) done.", [label, r.done.length])}</b></p>`);
		if (r.failed.length) {
			lines.push(`<p><b>${__("{0} record(s) not done:", [r.failed.length])}</b></p><ul>${r.failed
				.map((f) => `<li>${frappe.utils.get_form_link(doctype, f.name, true)}: ${frappe.utils.escape_html(f.reason || "")}</li>`)
				.join("")}</ul>`);
		}
		if (!r.failed.length) {
			frappe.show_alert({ message: __("{0}: {1} record(s) done.", [label, r.done.length]), indicator: "green" }, 6);
		} else {
			frappe.msgprint({ title: __("{0} on the ticked records", [label]), message: lines.join(""), indicator: r.done.length ? "orange" : "red" });
		}
	}

	function run_bulk(list, action) {
		const doctype = list.doctype;
		const names = list.get_checked_items(true);
		if (!names.length) return;
		const go = (reason) => {
			const before = reason
				? Promise.all(names.map((name) => frappe.xcall("utility_billing.utility_billing.approval.set_rejection_reason", { doctype, name, reason })))
				: Promise.resolve();
			list.disable_list_update = true;
			before
				.then(() =>
					frappe.xcall(
						"utility_billing.utility_billing.approval.bulk_action",
						{ doctype, docnames: names, action },
						"POST",
						{ freeze: true, freeze_message: __("{0}: {1} record(s)...", [__(action), names.length]) }
					)
				)
				.then((r) => {
					report(r, doctype);
					list.clear_checked_items();
					list.refresh();
				})
				.finally(() => (list.disable_list_update = false));
		};
		if (action === "Reject" && ours(doctype)) {
			frappe.prompt({ fieldname: "reason", fieldtype: "Small Text", label: __("Reason for rejecting"), reqd: 1 }, (v) => go(v.reason),
				__("Reject {0} record(s)", [names.length]), __("Reject"));
		} else {
			frappe.confirm(__("{0} the {1} ticked record(s)?", [__(action), names.length]), () => go());
		}
	}

	function patch_list() {
		const LV = frappe.views && frappe.views.ListView;
		if (!LV || LV.prototype.__ub_patched) return;
		LV.prototype.__ub_patched = true;

		LV.prototype.get_workflow_action_menu_items = function () {
			if (!frappe.model.has_workflow(this.doctype)) return [];
			const actions = [...new Set(frappe.workflow.get_all_transition_actions(this.doctype))];
			return actions.map((action) => ({
				label: __(action),
				name: action,
				action: () => run_bulk(this, action),
				is_workflow_action: true,
			}));
		};

		const show = LV.prototype.show;
		LV.prototype.show = function (...args) {
			if (this.checked_docnames && this.checked_docnames.size && this.$result) this.clear_checked_items();
			return show.apply(this, args);
		};
	}

	function cancel_button(frm) {
		if (!frm || !frm.meta || frm.meta.istable || frm.meta.issingle) return;
		if (!(frm.is_new() || (frm.is_dirty() && frm.doc.docstatus === 0))) return;
		// beside Save: Frappe hides the custom-button bar until a new record is saved
		frm.page.set_secondary_action(__("Cancel"), () => {
			const leave = () => {
				if (frm.is_new()) {
					frappe.model.remove_from_locals(frm.doctype, frm.docname);
					frappe.set_route("List", frm.doctype);
				} else {
					frm.reload_doc();
				}
			};
			frm.is_dirty() ? frappe.confirm(__("Leave without saving? What you entered is not kept."), leave) : leave();
		});
	}

	// Frappe clears the page's buttons each time it sets Save (on refresh and when a form becomes dirty), so Cancel follows it
	function patch_toolbar() {
		const TB = frappe.ui.form && frappe.ui.form.Toolbar;
		if (!TB || TB.prototype.__ub_patched) return;
		TB.prototype.__ub_patched = true;
		const set_primary_action = TB.prototype.set_primary_action;
		TB.prototype.set_primary_action = function (...args) {
			const out = set_primary_action.apply(this, args);
			try {
				cancel_button(this.frm);
			} catch (e) {
				console.warn(e);
			}
			return out;
		};
	}

	// Drop-downs of saved values: {doctype: [[fieldname, kind, child table or null]]}; kinds as in utility_billing.choices.
	const CHOICES = {
		Property: [["block", "block"], ["sector", "sector"]],
		Broadcast: [["block", "block"]],
		"Bulk Adjustment": [["block", "block"]],
		"Recovery Run": [["block", "block"]],
		"Cash Counter": [["office", "office"]],
		"User Access": [["block", "block", "blocks"]],
	};

	function fill_choices(frm) {
		(CHOICES[frm.doctype] || []).forEach(([field, kind, table]) => {
			const society = table ? null : frm.doc.society;
			frappe.xcall("utility_billing.utility_billing.choices.values", { kind, society }).then((options) => {
				if (table) {
					const grid = frm.fields_dict[table] && frm.fields_dict[table].grid;
					grid && grid.update_docfield_property(field, "options", options);
				} else if (frm.fields_dict[field]) {
					frm.set_df_property(field, "options", options);
				}
			});
		});
	}
	let choices_bound = false;
	function bind_choices() {
		if (choices_bound || !(frappe.ui.form && frappe.ui.form.on)) return;
		choices_bound = true;
		Object.keys(CHOICES).forEach((doctype) => frappe.ui.form.on(doctype, { society: fill_choices }));
	}

	// the form code is loaded with the first form, after this file: patch it then, and place the button for that first form
	$(document).on("form-refresh", (e, frm) => {
		patch_toolbar();
		bind_choices();
		cancel_button(frm);
		if (CHOICES[frm.doctype]) fill_choices(frm);
	});
	$(patch_toolbar);
	$(document).on("startup", patch_list);
	$(patch_list);
	frappe.router && frappe.router.on && frappe.router.on("change", patch_list);
})();
