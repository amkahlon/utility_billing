// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Bill Run", {
	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.is_new()) return;
		// FS-RUN-07: a large run posts in the background after approval; a progress bar shows how far it is (increment 54)
		if (frm.doc.posting_status === "Posting") {
			utility_billing.form_progress(frm, {
				method: "utility_billing.utility_billing.doctype.bill_run.bill_run.posting_progress",
				args: { run: frm.doc.name },
				title: __("Posting the bills"),
				failed_title: __("Posting didn't complete"),
			});
		} else if (frm.doc.posting_status === "Posting failed") {
			frm.dashboard.set_headline_alert(__("Posting stopped before every bill was posted. Fix the cause shown below, then Post Again: it carries on with the rest."), "red");
			if (frappe.perm.has_perm("Bill Run", 0, "submit")) {
				frm.add_custom_button(__("Post Again"), () =>
					frappe.xcall("utility_billing.utility_billing.doctype.bill_run.bill_run.retry_posting", { run: frm.doc.name }).then(() => frm.reload_doc()));
			}
		}
		if (frm.doc.docstatus === 1 && ["Reversing", "Reversal stopped"].includes(frm.doc.reversal_status)) show_reversal(frm);
		frm.add_custom_button(__("Open Wizard"), () => frappe.set_route("ub-billrun", { run: frm.doc.name }));
		frm.add_custom_button(__("Open Bills"), () => frappe.set_route("List", "Utility Bill", { bill_run: frm.doc.name }));
		if (frm.doc.bill_count && frm.doc.calculation_status === "Calculated" && frm.doc.docstatus < 2) {
			frm.add_custom_button(__("Print Bills"), () => print_run_bills(frm));
		}
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Record Delivery"), () => record_delivery(frm));
			frappe.call({
				method: "utility_billing.utility_billing.delivery.summary",
				args: { run: frm.doc.name },
				callback: ({ message: s }) => {
					const bits = Object.entries(s.by_status || {}).map(([k, n]) => `${__(k)}: ${n}`);
					if (bits.length) frm.dashboard.add_comment(__("Delivery of {0} bills: {1}", [s.total, bits.join(", ")]), "blue", true);
				},
			});
			if (!frm.doc.reversal_status) show_bill_messages(frm);
		}
		const editable = frm.doc.docstatus === 0 && ["Draft", "Rejected", "", undefined].includes(frm.doc.workflow_state);
		if (editable && frm.doc.calculation_status !== "Calculating") {
			frm.add_custom_button(__("Calculate Bills"), () => {
				const go = () =>
					frappe
						.call({
							method: "utility_billing.utility_billing.billing.calculate_run",
							args: { run: frm.doc.name },
							freeze: true,
							freeze_message: __("Calculating bills..."),
						})
						.then(({ message }) => {
							// a large run calculates in the background: the progress bar shows how far it is (increment 52)
							frm.reload_doc();
						});
				frm.is_dirty() ? frm.save().then(go) : go();
			}).addClass("btn-primary");
		}
		if (frm.doc.calculation_status === "Calculating") {
			watch_calculation(frm);
		} else if (frm.doc.calculation_status === "Failed" && editable) {
			frappe.xcall("utility_billing.utility_billing.billing.calculation_progress", { run: frm.doc.name }).then((p) =>
				frm.dashboard.set_headline_alert(
					`${__("The last calculation didn't complete.")} ${frappe.utils.escape_html((p && p.error) || "")}`, "red"));
		} else if (frm.doc.calculation_status !== "Calculated" && editable) {
			frm.dashboard.set_headline_alert(__("Bills need to be calculated before submitting for approval."), "orange");
		} else if ((frm.doc.problems || []).length) {
			frm.dashboard.set_headline_alert(
				__("{0} connections couldn't be billed; see Not Billed below.", [frm.doc.problems.length]),
				"orange"
			);
		}
	},
	before_workflow_action(frm) {
		const shared = utility_billing.before_workflow_action(frm); // asks the reason for a rejection or a cancellation
		if (frm.selected_workflow_action !== "Cancel") return shared;
		// increment 55: reversing a run undoes every bill one at a time, minutes for a whole society: a background job with a
		// progress bar, not a greyed screen (and not cut off by the server's time limit for a web request)
		return Promise.resolve(shared).then(() => {
			frappe.dom.unfreeze();
			reverse_run(frm);
			return Promise.reject(); // the workflow's own call doesn't run
		});
	},
});

function reverse_run(frm) {
	const name = frm.doc.name;
	utility_billing.run_job("run_cancel", { run: name }, __("Reversing bill run {0}", [name])).then(() => {
		frappe.show_alert({ message: __("Bill run {0} reversed.", [name]), indicator: "green" }, 7);
		frm.reload_doc();
	}, () => frm.reload_doc());
	setTimeout(() => frm.doc.name === name && frm.reload_doc(), 1500); // the run now says Reversing
}

// Increment 58: a run is reversed a batch of bills at a time. While it is going the form shows how far; if it stopped (an error, or
// nothing heard for 30 minutes) the bills reversed so far stay reversed, and Reverse Again carries on with the rest.
// Increment 62: how many posted bills have their "bill issued" message; the rest can be written (a background job with a progress
// bar). Posting writes them itself; this is for a run whose writing was stopped.
function show_bill_messages(frm) {
	frappe.xcall("utility_billing.utility_billing.notify.run_messages", { run: frm.doc.name }).then((m) => {
		if (!m.bills || frm.doc.name !== cur_frm.doc.name) return;
		const n = (v) => format_number(v, null, 0);
		if (!m.missing) return frm.dashboard.add_comment(__("Bill messages: all {0} written.", [n(m.bills)]), "green", true);
		if (m.writing) return frm.dashboard.add_comment(__("Bill messages are being written: {0} of {1} so far.", [n(m.written), n(m.bills)]), "blue", true);
		frm.dashboard.set_headline_alert(
			__("{0} of {1} bills have no bill message yet: their consumers haven't been told of the bill.", [n(m.missing), n(m.bills)]), "orange");
		if (!m.can_write) return;
		frm.add_custom_button(__("Write Bill Messages"), () =>
			frappe.confirm(__("Write the bill message for the {0} bills that have none? Bills that have one are left alone.", [n(m.missing)]), () =>
				utility_billing.run_job("bill_messages", { run: frm.doc.name }, __("Writing the bill messages of {0}", [frm.doc.name])).then((r) => {
					frappe.show_alert({ indicator: "green", message: __("{0} bill messages written; {1} of {2} bills have one.", [n(r.new), n(r.written), n(r.bills)]) }, 10);
					frm.reload_doc();
				})));
	});
}

function show_reversal(frm) {
	const name = frm.doc.name;
	frappe
		.xcall("utility_billing.utility_billing.progress.job_progress", { job: "run_cancel", record: name })
		.then((p) => {
			if (frm.doc.name !== name) return;
			if (frm.doc.reversal_status === "Reversing" && p && ["Queued", "Running"].includes(p.state)) {
				utility_billing.form_progress(frm, {
					method: "utility_billing.utility_billing.progress.job_progress",
					args: { job: "run_cancel", record: name },
					title: __("Reversing the bills"),
					failed_title: __("Reversing didn't complete"),
				});
				return;
			}
			const why = (p && p.state === "Failed" && p.error) || frm.doc.reversal_error || __("It stopped before every bill was reversed.");
			frm.dashboard.set_headline_alert(
				`${__("This run is only partly reversed.")} ${frappe.utils.escape_html(why)}`, "red");
			if (frappe.perm.has_perm("Bill Run", 0, "cancel") || frappe.perm.has_perm("Bill Run", 0, "submit")) {
				frm.add_custom_button(__("Reverse Again"), () => reverse_run(frm)).addClass("btn-danger");
			}
		});
}

// The calculation running in the background: a progress bar on the form, then the outcome (increment 52; it used to say "refresh
// in a minute", with nothing to show whether it was still going or had stopped).
const CALCULATING = __("Calculating bills");
function watch_calculation(frm) {
	const name = frm.doc.name;
	if (frm.ub_watching === name) return;
	frm.ub_watching = name;
	frm.dashboard.show_progress(CALCULATING, 2, __("Waiting to start…"));
	utility_billing.watch_calculation(
		name,
		(p) => frm.dashboard.show_progress(CALCULATING, utility_billing.calculation_percent(p), utility_billing.calculation_text(p)),
		(p) => {
			frm.ub_watching = null;
			frm.dashboard.hide_progress(CALCULATING);
			if (p.state === "Failed") {
				frappe.msgprint({ title: __("Calculation didn't complete"), indicator: "red", message: frappe.utils.escape_html(p.error || "") });
			} else {
				frappe.show_alert({ message: __("Bills calculated."), indicator: "green" }, 7);
			}
			frm.reload_doc();
		},
		() => {
			const here = cur_frm && cur_frm.doctype === "Bill Run" && cur_frm.doc.name === name && frappe.get_route()[0] === "Form";
			if (!here) frm.ub_watching = null; // asked again when the form is opened again
			return here;
		}
	);
}

// Record that a run's bills (all, or one block or route) were handed to a distributor, delivered, and so on (FS-RUN-10).
function record_delivery(frm) {
	const method = "utility_billing.utility_billing";
	frappe.call({ method: `${method}.bill_print.run_print_groups`, args: { run: frm.doc.name } }).then(({ message: g }) => {
		const options = (counts) => ub_sorted_entries(counts).map(([k, n]) => ({ value: k, label: `${k} (${n})` }));
		const d = new frappe.ui.Dialog({
			title: __("Record Delivery"),
			fields: [
				{
					fieldname: "status",
					fieldtype: "Select",
					label: __("Status"),
					reqd: 1,
					options: ["Handed to Distributor", "Delivered", "Returned", "Not Delivered", "Printed"],
					default: "Handed to Distributor",
				},
				{ fieldname: "block", fieldtype: "Select", label: __("Block"), options: [{ value: "", label: __("All blocks") }, ...options(g.blocks)],
					onchange: () => routes_for_block(d, g, options) },
				{ fieldname: "route", fieldtype: "Select", label: __("Route"), options: [{ value: "", label: __("All routes") }, ...options(g.routes)] },
				{ fieldname: "distributor", fieldtype: "Data", label: __("Distributor"), description: __("Required when handing bills over.") },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			],
			primary_action_label: __("Record"),
			primary_action(values) {
				d.hide();
				utility_billing.run_job("delivery", { run: frm.doc.name, ...values }, __("Recording delivery")).then((r) => {
					frappe.show_alert({ message: __("{0} of {1} bills updated.", [r.changed, r.bills]), indicator: "green" });
					frm.reload_doc();
				});
			},
		});
		d.show();
	});
}

// Blocks and routes in natural order: 01, 02 ... 10 ("10" first otherwise: a browser puts number-like keys before the rest).
function ub_sorted_entries(counts) {
	return Object.entries(counts).sort(([a], [b]) => (a === "(none)") - (b === "(none)") || a.localeCompare(b, undefined, { numeric: true }));
}

// Bulk print in delivery order, all bills or one block or route (FS-RUN-08). Increment 60: the walk order, route by route in each
// route's order with a cover sheet per route; accounts on no route after them, by block and house number.
function print_run_bills(frm) {
	const method = "utility_billing.utility_billing.bill_print";
	frappe.call({ method: `${method}.run_print_groups`, args: { run: frm.doc.name } }).then(({ message: g }) => {
		const options = (counts) => ub_sorted_entries(counts).map(([k, n]) => ({ value: k, label: `${k} (${n})` }));
		const d = new frappe.ui.Dialog({
			title: __("Print Bills"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p class="text-muted">${__(
						"Bills in this run: {0}. They print in walk order: route by route, each in its route's order, then the accounts on no route by block and house number. Up to {1} per PDF; more are printed in parts.",
						[format_number(g.total, null, 0), g.limit]
					)}</p>${g.routed ? "" : `<p class="text-warning">${__("None of these accounts is on a reading route yet, so they print by block and house number. Reading Routes > Walk Order sets up the walk.")}</p>`}`,
				},
				{ fieldname: "block", fieldtype: "Select", label: __("Block"), options: [{ value: "", label: __("All blocks") }, ...options(g.blocks)],
					onchange: () => { routes_for_block(d, g, options); parts_for_selection(d, g); } },
				{ fieldname: "route", fieldtype: "Select", label: __("Route"), options: [{ value: "", label: __("All routes") }, ...options(g.routes)],
					onchange: () => parts_for_selection(d, g) },
				{ fieldname: "part", fieldtype: "Select", label: __("Part"), hidden: 1,
					description: __("All parts are made in one go in the background; one part is quicker when you print a batch at a time.") },
				{ fieldname: "covers", fieldtype: "Check", label: __("A cover sheet before each route"), default: g.routed ? 1 : 0, hidden: g.routed ? 0 : 1,
					description: __("The route, its distributor and its bill count, so each pile is handed out as it comes off the printer.") },
			],
			primary_action_label: __("Make PDF"),
			primary_action(values) {
				d.hide();
				// background (increments 55, 56)
				utility_billing.print_run_bills(frm.doc.name, values.block, values.route, values.part, values.covers ? 1 : 0);
			},
		});
		parts_for_selection(d, g);
		d.show();
	});
}

// Increment 56: more bills than one PDF holds are printed in parts of g.limit, in delivery order: all parts, or one of them.
function parts_for_selection(d, g) {
	const block = d.get_value("block");
	const route = d.get_value("route");
	const count = block && route ? ((g.pairs || {})[block] || {})[route] || 0 : block ? g.blocks[block] || 0 : route ? g.routes[route] || 0 : g.total;
	const parts = Math.ceil(count / g.limit);
	const n = (v) => format_number(v, null, 0);
	const options = [{ value: "", label: __("All {0} parts ({1} bills)", [parts, n(count)]) }];
	for (let k = 1; k <= parts; k++) {
		options.push({ value: String(k), label: __("Part {0}: bills {1} to {2}", [k, n((k - 1) * g.limit + 1), n(Math.min(k * g.limit, count))]) });
	}
	d.set_df_property("part", "options", options);
	d.set_df_property("part", "hidden", parts > 1 ? 0 : 1);
	d.set_value("part", "");
}

// Choosing a block offers only that block's routes, with their bill counts; a route outside the block is cleared (user's request 2026-11-16).
function routes_for_block(d, g, options) {
	const block = d.get_value("block");
	const counts = block && g.pairs ? g.pairs[block] || {} : g.routes;
	d.set_df_property("route", "options", [{ value: "", label: __("All routes") }, ...options(counts)]);
	if (d.get_value("route") && !(d.get_value("route") in counts)) d.set_value("route", "");
}
