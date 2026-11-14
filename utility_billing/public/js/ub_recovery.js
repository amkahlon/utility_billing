// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Defaulters and Recovery page (FS-REC-04..08, 15). Data comes from utility_billing.utility_billing.recovery.get_recovery.
// Loaded by the stable page loader in page/ub_recovery/ub_recovery.js with a version number from the server.

// The icons are shared; load them first if this page is opened before any other custom page.
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
		root.on("click", ".ub-case", (e) =>
			frappe.xcall("utility_billing.utility_billing.recovery.case_of", { connection: $(e.currentTarget).attr("data-c") })
				.then((n) => frappe.set_route("Form", "Recovery Case", n)));
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

	render(d) {
		const esc = (v) => this.esc(v);
		const opt = (list, cur, any) =>
			(any ? `<option value="">${any}</option>` : "") +
			list.map((o) => `<option value="${esc(o.name || o)}" ${(o.name || o) === cur ? "selected" : ""}>${esc(o.society_name || o)}</option>`).join("");
		const c = d.cards,
			p = d.progress;
		const pct = p.percent == null ? null : Math.min(p.percent, 100);
		const stagePlan = d.rules.stages
			.map(
				(s) => `<tr><td><span class="ub-pill blue">${esc(__(s.stage))}</span></td><td>${s.days} ${__("days")}</td><td>${esc(__(s.channel))}</td>
				<td class="num">${c.due_by_stage[s.stage] || 0}</td><td class="num">${c.issued_this_month[s.stage] || 0}</td></tr>`
			)
			.join("");
		return `
		<div class="ub-head">
			<div><div class="ub-hello">${__("Recovery")}</div><h1>${__("Defaulters and Recovery")}</h1>
			<p>${__("Defaulter: owes at least {0} with a charge overdue {1}+ days.", [this.rs(d.rules.min_amount), d.rules.min_days])}</p></div>
			<div class="ub-tools">
				${d.data_label ? `<span class="ub-badge">${esc(d.data_label)}</span>` : ""}
				<select class="ub-select ub-f" data-f="society">${opt(d.societies, d.society)}</select>
				<input type="date" class="ub-select ub-f" data-f="as_of" value="${esc(d.as_of)}">
				${d.can_run ? `<button class="ub-btn ub-btn-primary ub-run">${__("Run recovery stages")}</button>` : ""}
			</div>
		</div>
		<div class="ub-kpis">
			<div class="ub-kpi"><div class="ub-kpi-top"><span class="ub-kpi-label">${__("Defaulters")}</span><span class="ub-kpi-icon">${ub_icon("defaulters")}</span></div>
				<div class="ub-kpi-value">${c.defaulters}</div><div class="ub-kpi-note red">${this.rs(c.defaulter_amount)} ${__("owed")}</div></div>
			<div class="ub-kpi"><div class="ub-kpi-top"><span class="ub-kpi-label">${__("Notices due now")}</span><span class="ub-kpi-icon">${ub_icon("receipt")}</span></div>
				<div class="ub-kpi-value">${c.notices_due}</div><div class="ub-kpi-note">${c.overdue_accounts} ${__("accounts overdue")}</div></div>
			<div class="ub-kpi"><div class="ub-kpi-top"><span class="ub-kpi-label">${__("On hold")}</span><span class="ub-kpi-icon">${ub_icon("calendar")}</span></div>
				<div class="ub-kpi-value">${c.on_hold}</div><div class="ub-kpi-note ${c.broken_promises ? "red" : ""}">${c.broken_promises} ${__("broken promises")}</div></div>
			<div class="ub-kpi"><div class="ub-kpi-top"><span class="ub-kpi-label">${__("Open cases")}</span><span class="ub-kpi-icon">${ub_icon("check")}</span></div>
				<div class="ub-kpi-value">${c.open_cases}</div><div class="ub-kpi-note">${__("assigned to officers")}</div></div>
		</div>
		<div class="ub-grid3">
			<div class="ub-card ub-span2">
				<div class="ub-cardhead"><h2>${__("Recovery in {0}", [esc(d.month_label)])}</h2>
					${d.can_target ? `<button class="ub-btn ub-target">${p.target ? __("Change target") : __("Set target")}</button>` : ""}</div>
				${
					p.target
						? `<div class="ub-kpi-value">${this.rs(p.recovered)} <span class="ub-kpi-note">${__("of")} ${this.rs(p.target)} · ${p.percent}%</span></div>
					<div class="ub-bar"><i class="${p.reached ? "ok" : ""}" style="width:${pct}%"></i></div>
					<div class="ub-kpi-note">${p.reached ? __("Target reached") : this.rs(p.remaining) + " " + __("still to recover")}</div>`
						: `<div class="ub-kpi-value">${this.rs(p.recovered)}</div><div class="ub-kpi-note">${__("recovered this month. No target is set.")}</div>`
				}
			</div>
			<div class="ub-card"><h2>${__("Stages")}</h2>
				<table class="ub-tbl"><thead><tr><th>${__("Stage")}</th><th>${__("After")}</th><th>${__("By")}</th><th class="num">${__("Due")}</th><th class="num">${__("Issued")}</th></tr></thead>
				<tbody>${stagePlan || `<tr><td colspan="5" class="sub">${__("No stages set on the Society.")}</td></tr>`}</tbody></table></div>
		</div>
		<div class="ub-card">
			<div class="ub-cardhead"><h2>${d.show === "defaulters" ? __("Defaulters") : __("Accounts overdue")} (${d.total_rows})</h2>
				<div class="ub-tools">
					<select class="ub-select ub-f" data-f="show"><option value="defaulters" ${d.show === "defaulters" ? "selected" : ""}>${__("Defaulters only")}</option>
						<option value="all" ${d.show === "all" ? "selected" : ""}>${__("All overdue accounts")}</option></select>
					<select class="ub-select ub-f" data-f="block"><option value="">${__("All blocks")}</option>${d.blocks.map((b) => `<option ${b === d.block ? "selected" : ""}>${esc(b)}</option>`).join("")}</select>
					<select class="ub-select ub-f" data-f="category">${opt(d.categories, d.category, __("All categories"))}</select>
					${
						d.can_run
							? `<select class="ub-select ub-officer"><option value="">${__("Assign to…")}</option>${d.officers.map((o) => `<option>${esc(o)}</option>`).join("")}</select>
						<button class="ub-btn ub-assign" disabled>${__("Assign")}</button>`
							: ""
					}
				</div></div>
			${d.rows.length ? this.table(d) : `<div class="ub-allgood">${ub_icon("check")} ${__("No accounts match.")}</div>`}
			${d.truncated ? `<div class="ub-kpi-note">${__("Showing the first 500. Use the filters or the Defaulters report for the rest.")}</div>` : ""}
		</div>`;
	}

	table(d) {
		const esc = (v) => this.esc(v);
		const tone = { Promised: "amber", Broken: "red" };
		const rows = d.rows
			.map((r) => {
				const status = r.hold
					? `<span class="ub-pill amber">${esc(__(r.hold))}</span>`
					: r.next_stage
					? `<span class="ub-pill red">${esc(__(r.next_stage))} ${__("due")}</span>`
					: r.last_stage
					? `<span class="ub-pill green">${esc(__(r.last_stage))} ${__("issued")}</span>`
					: `<span class="ub-pill">${__("No notice yet")}</span>`;
				return `<tr>
				<td>${d.can_run ? `<input type="checkbox" class="ub-pick" data-c="${esc(r.connection)}" ${this.picked.has(r.connection) ? "checked" : ""}>` : ""}</td>
				<td><b>${esc(r.consumer_name)}</b><div class="sub">${esc(r.connection)} · ${esc(r.property_no || "")}</div></td>
				<td>${esc(r.block)}<div class="sub">${esc(r.category || "")}</div></td>
				<td class="num">${this.rs(r.owed)}<div class="sub">${this.rs(r.overdue)} ${__("overdue")}</div></td>
				<td class="num">${r.oldest}${r.defaulter ? "" : ""}</td>
				<td>${status}</td>
				<td>${r.case_status ? `<span class="ub-pill ${tone[r.promise] || "blue"}">${esc(__(r.case_status))}${r.promise_date ? " · " + esc(frappe.datetime.str_to_user(r.promise_date)) : ""}</span>` : ""}
					<div class="sub">${esc(r.assigned_to || "")}</div></td>
				<td>${d.can_run ? `<button class="ub-btn ub-case" data-c="${esc(r.connection)}">${__("Case")}</button>` : ""}</td></tr>`;
			})
			.join("");
		return `<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr>
			<th>${d.can_run ? '<input type="checkbox" class="ub-pick-all">' : ""}</th><th>${__("Consumer")}</th><th>${__("Block")}</th><th class="num">${__("Owed")}</th>
			<th class="num">${__("Days")}</th><th>${__("Recovery")}</th><th>${__("Case")}</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
	}

	refresh_bar() {
		this.$root.find(".ub-assign").prop("disabled", !this.picked.size).text(this.picked.size ? __("Assign {0}", [this.picked.size]) : __("Assign"));
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
