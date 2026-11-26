// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Reading Route (increment 60, walk order): the rows are the walk, for reading meters and for printing and delivering bills.
// A row is moved by dragging it; Move Accounts moves many at once, from other routes too, to the start, the end or after an account.

frappe.ui.form.on("Reading Route", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.set_intro(__("The rows are the walk: meters are read and bills printed and delivered in this order. Drag a row to move it, or use Move Accounts for many at once."), "blue");
		if (frm.perm[0] && frm.perm[0].write) frm.add_custom_button(__("Move Accounts"), () => ub_move_accounts(frm));
	},
});

function ub_move_accounts(frm) {
	if (frm.is_dirty()) return frappe.msgprint(__("Save the route first."));
	if (!frm.doc.enabled) return frappe.msgprint(__("This route isn't in use. Tick In Use and save first."));
	const d = new frappe.ui.Dialog({
		title: __("Move Accounts onto Route {0}", [frm.doc.route_code]),
		fields: [
			{ fieldname: "accounts", fieldtype: "Small Text", label: __("Reference numbers"), reqd: 1,
				description: __("One per line, or separated by commas, in the order they are walked. They come off the routes they are on now.") },
			{ fieldname: "where", fieldtype: "Select", label: __("Put them"), default: "end",
				options: [{ value: "end", label: __("At the end") }, { value: "start", label: __("At the start") }, { value: "after", label: __("After an account") }] },
			{ fieldname: "after", fieldtype: "Data", label: __("After account (reference number)"),
				depends_on: "eval:doc.where=='after'", mandatory_depends_on: "eval:doc.where=='after'" },
		],
		primary_action_label: __("Move"),
		primary_action(v) {
			frappe.call({
				method: "utility_billing.utility_billing.walk.move",
				args: { route: frm.doc.name, accounts: v.accounts, where: v.where, after: v.where === "after" ? v.after : "" },
				freeze: true, freeze_message: __("Moving the accounts…"),
			}).then(({ message: r }) => {
				d.hide();
				frm.reload_doc();
				const places = Object.values(r.position || {});
				frappe.show_alert({ indicator: "green", message: places.length > 1
					? __("{0} accounts moved: now at places {1} to {2}.", [r.moved, Math.min(...places), Math.max(...places)])
					: __("Moved: now at place {0}.", [places[0]]) }, 8);
			});
		},
	});
	d.show();
}
