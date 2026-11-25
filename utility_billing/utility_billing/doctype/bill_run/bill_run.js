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
		return utility_billing.before_workflow_action(frm);
	},
});

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
		const options = (counts) => Object.entries(counts).map(([k, n]) => ({ value: k, label: `${k} (${n})` }));
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
				frappe
					.call({ method: `${method}.delivery.mark`, args: { run: frm.doc.name, ...values }, freeze: true })
					.then(({ message: r }) => {
						frappe.show_alert({ message: __("{0} of {1} bills updated.", [r.changed, r.bills]), indicator: "green" });
						d.hide();
						frm.reload_doc();
					});
			},
		});
		d.show();
	});
}

// Bulk print in delivery order (block, route, property), all bills or one block or route (FS-RUN-08).
function print_run_bills(frm) {
	const method = "utility_billing.utility_billing.bill_print";
	frappe.call({ method: `${method}.run_print_groups`, args: { run: frm.doc.name } }).then(({ message: g }) => {
		const options = (counts) => Object.entries(counts).map(([k, n]) => ({ value: k, label: `${k} (${n})` }));
		const d = new frappe.ui.Dialog({
			title: __("Print Bills"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p class="text-muted">${__(
						"Bills in this run: {0}. They print in delivery order (block, route, property number), up to {1} per PDF.",
						[g.total, g.limit]
					)}</p>`,
				},
				{ fieldname: "block", fieldtype: "Select", label: __("Block"), options: [{ value: "", label: __("All blocks") }, ...options(g.blocks)],
					onchange: () => routes_for_block(d, g, options) },
				{ fieldname: "route", fieldtype: "Select", label: __("Route"), options: [{ value: "", label: __("All routes") }, ...options(g.routes)] },
			],
			primary_action_label: __("Open PDF"),
			primary_action(values) {
				const args = new URLSearchParams({ run: frm.doc.name, block: values.block || "", route: values.route || "" });
				window.open(`/api/method/${method}.print_run_bills?${args}`);
				if (frm.doc.docstatus === 1) {
					frappe.call({
						method: "utility_billing.utility_billing.delivery.mark",
						args: { run: frm.doc.name, status: "Printed", block: values.block || "", route: values.route || "" },
						silent: true,
					});
				}
				d.hide();
			},
		});
		d.show();
	});
}

// Choosing a block offers only that block's routes, with their bill counts; a route outside the block is cleared (user's request 2026-11-16).
function routes_for_block(d, g, options) {
	const block = d.get_value("block");
	const counts = block && g.pairs ? g.pairs[block] || {} : g.routes;
	d.set_df_property("route", "options", [{ value: "", label: __("All routes") }, ...options(counts)]);
	if (d.get_value("route") && !(d.get_value("route") in counts)) d.set_value("route", "");
}
