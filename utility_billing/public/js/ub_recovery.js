// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Defaulters and Recovery page (FS-REC-04..08, 15), laid out after the mock-up "Defaulters and recovery" (2026-11-16): four figures, then
// the accounts with a selection bar (total of the selection and what can be done with it) and a stage pill per account.
// Data comes from utility_billing.utility_billing.recovery.get_recovery. Loaded by page/ub_recovery/ub_recovery.js.

if (!window.ub_icon) {
	$.ajax({ url: `/assets/utility_billing/js/ub_icons.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", async: false, cache: true });
}

window.UtilityBillingRecovery = class UtilityBillingRecovery {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.q = { society: null, as_of: null, block: "", category: "", show: "defaulters" };
		this.data = null;
		this.picked = new Set();
		const root = this.$root;
		root.on("change", ".ub-f", (e) => {
			const k = $(e.target).attr("data-f");
			this.q[k] = e.target.value;
			if (k === "society") Object.assign(this.q, { block: "", category: "" });
			this.picked.clear();
			this.load();
		});
		root.on("change", ".ub-pick", (e) => {
			const c = $(e.target).attr("data-c");
			e.target.checked ? this.picked.add(c) : this.picked.delete(c);
			this.refresh_bar();
		});
		root.on("change", ".ub-pick-all", (e) => {
			this.$root.find(".ub-pick").each((_i, el) => {
				el.checked = e.target.checked;
				const c = $(el).attr("data-c");
				e.target.checked ? this.picked.add(c) : this.picked.delete(c);
			});
			this.refresh_bar();
		});
		root.on("click", ".ub-run", () => this.run());
		root.on("click", ".ub-target", () => this.target());
		root.on("click", ".ub-assign", () => this.assign());
		root.on("click", ".ub-notice", () => this.notice());
		root.on("click", ".ub-plan", () => this.plan());
		root.on("click", ".ub-disconnect", () => this.disconnect());
		root.on("click", ".ub-case", (e) =>
			frappe.xcall("utility_billing.utility_billing.recovery.case_of", { connection: $(e.currentTarget).attr("data-c") })
				.then((n) => frappe.set_route("Form", "Recovery Case", n)));
		root.on("click", ".ub-c360", (e) => frappe.set_route("ub-consumer-360", { account: $(e.currentTarget).attr("data-c") }));
		root.on("click", ".ub-link", (e) => {
			const [dt, name] = $(e.currentTarget).attr("data-go").split("|");
			frappe.set_route(name ? ["Form", dt, name] : ["List", dt]);
		});
	}

	load() {
		this.$root.html(`<div class="ub-card ub-empty">${__("Loading recovery…")}</div>`);
		frappe
			.xcall("utility_billing.utility_billing.recovery.get_recovery", this.q)
			.then((d) => {
				this.data = d;
				if (d.society) this.q.society = d.society;
				if (d.as_of) this.q.as_of = d.as_of;
				this.$root.html(d.empty ? `<div class="ub-card ub-empty">${__("Set up a society first.")}</div>` : this.render(d));
				this.refresh_bar();
			})
			.catch(() => this.$root.html(`<div class="ub-card ub-empty">${__("Recovery could not be loaded.")}</div>`));
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	rs(v) {
		return "Rs " + Math.round(Number(v || 0)).toLocaleString("en-US");
	}
	big(v) {
		const a = Number(v || 0);
		return a >= 1e6 ? `Rs ${(a / 1e6).toFixed(1)}M` : this.rs(a);
	}

	render(d) {
		const esc = (v) => this.esc(v);
		const opt = (list, cur, any) =>
			(any ? `<option value="">${any}</option>` : "") +
			list.map((o) => `<option value="${esc(o.name || o)}" ${(o.name || o) === cur ? "selected" : ""}>${esc(o.society_name || o)}</option>`).join("");
		const c = d.cards,
			p = d.progress;
		const kpi = (label, value, note, tone) => `<div class="ub-kpi"><div class="ub-kpi-top"><span class="ub-kpi-label">${label}</span></div>
			<div class="ub-kpi-value">${value}</div><div class="ub-kpi-note ${tone || ""}">${note || ""}</div></div>`;
		const stagePlan = d.rules.stages
			.map((s) => `<tr><td><span class="ub-pill blue">${esc(__(s.stage))}</span></td><td>${s.days} ${__("days")}</td><td>${esc(__(s.channel))}</td>
				<td class="num">${c.due_by_stage[s.stage] || 0}</td><td class="num">${c.issued_this_month[s.stage] || 0}</td></tr>`)
			.join("");
		return `
		<div class="ub-head">
			<div><h1>${__("Defaulters and recovery")}</h1>
			<p>${__("Arrears over {0} and older than {1} days", [this.rs(d.rules.min_amount), d.rules.min_days])} · ${d.block ? esc(d.block) : __("all blocks")}</p></div>
			<div class="ub-tools">
				${d.data_label ? `<span class="ub-badge">${esc(d.data_label)}</span>` : ""}
				<select class="ub-select ub-f" data-f="society">${opt(d.societies, d.society)}</select>
				<input type="date" class="ub-select ub-f" data-f="as_of" value="${esc(d.as_of)}">
				${d.can_run ? `<button class="ub-btn ub-run">${__("Run recovery stages")}</button>` : ""}
			</div>
		</div>
		<div class="ub-kpis">
			${kpi(__("Defaulters"), c.defaulters.toLocaleString("en-US"), __("{0} accounts overdue in all", [c.overdue_accounts]))}
			${kpi(__("Outstanding"), this.big(c.outstanding), __("{0} owed by defaulters", [this.big(c.defaulter_amount)]))}
			${kpi(__("Recovered this month"), this.big(p.recovered),
				p.target ? `${__("Target")} ${this.big(p.target)} · ${p.percent}%${d.can_target ? ` · <a class="ub-target">${__("change")}</a>` : ""}`
					: d.can_target ? `<a class="ub-target">${__("Set a target")}</a>` : __("No target set"), p.reached ? "green" : "")}
			${kpi(__("On installment plans"), c.plans, c.missed_installments ? __("{0} missed an installment", [c.missed_installments]) : __("none missed"),
				c.missed_installments ? "red" : "")}
		</div>
		<div class="ub-card">
			<div class="ub-cardhead"><h2>${d.show === "defaulters" ? __("Defaulters") : __("Accounts overdue")} (${d.total_rows})</h2>
				<div class="ub-tools">
					<select class="ub-select ub-f" data-f="show"><option value="defaulters" ${d.show === "defaulters" ? "selected" : ""}>${__("Defaulters only")}</option>
						<option value="all" ${d.show === "all" ? "selected" : ""}>${__("All overdue accounts")}</option></select>
					<select class="ub-select ub-f" data-f="block"><option value="">${__("All blocks")}</option>${d.blocks.map((b) => `<option ${b === d.block ? "selected" : ""}>${esc(b)}</option>`).join("")}</select>
					<select class="ub-select ub-f" data-f="category">${opt(d.categories, d.category, __("All categories"))}</select>
				</div></div>
			${d.can_run ? `<div class="ub-selbar" hidden><span class="ub-selcount"></span>
				<div class="ub-tools"><button class="ub-btn ub-notice">${__("Send notice")}</button><button class="ub-btn ub-plan">${__("Offer installment plan")}</button>
				<select class="ub-select ub-officer"><option value="">${__("Assign to…")}</option>${d.officers.map((o) => `<option>${esc(o)}</option>`).join("")}</select>
				<button class="ub-btn ub-assign">${__("Assign")}</button>
				<button class="ub-btn ub-btn-danger ub-disconnect">${__("Create disconnection orders")}</button></div></div>` : ""}
			${d.rows.length ? this.table(d) : `<div class="ub-allgood">${ub_icon("check")} ${__("No accounts match.")}</div>`}
			${d.truncated ? `<div class="ub-kpi-note">${__("Showing the first 500. Use the filters or the Defaulters report for the rest.")}</div>` : ""}
		</div>
		<details class="ub-card ub-rec-stages"><summary><h2 style="display:inline">${__("How recovery works here")}</h2></summary>
			<table class="ub-tbl"><thead><tr><th>${__("Stage")}</th><th>${__("After")}</th><th>${__("By")}</th><th class="num">${__("Due now")}</th><th class="num">${__("Issued this month")}</th></tr></thead>
			<tbody>${stagePlan || `<tr><td colspan="5" class="sub">${__("No stages set on the Society.")}</td></tr>`}</tbody></table></details>`;
	}

	table(d) {
		const esc = (v) => this.esc(v);
		const rows = d.rows
			.map((r) => `<tr>
				<td>${d.can_run ? `<input type="checkbox" class="ub-pick" data-c="${esc(r.connection)}" ${this.picked.has(r.connection) ? "checked" : ""}>` : ""}</td>
				<td><a class="ub-c360" data-c="${esc(r.connection)}"><b>${esc(r.consumer_name)}</b></a><div class="sub">${__("Ref.")} ${esc(r.connection)}</div></td>
				<td>${esc(r.property_no || "")}<div class="sub">${esc(r.block || "")}</div></td>
				<td class="num">${Math.round(r.owed).toLocaleString("en-US")}</td>
				<td class="num">${r.oldest} ${__("days")}</td>
				<td>${r.last_payment ? esc(moment(r.last_payment).format("D MMM YYYY")) : `<span class="sub">${__("none")}</span>`}</td>
				<td><span class="ub-pill ${r.stage_tone}">${esc(r.stage_label)}</span>${r.case_status ? `<div class="sub">${esc(__(r.case_status))}${r.assigned_to ? " · " + esc(r.assigned_to) : ""}</div>` : ""}</td>
				<td>${d.can_run ? `<button class="ub-btn ub-case" data-c="${esc(r.connection)}">${__("Case")}</button>` : ""}</td></tr>`)
			.join("");
		return `<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr>
			<th>${d.can_run ? '<input type="checkbox" class="ub-pick-all">' : ""}</th><th>${__("Consumer")}</th><th>${__("House")}</th><th class="num">${__("Arrears (Rs)")}</th>
			<th class="num">${__("Age")}</th><th>${__("Last payment")}</th><th>${__("Stage")}</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
	}

	refresh_bar() {
		const bar = this.$root.find(".ub-selbar");
		if (!bar.length) return;
		const rows = (this.data && this.data.rows) || [];
		const total = rows.filter((r) => this.picked.has(r.connection)).reduce((s, r) => s + r.owed, 0);
		bar.prop("hidden", !this.picked.size);
		bar.find(".ub-selcount").html(`<b>${__("{0} selected", [this.picked.size])}</b> · ${this.rs(total)}`);
	}

	run() {
		frappe.confirm(
			__("Work out the notice each defaulter is due as of {0}? You can review the list before anything is issued.", [frappe.datetime.str_to_user(this.q.as_of)]),
			() =>
				frappe
					.xcall("utility_billing.utility_billing.recovery.new_run", { society: this.q.society, as_of: this.q.as_of, block: this.q.block || null, consumer_category: this.q.category || null })
					.then((n) => frappe.set_route("Form", "Recovery Run", n))
		);
	}

	notice() {
		// a recovery run for the selected accounts: each gets the stage it is due; the run is reviewed before anything is issued
		frappe
			.xcall("utility_billing.utility_billing.recovery.new_run", { society: this.q.society, as_of: this.q.as_of, connections: JSON.stringify([...this.picked]) })
			.then((n) => frappe.set_route("Form", "Recovery Run", n));
	}

	plan() {
		if (this.picked.size !== 1) return frappe.msgprint(__("Choose one account to offer an installment plan to."));
		frappe.new_doc("Installment Plan", { connection: [...this.picked][0] });
	}

	disconnect() {
		const n = this.picked.size;
		frappe.confirm(__("Raise a disconnection request for {0} account(s)? Each goes through the notice period and an approval before the supply is cut.", [n]), () =>
			frappe
				.xcall("utility_billing.utility_billing.recovery.disconnection_requests", { society: this.q.society, connections: JSON.stringify([...this.picked]) })
				.then((r) => {
					frappe.show_alert({ message: __("{0} requests raised, {1} already had one.", [r.made.length, r.skipped.length]), indicator: "green" });
					this.picked.clear();
					this.load();
				}));
	}

	assign() {
		const user = this.$root.find(".ub-officer").val();
		if (!user) return frappe.msgprint(__("Choose the recovery officer first."));
		frappe
			.xcall("utility_billing.utility_billing.recovery.assign", { society: this.q.society, connections: JSON.stringify([...this.picked]), user })
			.then((r) => {
				frappe.show_alert({ message: __("{0} cases opened, {1} reassigned.", [r.opened, r.reassigned]), indicator: "green" });
				this.picked.clear();
				this.load();
			});
	}

	target() {
		const t = this.data.progress;
		const first = frappe.datetime.month_start(this.q.as_of);
		if (t.name) return frappe.set_route("Form", "Recovery Target", t.name);
		frappe.new_doc("Recovery Target", { society: this.q.society, month: first });
	}
};
