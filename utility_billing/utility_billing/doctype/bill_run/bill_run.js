// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Bill Run", {
	refresh(frm) {
		utility_billing.setup_approval_form(frm);
		if (frm.is_new()) return;
		// FS-RUN-07: a large run posts in the background after approval
		if (frm.doc.posting_status === "Posting") {
			frm.dashboard.set_headline_alert(__("Posting the bills in the background. Reload in a few minutes."), "blue");
		} else if (frm.doc.posting_status === "Posting failed") {
			frm.dashboard.set_headline_alert(__("Posting stopped before every bill was posted. Fix the cause shown below, then Post Again: it carries on with the rest."), "red");
			if (frappe.user.has_role("Billing Manager")) {
				frm.add_custom_button(__("Post Again"), () =>
					frappe.xcall("utility_billing.utility_billing.doctype.bill_run.bill_run.retry_posting", { run: frm.doc.name }).then(() => frm.reload_doc()));
			}
		}
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
		if (editable) {
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
							if (message.background) {
								frappe.show_alert({ message: __("Calculating in the background; refresh in a minute."), indicator: "blue" });
							}
							frm.reload_doc();
						});
				frm.is_dirty() ? frm.save().then(go) : go();
			}).addClass("btn-primary");
		}
		if (frm.doc.calculation_status !== "Calculated" && editable) {
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
				{ fieldname: "block", fieldtype: "Select", label: __("Block"), options: [{ value: "", label: __("All blocks") }, ...options(g.blocks)] },
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
				{ fieldname: "block", fieldtype: "Select", label: __("Block"), options: [{ value: "", label: __("All blocks") }, ...options(g.blocks)] },
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
