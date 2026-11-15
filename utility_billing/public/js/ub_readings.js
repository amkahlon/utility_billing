// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Reading Dashboard (FS-RDG-13, increment 23). Data: utility_billing.utility_billing.reading_dashboard.get_reading_dashboard.
// Refreshes itself every minute while it is open, so supervisors watch the readers' progress through the day.

if (!window.ub_icon) {
	$.ajax({ url: `/assets/utility_billing/js/ub_icons.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", async: false, cache: true });
}

window.UtilityBillingReadings = class UtilityBillingReadings {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.q = { society: null, cycle: null };
		this.$root.on("change", ".ub-f", (e) => {
			const k = $(e.target).attr("data-f");
			this.q[k] = e.target.value;
			if (k === "society") this.q.cycle = null;
			this.load();
		});
		this.$root.on("click", ".ub-link", (e) => {
			const [dt, name, field, value] = $(e.currentTarget).attr("data-go").split("|");
			if (name) return frappe.set_route("Form", dt, name);
			frappe.route_options = field ? { [field]: value, reading_cycle: this.q.cycle } : { reading_cycle: this.q.cycle };
			frappe.set_route("List", dt);
		});
		this.timer = setInterval(() => {
			if (frappe.get_route_str() === "ub-readings" && !document.hidden) this.load(true);
		}, 60000);
	}

	load(quiet) {
		if (!quiet) this.$root.html(`<div class="ub-card ub-empty">${__("Loading readings…")}</div>`);
		frappe
			.xcall("utility_billing.utility_billing.reading_dashboard.get_reading_dashboard", this.q)
			.then((d) => {
				this.q.society = d.society || this.q.society;
				this.q.cycle = d.cycle || this.q.cycle;
				this.$root.html(d.empty ? `<div class="ub-card ub-empty">${__("Set up a society first.")}</div>` : this.render(d));
			})
			.catch(() => quiet || this.$root.html(`<div class="ub-card ub-empty">${__("The reading dashboard could not be loaded.")}</div>`));
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	bar(pct) {
		const w = Math.max(0, Math.min(100, pct || 0));
		return `<div class="ub-bar"><i class="${w >= 100 ? "ok" : ""}" style="width:${w}%"></i></div>`;
	}

	table(rows, first) {
		const esc = (v) => this.esc(v);
		if (!rows.length) return `<div class="ub-kpi-note">${__("No readings in this cycle yet.")}</div>`;
		const body = rows
			.map(
				(r) => `<tr><td><b>${esc(r.label)}</b></td><td class="num">${r.total}</td><td class="num">${r.read}</td>
				<td class="num">${r.waiting ? `<span class="ub-pill amber">${r.waiting}</span>` : 0}</td>
				<td class="num">${r.flagged ? `<span class="ub-pill red">${r.flagged}</span>` : 0}</td><td class="num">${r.estimated}</td>
				<td>${this.bar(r.percent)}<div class="sub">${r.percent == null ? "" : r.percent + "%"}</div></td></tr>`
			)
			.join("");
		return `<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr><th>${first}</th><th class="num">${__("Accounts")}</th>
			<th class="num">${__("Read")}</th><th class="num">${__("Waiting")}</th><th class="num">${__("Flagged")}</th><th class="num">${__("Estimated")}</th>
			<th style="min-width:120px">${__("Done")}</th></tr></thead><tbody>${body}</tbody></table></div>`;
	}

	review(d) {
		const esc = (v) => this.esc(v);
		if (!d.review.length) return `<div class="ub-allgood">${ub_icon("check")} ${__("Nothing waiting for review.")}</div>`;
		const body = d.review
			.map(
				(r) => `<tr class="ub-link" data-go="Meter Reading|${esc(r.name)}" style="cursor:pointer">
				<td><b>${esc(r.consumer_name)}</b><div class="sub">${esc(r.connection)}</div></td><td>${esc(r.route || "")}</td>
				<td class="num">${Math.round(r.units || 0)}</td><td class="num">${Math.round(r.expected || 0)}</td>
				<td>${r.exception_code ? `<span class="ub-pill amber">${esc(r.exception_code)}</span>` : ""}</td></tr>`
			)
			.join("");
		return `<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr><th>${__("Account")}</th><th>${__("Route")}</th>
			<th class="num">${__("Units")}</th><th class="num">${__("Usual")}</th><th>${__("Code")}</th></tr></thead><tbody>${body}</tbody></table></div>
			${d.review_total > d.review.length ? `<div class="ub-kpi-note">${__("Showing {0} of {1}.", [d.review.length, d.review_total])}</div>` : ""}`;
	}

	checks(d) {
		const esc = (v) => this.esc(v);
		if (!d.check_meters.length) return `<div class="ub-kpi-note">${__("No check meters on this cycle's routes.")}</div>`;
		const body = d.check_meters
			.map(
				(m) => `<tr class="ub-link" data-go="Check Meter Reading|${esc(m.name)}" style="cursor:pointer">
				<td><b>${esc(m.meter)}</b><div class="sub">${esc(m.measures || "")}</div></td><td>${esc(m.route || "")}</td>
				<td class="num">${m.status === "Read" ? Math.round(m.units || 0) : ""}</td>
				<td><span class="ub-pill ${m.status === "Read" ? "green" : "amber"}">${esc(__(m.status))}</span></td></tr>`
			)
			.join("");
		return `<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr><th>${__("Check meter")}</th><th>${__("Route")}</th>
			<th class="num">${__("Units")}</th><th>${__("Status")}</th></tr></thead><tbody>${body}</tbody></table></div>`;
	}

	render(d) {
		const esc = (v) => this.esc(v);
		const socs = d.societies.map((s) => `<option value="${esc(s.name)}" ${s.name === d.society ? "selected" : ""}>${esc(s.society_name)}</option>`).join("");
		const cycles = (d.cycles || []).map((c) => `<option value="${esc(c.name)}" ${c.name === d.cycle ? "selected" : ""}>${esc(c.label)}</option>`).join("");
		const head = `
		<div class="ub-head">
			<div><div class="ub-hello">${__("Meter readings")}</div><h1>${__("Reading Dashboard")}</h1>
			<p>${d.no_cycle ? __("No reading cycle yet for this society.")
				: __("{0} readings for {1}. Updated at {2}; refreshes every minute.", [esc(d.utility), esc(d.month_label), esc(d.refreshed)])}</p></div>
			<div class="ub-tools">
				${d.data_label ? `<span class="ub-badge">${esc(d.data_label)}</span>` : ""}
				<select class="ub-select ub-f" data-f="society">${socs}</select>
				${cycles ? `<select class="ub-select ub-f" data-f="cycle">${cycles}</select>` : ""}
			</div>
		</div>`;
		if (d.no_cycle) return head + `<div class="ub-card ub-empty">${__("Create a Reading Cycle and its reading sheet to follow it here.")}</div>`;
		const c = d.cards;
		const w = d.window;
		let windowNote = __("No reading window set");
		if (d.status === "Closed") windowNote = __("Cycle closed");
		else if (w.to) windowNote = w.days_left >= 0 ? __("{0} day(s) left to read", [w.days_left]) : __("Reading window ended {0} day(s) ago", [-w.days_left]);
		const late = d.status !== "Closed" && w.days_left != null && w.days_left < 0 && c.waiting;
		const kpi = (label, icon, value, note, tone, go) => `
			<div class="ub-kpi ub-link" data-go="${go}" style="cursor:pointer">
				<div class="ub-kpi-top"><span class="ub-kpi-label">${label}</span><span class="ub-kpi-icon">${ub_icon(icon)}</span></div>
				<div class="ub-kpi-value">${value}</div><div class="ub-kpi-note ${tone || ""}">${note}</div></div>`;
		return `${head}
		<div class="ub-kpis">
			${kpi(__("Read"), "meters", c.percent == null ? "–" : c.percent + "%", __("{0} of {1} accounts", [c.read + c.estimated, c.total]), "", "Meter Reading")}
			${kpi(__("Waiting"), "calendar", c.waiting, windowNote, late ? "red" : "", "Meter Reading||status|Pending")}
			${kpi(__("Flagged for review"), "alert", c.flagged, __("unusual readings to check"), c.flagged ? "red" : "", "Meter Reading||status|Flagged")}
			${kpi(__("Estimated"), "units", c.estimated, __("{0} of {1} check meters read", [d.check_read, d.check_meters.length]), "", "Meter Reading||status|Estimated")}
		</div>
		<div class="ub-card"><h2>${__("Progress")}</h2>${this.bar(c.percent)}</div>
		<div class="ub-grid3">
			<div class="ub-card ub-span2"><h2>${__("By route")}</h2>${this.table(d.by_route, __("Route"))}</div>
			<div class="ub-card"><h2>${__("Check meters")}</h2>${this.checks(d)}</div>
		</div>
		<div class="ub-grid3">
			<div class="ub-card ub-span2"><h2>${__("Waiting for review")} (${d.review_total})</h2>${this.review(d)}</div>
			<div class="ub-card"><h2>${__("By reader")}</h2>${this.table(d.by_reader, __("Reader"))}</div>
		</div>`;
	}
};
