// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.ui.form.on("Tariff", {
	setup(frm) {
		// Only categories of the selected utility.
		frm.set_query("consumer_category", () => ({
			filters: { utility: frm.doc.utility, enabled: 1 },
		}));
	},

	utility(frm) {
		frm.set_value("consumer_category", null);
	},

	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Preview Charge"), () => preview_charge(frm));
		}
		if (frm.doc.workflow_state === "Rejected" && frm.doc.rejection_reason) {
			frm.dashboard.set_headline_alert(
				`${__("Rejected")}: ${frappe.utils.escape_html(frm.doc.rejection_reason)}`,
				"red"
			);
		}
	},

	// Ask the approver for a reason before the Reject action is applied.
	before_workflow_action(frm) {
		if (frm.selected_workflow_action !== "Reject") return;
		return new Promise((resolve, reject) => {
			frappe.prompt(
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason for rejecting"),
					reqd: 1,
				},
				({ reason }) => {
					frappe
						.call(
							"utility_billing.utility_billing.doctype.tariff.tariff.set_rejection_reason",
							{ tariff: frm.doc.name, reason }
						)
						.then(() => resolve(), () => reject());
				},
				__("Reject Tariff"),
				__("Reject")
			);
		});
	},
});

function preview_charge(frm) {
	frappe.prompt(
		{ fieldname: "units", fieldtype: "Float", label: __("Units"), reqd: 1 },
		({ units }) => {
			frappe
				.call("utility_billing.utility_billing.doctype.tariff.tariff.preview_energy", {
					tariff: frm.doc.name,
					units,
				})
				.then(({ message: r }) => {
					const rows = r.lines
						.map(
							(l) =>
								`<tr><td>${format_number(l.units)}</td><td>${format_currency(
									l.rate
								)}</td><td style="text-align:right">${format_currency(l.amount)}</td></tr>`
						)
						.join("");
					const note = r.minimum_applied
						? `<p>${__("Minimum charge applied.")}</p>`
						: "";
					frappe.msgprint({
						title: __("Energy charge for {0} units", [format_number(r.units)]),
						message: `<table class="table table-bordered"><thead><tr><th>${__(
							"Units"
						)}</th><th>${__("Rate")}</th><th>${__(
							"Amount"
						)}</th></tr></thead><tbody>${rows}</tbody></table>${note}<p><b>${__(
							"Total"
						)}: ${format_currency(r.amount)}</b></p>`,
					});
				});
		},
		__("Preview Energy Charge"),
		__("Calculate")
	);
}
