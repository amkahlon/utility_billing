// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Reading Routes (increment 60, walk order): a first walk from the reference numbers, a walk sheet to download, and the corrected
// sheet to import. Reference numbers never change; the walk is the accounts' places on the routes.

frappe.listview_settings["Reading Route"] = {
	onload(listview) {
		const group = __("Walk Order");
		listview.page.add_inner_button(__("Download Walk Sheet"), () => ub_walk_sheet(), group);
		if (!frappe.model.can_create("Reading Route")) return;
		listview.page.add_inner_button(__("Build from Reference Numbers"), () => ub_walk_build(listview), group);
		listview.page.add_inner_button(__("Import Walk Order"), () => ub_walk_import(listview), group);
	},
};

const UB_WALK = "utility_billing.utility_billing.walk";
const ub_walk_where = () => [
	{ fieldname: "society", fieldtype: "Link", label: __("Society"), options: "Society", reqd: 1 },
	{ fieldname: "utility", fieldtype: "Link", label: __("Utility"), options: "Utility Type", reqd: 1, default: "Electricity" },
];

function ub_walk_sheet() {
	const d = new frappe.ui.Dialog({
		title: __("Download Walk Sheet"),
		fields: [
			{ fieldtype: "HTML", options: `<p class="text-muted">${__("Every account with its route and place, in walk order (accounts on no route at the end). Correct it in Excel and import it again.")}</p>` },
			...ub_walk_where(),
		],
		primary_action_label: __("Download"),
		primary_action(v) {
			d.hide();
			window.open(`/api/method/${UB_WALK}.walk_sheet?${new URLSearchParams({ society: v.society, utility: v.utility })}`);
		},
	});
	d.show();
}

function ub_walk_build(listview) {
	const n = (v) => format_number(v, null, 0);
	const e = frappe.utils.escape_html;
	const d = new frappe.ui.Dialog({
		title: __("Build Routes from Reference Numbers"),
		size: "large",
		fields: [
			{ fieldtype: "HTML", options: `<p class="text-muted">${__("For a society migrated from an older system: the accounts on no route are grouped by the leading digits of their reference numbers (the old batches), one route per group, in reference-number order. The reference numbers don't change. Correct the order afterwards on each route, or with a walk sheet.")}</p>` },
			...ub_walk_where().map((f) => ({ ...f, onchange: () => preview() })),
			{ fieldname: "digits", fieldtype: "Int", label: __("Group by the first … digits"), default: 2, reqd: 1, onchange: () => preview() },
			{ fieldname: "plan", fieldtype: "HTML" },
		],
		primary_action_label: __("Build Routes"),
		primary_action(v) {
			d.hide();
			utility_billing.run_job("walk_build", { society: v.society, utility: v.utility, digits: v.digits }, __("Building routes for {0}", [v.society]))
				.then((r) => {
					listview.refresh();
					frappe.msgprint({ title: __("Routes built"), indicator: "green",
						message: __("{0} accounts put on routes: {1} routes made, {2} added to.", [n(r.accounts), r.created, r.extended]) });
				});
		},
	});
	let asked = 0;
	function preview() {
		const v = d.get_values(true);
		const box = d.fields_dict.plan.$wrapper;
		if (!v.society || !v.utility || !v.digits) return box.empty();
		const mine = ++asked;
		frappe.xcall(`${UB_WALK}.preview_build`, { society: v.society, utility: v.utility, digits: v.digits }).then((p) => {
			if (mine !== asked) return;
			const rows = p.groups.slice(0, 60).map((g) => `<tr><td>${e(g.code)}</td><td class="text-right">${n(g.accounts)}</td>
				<td>${g.existing ? (g.in_use ? __("added at the end of the route") : `<span class="text-danger">${__("route not in use")}</span>`) : __("new route")}</td></tr>`).join("");
			box.html(`<p>${__("{0} of {1} accounts are on no route; they make {2} routes.", [n(p.unrouted), n(p.accounts), p.groups.length])}</p>
				${p.groups.length ? `<table class="table table-sm table-bordered"><thead><tr><th>${__("Route")}</th><th class="text-right">${__("Accounts")}</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : ""}
				${p.groups.length > 60 ? `<p class="text-muted">${__("… and {0} more. Group by fewer digits for fewer, larger routes.", [p.groups.length - 60])}</p>` : ""}`);
		}).catch(() => box.empty());
	}
	d.show();
}

function ub_walk_import(listview) {
	const n = (v) => format_number(v, null, 0);
	const d = new frappe.ui.Dialog({
		title: __("Import Walk Order"),
		fields: [
			{ fieldtype: "HTML", options: `<p class="text-muted">${__("A CSV or Excel sheet with the columns <b>route</b> and <b>reference_no</b>, and optionally <b>order</b>; without an order the rows are taken in their order. Each route gets its listed accounts first, then the accounts it already had. Missing routes are made. If any row is wrong, nothing is changed.")}</p>` },
			...ub_walk_where(),
			{ fieldname: "file", fieldtype: "Attach", label: __("Walk sheet"), reqd: 1 },
		],
		primary_action_label: __("Import"),
		primary_action(v) {
			d.hide();
			utility_billing.run_job("walk_import", { society: v.society, utility: v.utility, file_url: v.file }, __("Importing the walk order for {0}", [v.society]))
				.then((r) => {
					listview.refresh();
					frappe.msgprint({ title: __("Walk order imported"), indicator: "green",
						message: __("{0} accounts put in order on {1} routes ({2} new).", [n(r.accounts), r.routes, r.created]) });
				});
		},
	});
	d.show();
}
