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
		show_energy_pricing(frm);
	},

	refresh(frm) {
		if (!frm.is_new()) frm.add_custom_button(__("Open in Tariff Editor"), () => frappe.set_route("ub-tariff", { name: frm.doc.name }));
		show_energy_pricing(frm).then((metered) => {
			if (frm.doc.docstatus === 1 && metered) {
				frm.add_custom_button(__("Preview Charge"), () => preview_charge(frm));
			}
		});
		utility_billing.add_impact_button(frm, "tariff_impact");
		utility_billing.render_approval_history(frm);
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
		return ask_rejection_reason(frm);
	},
});

// Frappe freezes the screen before calling before_workflow_action, which leaves a dialog
// greyed out underneath the freeze (found by the user on 2026-11-13). Lift the freeze while the
// dialog is open; re-freeze only when the reason is saved and the action continues.
function ask_rejection_reason(frm) {
	frappe.dom.unfreeze();
	return new Promise((resolve, reject) => {
		let saved = false;
		const d = new frappe.ui.Dialog({
			title: __("Reject Tariff"),
			fields: [
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason for rejecting"),
					description: __("The submitter sees this reason and can correct and resubmit."),
					reqd: 1,
				},
			],
			primary_action_label: __("Reject"),
			primary_action({ reason }) {
				frappe
					.call({
						method: "utility_billing.utility_billing.doctype.tariff.tariff.set_rejection_reason",
						args: { tariff: frm.doc.name, reason },
					})
					.then(() => {
						saved = true;
						d.hide();
						frappe.dom.freeze();
						resolve();
					});
			},
		});
		d.onhide = () => {
			if (!saved) {
				frm.selected_workflow_action = null;
				reject(); // cancelled: the screen stays unfrozen and nothing changes
			}
		};
		d.show();
	});
}

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

// A service without a meter (maintenance, by plot size) has no units to price: no energy pricing section (increment 22).
function show_energy_pricing(frm) {
	const apply = (metered) => {
		frm.toggle_display(["pricing_section", "slabs"], metered);
		frm.toggle_reqd("flat_rate", metered && frm.doc.pricing_method === "Flat rate");
		return metered;
	};
	if (!frm.doc.utility) return Promise.resolve(apply(true));
	return frappe.db.get_value("Utility Type", frm.doc.utility, "is_metered").then((r) => apply(!!(r.message && r.message.is_metered)));
}
