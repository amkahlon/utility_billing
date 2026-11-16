// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Society Users (increment 25): the users of the manager's own societies, their roles, societies, counters and blocks.
// Data and the limits: utility_billing.utility_billing.users.

window.UtilityBillingSocietyUsers = class UtilityBillingSocietyUsers {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root ub-us"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.sel = null;
		this.q = "";
		const root = this.$root;
		root.on("click", ".ub-us-row", (e) => this.pick($(e.currentTarget).attr("data-u")));
		root.on("click", ".ub-us-new", () => this.pick("__new__"));
		root.on("input", ".ub-us-q", (e) => {
			this.q = e.target.value.toLowerCase();
			this.paint();
			this.$root.find(".ub-us-q").trigger("focus")[0].setSelectionRange(this.q.length, this.q.length);
		});
		root.on("click", ".ub-us-save", () => this.save());
		root.on("click", ".ub-us-addblock", () => {
			this.form().blocks.push({ society: (this.form().societies[0] || ""), block: "" });
			this.paint();
		});
		root.on("change input", ".ub-us-f", (e) => {
			const f = this.form();
			const $t = $(e.target);
			const k = $t.attr("data-k");
			if (k === "roles" || k === "societies" || k === "counters") {
				const v = $t.attr("data-v");
				f[k] = e.target.checked ? [...new Set([...f[k], v])] : f[k].filter((x) => x !== v);
				if (k === "societies") this.paint();
			} else if (k === "block" || k === "bsociety") {
				const i = +$t.attr("data-i");
				f.blocks[i][k === "block" ? "block" : "society"] = e.target.value;
			} else if (k === "enabled") {
				f.enabled = e.target.checked ? 1 : 0;
			} else {
				f[k] = e.target.value;
			}
		});
		root.on("click", ".ub-us-delblock", (e) => {
			this.form().blocks.splice(+$(e.currentTarget).attr("data-i"), 1);
			this.paint();
		});
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	load(user) {
		if (user) this.sel = user;
		frappe
			.xcall("utility_billing.utility_billing.users.users")
			.then((d) => this.show(d))
			.catch((e) => {
				let msg = __("You can't manage society users.");
				try {
					msg = JSON.parse(JSON.parse(e._server_messages)[0]).message || msg;
				} catch (err) {
					if (e && e.message) msg = e.message;
				}
				$(".modal:visible .btn-modal-close").click(); // the message is shown on the page itself
				this.$root.html(`<div class="ub-card ub-empty">${frappe.utils.escape_html(frappe.utils.strip_html(String(msg)))}</div>`);
			});
	}

	show(d) {
		this.d = d;
		if (this.sel && this.sel !== "__new__") this.edit = null;
		this.paint();
	}

	pick(name) {
		this.sel = name;
		const u = this.d.users.find((x) => x.name === name);
		this.edit = u
			? { email: u.name, full_name: u.full_name, enabled: u.enabled, roles: [...u.roles], societies: [...u.societies], counters: [...u.counters],
				blocks: u.blocks.map((b) => ({ ...b })), existing: 1 }
			: { email: "", full_name: "", enabled: 1, roles: [], societies: this.d.societies.length === 1 ? [this.d.societies[0].name] : [], counters: [], blocks: [] };
		this.paint();
	}

	form() {
		return this.edit;
	}

	paint() {
		const d = this.d;
		const list = d.users.filter((u) => !this.q || `${u.full_name} ${u.name} ${u.roles.join(" ")}`.toLowerCase().includes(this.q));
		const rows = list.length
			? list.map((u) => `<tr class="ub-us-row ${u.name === this.sel ? "on" : ""}" data-u="${this.esc(u.name)}" style="cursor:pointer">
				<td><b>${this.esc(u.full_name || u.name)}</b><div class="sub">${this.esc(u.name)}</div></td>
				<td>${u.roles.map((r) => `<span class="ub-pill blue">${this.esc(__(r))}</span>`).join(" ")}</td>
				<td>${u.societies.map((s) => this.esc(s)).join(", ")}</td>
				<td>${u.enabled ? `<span class="ub-pill green">${__("Active")}</span>` : `<span class="ub-pill">${__("Disabled")}</span>`}</td></tr>`).join("")
			: `<tr><td colspan="4" class="ub-empty">${__("No users yet.")}</td></tr>`;
		this.$root.html(`
			<div class="ub-head"><div><h1>${__("Society Users")}</h1>
				<p>${d.scoped ? __("Users of your societies: their roles, and the societies, counters and blocks they work in.") : __("All users of this site except the administrator.")}</p></div>
				<div class="ub-tools"><input class="ub-select ub-us-q" placeholder="${__("Find a user or role")}" value="${this.esc(this.q)}">
					<button class="ub-btn ub-btn-primary ub-us-new">${__("New user")}</button></div></div>
			<div class="ub-rv-grid"><section class="ub-card"><div style="overflow-x:auto"><table class="ub-tbl"><thead><tr><th>${__("User")}</th><th>${__("Roles")}</th>
				<th>${__("Societies")}</th><th>${__("Status")}</th></tr></thead><tbody>${rows}</tbody></table></div></section>
				<div class="ub-rv-side">${this.edit ? this.editor() : `<section class="ub-card ub-empty">${__("Choose a user, or make a new one.")}</section>`}</div></div>`);
	}

	editor() {
		const f = this.edit;
		const d = this.d;
		const tick = (k, v, label) => `<label class="ub-us-tick"><input type="checkbox" class="ub-us-f" data-k="${k}" data-v="${this.esc(v)}" ${f[k].includes(v) ? "checked" : ""}> ${this.esc(label)}</label>`;
		const counters = d.counters.filter((c) => f.societies.includes(c.society));
		return `<section class="ub-card">
			<div class="ub-cardhead"><h2>${f.existing ? this.esc(f.full_name || f.email) : __("New user")}</h2></div>
			<div class="ub-te-fields" style="grid-template-columns:1fr 1fr">
				<label>${__("E-mail (sign-in)")}<input class="ub-select ub-us-f" data-k="email" type="email" value="${this.esc(f.email)}" ${f.existing ? "disabled" : ""}></label>
				<label>${__("Name")}<input class="ub-select ub-us-f" data-k="full_name" value="${this.esc(f.full_name)}"></label></div>
			<label class="ub-us-tick"><input type="checkbox" class="ub-us-f" data-k="enabled" ${f.enabled ? "checked" : ""}> ${__("Can sign in")}</label>
			<h3 class="ub-us-h">${__("Roles")}</h3><div class="ub-us-ticks">${d.roles.map((r) => tick("roles", r, __(r))).join("")}</div>
			<h3 class="ub-us-h">${__("Societies")}</h3><div class="ub-us-ticks">${d.societies.map((s) => tick("societies", s.name, s.society_name)).join("")}</div>
			${counters.length ? `<h3 class="ub-us-h">${__("Cash counters (cashiers)")}</h3><div class="ub-us-ticks">${counters.map((c) => tick("counters", c.name, c.counter_name || c.name)).join("")}</div>` : ""}
			<h3 class="ub-us-h">${__("Only these blocks (leave empty for all)")}</h3>
			${f.blocks.map((b, i) => `<div class="ub-us-block"><select class="ub-select ub-us-f" data-k="bsociety" data-i="${i}">${f.societies.map((s) => `<option ${s === b.society ? "selected" : ""}>${this.esc(s)}</option>`).join("")}</select>
				<input class="ub-select ub-us-f" data-k="block" data-i="${i}" value="${this.esc(b.block)}" placeholder="${__("Block")}"><button class="ub-btn ub-btn-sm ub-us-delblock" data-i="${i}">×</button></div>`).join("")}
			<button class="ub-te-add ub-us-addblock">${__("+ Add block")}</button>
			<div class="ub-kpi-note">${__("New users set their password with Forgot Password on the sign-in page.")}</div>
			<div class="ub-cash-actions"><button class="ub-btn ub-btn-primary ub-us-save">${f.existing ? __("Save") : __("Create user")}</button></div>
		</section>`;
	}

	save() {
		frappe.xcall("utility_billing.utility_billing.users.save_user", { data: this.edit }, "POST", { freeze: true }).then((d) => {
			frappe.show_alert({ message: __("Saved {0}", [this.edit.email]), indicator: "green" });
			this.sel = this.edit.email.toLowerCase();
			this.d = d;
			this.pick(this.sel);
		});
	}
};
