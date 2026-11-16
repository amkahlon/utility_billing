// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Role Rights (increment 25): which roles may do each action of the app, ticked by the society. Actions are grouped by area;
// System Manager always may (so the site's owner can't lock themselves out). Data: utility_billing.utility_billing.rights.

window.UtilityBillingRoleRights = class UtilityBillingRoleRights {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root ub-rr"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.role = "";
		this.q = "";
		this.$root.on("change", ".ub-rr-role", (e) => {
			this.role = e.target.value;
			this.paint();
		});
		this.$root.on("input", ".ub-rr-q", (e) => {
			this.q = e.target.value.toLowerCase();
			this.paint(true);
		});
		this.$root.on("change", ".ub-rr-tick", (e) => {
			const $t = $(e.target);
			const action = $t.attr("data-a");
			const role = $t.attr("data-r");
			$t.prop("disabled", true);
			frappe
				.xcall("utility_billing.utility_billing.rights.set_right", { action, role, allowed: e.target.checked ? 1 : 0 })
				.then((roles) => {
					this.d.actions.find((a) => a.action === action).roles = roles;
					frappe.show_alert({ message: e.target.checked ? __("{0} may now: {1}", [role, $t.attr("data-l")]) : __("{0} may no longer: {1}", [role, $t.attr("data-l")]), indicator: "green" });
				})
				.catch(() => (e.target.checked = !e.target.checked))
				.finally(() => $t.prop("disabled", false));
		});
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	load(role) {
		if (role) this.role = role;
		frappe.xcall("utility_billing.utility_billing.rights.matrix").then((d) => {
			this.d = d;
			this.paint();
		});
	}

	paint(keep_focus) {
		const d = this.d;
		const roles = this.role ? [this.role] : d.roles;
		const shown = d.actions.filter((a) => !this.q || `${a.label} ${a.area} ${a.description}`.toLowerCase().includes(this.q));
		const areas = [...new Set(shown.map((a) => a.area))];
		const head = `<tr><th>${__("Action")}</th>${roles.map((r) => `<th class="ub-rr-rolehead"><span>${this.esc(__(r))}</span></th>`).join("")}</tr>`;
		const body = areas
			.map((area) => `<tr class="ub-rr-area"><td colspan="${roles.length + 1}">${this.esc(__(area))}</td></tr>` + shown
				.filter((a) => a.area === area)
				.map((a) => `<tr><td><b>${this.esc(a.label)}</b><div class="sub">${this.esc(a.description)}</div></td>${roles
					.map((r) => `<td class="ub-rr-cell"><input type="checkbox" class="ub-rr-tick" data-a="${this.esc(a.action)}" data-r="${this.esc(r)}"
						data-l="${this.esc(a.label)}" ${a.roles.includes(r) ? "checked" : ""} ${d.can_edit ? "" : "disabled"} title="${this.esc(__(r))}"></td>`)
					.join("")}</tr>`)
				.join(""))
			.join("");
		const table = `<div class="ub-rr-wrap"><table class="ub-tbl ub-rr-tbl"><thead>${head}</thead><tbody>${body ||
			`<tr><td class="ub-empty" colspan="${roles.length + 1}">${__("No action matches.")}</td></tr>`}</tbody></table></div>`;
		if (keep_focus && this.$root.find(".ub-rr-wrap").length) return this.$root.find(".ub-rr-wrap").replaceWith(table);
		this.$root.html(`
			<div class="ub-head"><div><h1>${__("Role Rights")}</h1>
				<p>${__("Which roles may do each action. Rights on records are on Role Permissions, who approves on each Workflow, and which screens and reports a role sees on Screen and Report Roles. A System Manager may always do everything.")}</p></div>
				<div class="ub-tools"><input class="ub-select ub-rr-q" placeholder="${__("Find an action")}" value="${this.esc(this.q)}">
					<select class="ub-select ub-rr-role"><option value="">${__("All roles")}</option>${d.roles
						.map((r) => `<option value="${this.esc(r)}" ${r === this.role ? "selected" : ""}>${this.esc(__(r))}</option>`).join("")}</select></div></div>
			${d.can_edit ? "" : `<div class="ub-card ub-kpi-note">${__("You can see the rights but not change them (that needs the right to manage action rights).")}</div>`}
			<section class="ub-card">${table}</section>`);
	}
};
