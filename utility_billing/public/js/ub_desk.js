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

	// ---------------------------------------------------------------- 5. filters that follow the one chosen before them
	// User's request (2026-11-16): "if a value is selected in a filter, the next filters should offer only what belongs to it ...
	// application wide". A picker for records carrying a society, utility or feeder (frappe.boot.ub_link_parents) offers only those
	// matching the value already chosen on the same list, report, page or form; changing that value clears a choice that no longer
	// fits; block and sector boxes offer the chosen society's values. A picker a screen already narrows itself is left alone.
	const PARENTS = ["society", "utility", "feeder"];
	const KINDS = { block: "block", sector: "sector" };
	const parents_of = (doctype) => (frappe.boot.ub_link_parents || {})[doctype] || [];
	utility_billing.link_filters = (doctype, get) => {
		const filters = {};
		parents_of(doctype).forEach((p) => {
			const v = get(p);
			if (v) filters[p] = v;
		});
		return filters;
	};

	// a dependent Link whose record no longer matches its new parent value is cleared
	function clear_unfit(fields, parent, value, set) {
		fields.forEach(({ fieldname, doctype, current }) => {
			if (!current || !parents_of(doctype).includes(parent)) return;
			if (!value) return;
			frappe.db.get_value(doctype, current, parent).then((r) => {
				if (r && r.message && r.message[parent] && r.message[parent] !== value) set(fieldname, "");
			});
		});
	}

	function patch_page_fields() {
		const P = frappe.ui.Page && frappe.ui.Page.prototype;
		if (!P || P.__ub_cascade) return;
		P.__ub_cascade = true;
		const add_field = P.add_field;
		P.add_field = function (df, wrapper) {
			const page = this;
			const kind = KINDS[df.fieldname];
			if (kind && df.fieldtype === "Data") Object.assign(df, { fieldtype: "Autocomplete", options: [] });
			const f = add_field.call(this, df, wrapper);
			const get = (p) => page.fields_dict[p] && page.fields_dict[p].get_value();
			if (df.fieldtype === "Link" && !df.get_query && !f.get_query && parents_of(df.options).length) {
				f.get_query = () => ({ filters: utility_billing.link_filters(df.options, get) });
			}
			if (kind && f.$input) {
				// the society's blocks or sectors, looked up each time the box is opened (the society may have changed)
				f.$input.on("focus", () =>
					frappe.xcall("utility_billing.utility_billing.choices.values", { kind, society: get("society") || null }).then((opts) => f.set_data && f.set_data(opts))
				);
			}
			if (PARENTS.includes(df.fieldname) && f.$wrapper) {
				f.$wrapper.on("change", "input, select", () =>
					setTimeout(() => {
						const fields = Object.values(page.fields_dict || {})
							.filter((x) => x.df && x.df.fieldtype === "Link" && x !== f)
							.map((x) => ({ fieldname: x.df.fieldname, doctype: x.df.options, current: x.get_value() }));
						clear_unfit(fields, df.fieldname, f.get_value(), (fn, v) => page.fields_dict[fn].set_value(v));
					}, 0)
				);
			}
			return f;
		};
	}

	const bound_parents = new Set();
	function cascade_form(frm) {
		if (!frm || !frm.meta || frm.meta.istable) return;
		const own = (fieldname) => frm.fields_dict[fieldname];
		frm.meta.fields.forEach((df) => {
			if (df.fieldtype === "Link" && parents_of(df.options).length) {
				const field = own(df.fieldname);
				if (field && !field.get_query && !df.get_query) {
					frm.set_query(df.fieldname, () => ({ filters: utility_billing.link_filters(df.options, (p) => frm.doc[p]) }));
				}
			}
			if (df.fieldtype === "Table" && own(df.fieldname) && own(df.fieldname).grid) {
				const grid = own(df.fieldname).grid;
				(frappe.get_meta(df.options) || { fields: [] }).fields.forEach((cdf) => {
					if (cdf.fieldtype !== "Link" || !parents_of(cdf.options).length) return;
					const gf = grid.get_field && grid.get_field(cdf.fieldname);
					if (gf && gf.get_query) return;
					frm.set_query(cdf.fieldname, df.fieldname, (doc, cdt, cdn) => {
						const row = (locals[cdt] || {})[cdn] || {};
						return { filters: utility_billing.link_filters(cdf.options, (p) => row[p] || frm.doc[p]) };
					});
				});
			}
		});
		// changing a parent clears the form's links that no longer belong to it
		PARENTS.filter((p) => frm.meta.fields.some((df) => df.fieldname === p)).forEach((p) => {
			const key = `${frm.doctype}:${p}`;
			if (bound_parents.has(key)) return;
			bound_parents.add(key);
			frappe.ui.form.on(frm.doctype, p, (f) => {
				const fields = f.meta.fields
					.filter((df) => df.fieldtype === "Link" && df.fieldname !== p && !df.read_only)
					.map((df) => ({ fieldname: df.fieldname, doctype: df.options, current: f.doc[df.fieldname] }));
				clear_unfit(fields, p, f.doc[p], (fn, v) => f.set_value(fn, v));
			});
		});
	}

	// ---------------------------------------------------------------- 6. our own About (increment 42)
	// User, 2026-11-21: "user should not know what platform or open source tool we have used to build this application". The
	// platform's About dialog (opened from the search bar or a keyboard shortcut) names it and its versions; ours names the product.
	function about() {
		const p = frappe.boot.ub_product || { name: "Utility Management System", publisher: "SoftVerx", version: "" };
		const d = new frappe.ui.Dialog({ title: __("About") });
		$(d.body).html(`<div class="ub-about">
			<img src="/assets/utility_billing/images/utility-billing-logo.svg" alt="" style="width:48px;height:48px;margin-bottom:12px">
			<h4 style="margin:0 0 4px">${frappe.utils.escape_html(__(p.name))}</h4>
			<p class="text-muted" style="margin:0 0 12px">${__("Version {0}", [frappe.utils.escape_html(p.version || "")])}</p>
			<p class="text-muted" style="margin:0">© ${new Date().getFullYear()} ${frappe.utils.escape_html(p.publisher)}. ${__("All rights reserved.")}</p>
		</div>`);
		d.show();
	}
	function patch_about() {
		frappe.provide("frappe.ui.misc");
		frappe.ui.misc.about = about;
	}
	patch_about();
	$(document).on("startup", patch_about);

	// ---------------------------------------------------------------- 7. one banner per message (increment 43)
	// User, 2026-11-21: the Court Hold form showed its "On approval ..." banner twice. Frappe's set_headline_alert adds a banner each
	// time it is called and a form refreshes more than once (after saving, after the approval panel loads), so a form that sets its
	// banner in refresh stacked copies. A banner with the same text now replaces the earlier one, on every form.
	function patch_headline() {
		const D = frappe.ui.form && frappe.ui.form.Dashboard;
		if (!D || D.prototype.__ub_headline) return;
		D.prototype.__ub_headline = true;
		const set_headline_alert = D.prototype.set_headline_alert;
		D.prototype.set_headline_alert = function (text, ...args) {
			const box = this.frm && this.frm.layout && this.frm.layout.message;
			if (text && box) {
				const same = $("<div>").html(text).text().trim();
				box.children(".form-message").filter((_i, el) => $(el).text().trim() === same).remove();
			}
			return set_headline_alert.call(this, text, ...args);
		};
	}

	// the form code is loaded with the first form, after this file: patch it then, and place the button for that first form
	$(document).on("form-refresh", (e, frm) => {
		patch_headline();
		patch_toolbar();
		bind_choices();
		cancel_button(frm);
		if (CHOICES[frm.doctype]) fill_choices(frm);
		cascade_form(frm);
	});
	$(patch_headline);
	$(document).on("startup", patch_headline);
	$(patch_page_fields);
	$(document).on("startup", patch_page_fields);
	$(patch_toolbar);
	$(document).on("startup", patch_list);
	$(patch_list);
	frappe.router && frappe.router.on && frappe.router.on("change", patch_list);
})();
