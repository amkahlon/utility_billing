// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Home dashboard. All figures come from utility_billing.utility_billing.dashboard.get_dashboard, already formatted;
// this file only draws them (cards with icons and sparklines, area chart, cycle steps, alerts, feeders, channels).
// Loaded by the stable page loader in page/ub_dashboard/ub_dashboard.js with a version number from the server.

// The icons are shared; load them first if this page is opened before any other custom page.
if (!window.ub_icon) {
	$.ajax({ url: `/assets/utility_billing/js/ub_icons.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", async: false, cache: true });
}

window.UtilityBillingDashboard = class UtilityBillingDashboard {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.society = null;
		this.month = null;
		this.data = null;
		this.$root.on("change", ".ub-society", (e) => this.reload({ society: e.target.value, month: null }));
		this.$root.on("change", ".ub-month", (e) => this.reload({ month: e.target.value }));
		this.$root.on("click", ".ub-export", () => this.export_csv());
		this.$root.on("click", "[data-new]", (e) => frappe.new_doc($(e.currentTarget).attr("data-new")));
		this.$root.on("click", "[data-report]", () => frappe.set_route("query-report", "Management Dashboard"));
		this.$root.on("click", "[data-page]", (e) => frappe.set_route($(e.currentTarget).attr("data-page")));
		this.$root.on("click", ".ub-attn-item", (e) => {
			const item = this.data.attention[$(e.currentTarget).attr("data-i")];
			frappe.route_options = item.options || {};
			frappe.set_route(...item.route);
		});
	}

	reload(change) {
		Object.assign(this, change);
		this.load();
	}

	load() {
		this.$root.html(this.skeleton());
		frappe
			.xcall("utility_billing.utility_billing.dashboard.get_dashboard", { society: this.society, month: this.month })
			.then((d) => {
				this.data = d;
				this.society = d.society || null;
				this.month = d.month || null;
				this.$root.html(d.empty ? `<div class="ub-card ub-empty">${frappe.utils.escape_html(d.message)}</div>` : this.render(d));
				if (!d.empty) this.insights(d);
			})
			.catch(() => this.$root.html(`<div class="ub-card ub-empty">${__("The dashboard could not be loaded.")}</div>`));
	}

	// Insights (AI analytics 1, increment 24): the month's facts by rules; a written summary when the society has switched it on.
	// Loaded after the dashboard, so a slow AI call never holds the page up.
	insights(d) {
		const slot = this.$root.find(".ub-insights-slot");
		frappe
			.xcall("utility_billing.utility_billing.ai.insights", { society: d.society, month: d.month })
			.then((r) => {
				if (!r.facts.length) return slot.empty();
				const e = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
				const linkify = (text) => e(text).replace(/\[(\d+)\]/g, (_m, n) => `<sup class="ub-fact-ref">[${n}]</sup>`);
				const body = r.summary
					? `<p class="ub-insight-text">${linkify(r.summary)}</p>`
					: `<ul class="ub-insight-facts">${r.facts.slice(0, 5).map((f) => `<li>${e(f.text)}</li>`).join("")}</ul>`;
				const facts = r.summary
					? `<details class="ub-insight-sources"><summary>${__("The facts behind it")}</summary><ol>${r.facts.map((f) => `<li>${e(f.text)}</li>`).join("")}</ol></details>`
					: "";
				const note = r.error ? `<div class="ub-kpi-note red">${e(r.error)}</div>`
					: r.ai ? `<div class="ub-kpi-note">${__("Written by AI from the facts below; every figure comes from the system's own reports.")}</div>`
					: `<div class="ub-kpi-note">${__("Switch on the AI summary in AI Settings for a written overview.")}</div>`;
				slot.html(`<section class="ub-card ub-insights"><div class="ub-cardhead"><h2>${__("Insights")}</h2></div>${body}${note}${facts}</section>`);
			})
			.catch(() => slot.empty());
	}

	skeleton() {
		const cards = Array.from({ length: 8 }, () => '<div class="ub-skeleton"></div>').join("");
		return `<div class="ub-head"><div><h1>${__("Dashboard")}</h1></div></div><div class="ub-kpis">${cards}</div>`;
	}

	greeting(d) {
		const h = new Date().getHours();
		const part = h < 12 ? __("Good morning") : h < 18 ? __("Good afternoon") : __("Good evening");
		const first = ((d.user && d.user.full_name) || "").split(" ")[0];
		return first ? `${part}, ${frappe.utils.escape_html(first)}` : part;
	}

	spark(values) {
		if (!values || values.length < 2) return "";
		const w = 84, h = 28, lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
		const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 3 - ((v - lo) / span) * (h - 6)]);
		const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
		return `<svg class="ub-spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path class="fill" d="${line} L${w} ${h} L0 ${h}Z"/><path class="line" d="${line}"/></svg>`;
	}

	chart(series) {
		const W = 660, H = 240, P = { l: 52, r: 14, t: 14, b: 28 };
		const top = Math.max(1, ...series.map((m) => Math.max(m.billed, m.collected)));
		const raw = top / 4;  // four grid intervals, each a "nice" number (1, 2, 2.5, 5 x a power of ten)
		const pow = Math.pow(10, Math.floor(Math.log10(raw)));
		const step = [1, 2, 2.5, 5, 10].map((k) => k * pow).find((s) => s >= raw);
		const max = step * 4;
		const n = series.length;
		const x = (i) => P.l + (i * (W - P.l - P.r)) / Math.max(n - 1, 1);
		const y = (v) => P.t + (1 - v / max) * (H - P.t - P.b);
		const compact = (v) => (v >= 1e6 ? `${(v / 1e6).toFixed(v % 1e6 ? 1 : 0)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}K` : `${Math.round(v)}`);
		const path = (key) => series.map((m, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(m[key]).toFixed(1)}`).join(" ");
		const area = (key) => `${path(key)} L${x(n - 1)} ${y(0)} L${x(0)} ${y(0)}Z`;
		const grid = [0, 1, 2, 3, 4]
			.map((k) => `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${y(step * k)}" y2="${y(step * k)}"/><text x="${P.l - 8}" y="${y(step * k) + 4}" text-anchor="end">${compact(step * k)}</text>`)
			.join("");
		const labels = series.map((m, i) => `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${frappe.utils.escape_html(m.month.split(" ")[0])}</text>`).join("");
		const half = (W - P.l - P.r) / Math.max(n - 1, 1) / 2;
		const hits = series
			.map((m, i) => `<rect class="hit" x="${x(i) - half}" y="${P.t}" width="${half * 2}" height="${H - P.t - P.b}"><title>${frappe.utils.escape_html(m.month)}: ${__("billed")} ${format_currency(m.billed)}, ${__("collected")} ${format_currency(m.collected)}</title></rect>`)
			.join("");
		const last = series[n - 1];
		return `<svg class="ub-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${__("Billed vs. collected, last 12 months")}">
			<defs><linearGradient id="ubGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3B82F6" stop-opacity=".38"/><stop offset="1" stop-color="#3B82F6" stop-opacity="0"/></linearGradient></defs>
			${grid}<path class="a-billed" d="${area("billed")}"/><path class="l-billed" d="${path("billed")}"/>
			<path class="a-coll" d="${area("collected")}"/><path class="l-coll" d="${path("collected")}"/>
			<circle class="dot" cx="${x(n - 1)}" cy="${y(last.collected)}" r="4"/>${labels}${hits}</svg>`;
	}

	render(d) {
		const e = frappe.utils.escape_html;
		const options = (list, current, key, label) => list.map((o) => `<option value="${e(o[key])}" ${o[key] === current ? "selected" : ""}>${e(o[label])}</option>`).join("");
		const kpis = d.kpis
			.map(
				(k) => `<div class="ub-kpi ${k.raw === null || k.raw === undefined ? "is-empty" : ""}">
					<div class="ub-kpi-top"><div class="ub-kpi-label">${e(k.label)}</div><div class="ub-kpi-icon">${ub_icon(k.icon)}</div></div>
					<div class="ub-kpi-value">${e(k.value)}</div>
					<div class="ub-kpi-foot"><div class="ub-kpi-note ${e(k.tone)}">${e(k.note)}</div>${this.spark(k.spark)}</div></div>`
			)
			.join("");
		const steps = d.steps
			.map((s) => `<div class="ub-step ${s.state}"><div class="ub-step-mark">${e(s.mark)}</div><div><div class="ub-step-title">${e(s.title)}</div><div class="ub-step-detail">${e(s.detail)}</div></div></div>`)
			.join("");
		const attention = d.attention.length
			? `<div class="ub-attn">${d.attention
					.map(
						(a, i) => `<button class="ub-attn-item ${e(a.tone)}" data-i="${i}"><span class="ub-attn-icon">${ub_icon(a.tone === "blue" ? "info" : "alert")}</span>
							<span><div class="ub-attn-title">${e(a.title)}</div><div class="ub-attn-detail">${e(a.detail)}</div></span><span class="ub-attn-go">›</span></button>`
					)
					.join("")}</div>`
			: `<div class="ub-allgood"><span style="width:20px;height:20px;display:inline-flex">${ub_icon("check")}</span>${__("Nothing needs attention right now.")}</div>`;
		const feeders = d.feeders.length
			? d.feeders
					.map(
						(f) => `<tr><td><b>${e(f.name)}</b><div class="sub">${f.connections.toLocaleString()} ${__("connections")}</div></td>
							<td class="num">${e(f.input_kwh)}</td><td class="num">${e(f.billed_kwh)}</td><td class="num">${e(f.loss)}</td>
							<td><span class="ub-pill ${e(f.tone)}">${e(f.status)}</span></td></tr>`
					)
					.join("")
			: `<tr><td colspan="5" class="ub-empty">${__("No bills in this cycle yet.")}</td></tr>`;
		const channels = d.channels.length
			? d.channels
					.map((c) => `<div><div class="ub-row-top"><span>${e(c.name)}</span><span>${e(c.amount)} · ${c.pct}%</span></div><div class="ub-meter"><i style="width:${c.pct}%"></i></div></div>`)
					.join("")
			: `<div class="ub-empty">${__("No payments received in this month.")}</div>`;
		const actions = [
			["Bill Run", "plus", __("New bill run")],
			["Payment Receipt", "receipt", __("Receive payment")],
			["Reading Cycle", "calendar", __("Open reading cycle")],
			["DISCO Bill", "bolt", __("Enter DISCO bill")],
		]
			.filter(([dt]) => frappe.model.can_create(dt))
			.map(([dt, icon, label]) => `<button class="ub-chip" data-new="${dt}">${ub_icon(icon)}${label}</button>`)
			.join("");

		return `
		<div class="ub-head">
			<div><div class="ub-hello">${this.greeting(d)}</div><h1>${__("Dashboard")}</h1><p>${e(d.subtitle)}</p></div>
			<div class="ub-tools">
				${d.data_label ? `<span class="ub-badge">${e(d.data_label)}</span>` : ""}
				<select class="ub-select ub-society" aria-label="${__("Society")}">${options(d.societies, d.society, "name", "label")}</select>
				<select class="ub-select ub-month" aria-label="${__("Month")}">${options(d.months, d.month, "value", "label")}</select>
				<button class="ub-btn ub-btn-primary ub-export">${__("Export")}</button>
			</div>
		</div>
		<div class="ub-actions">${actions}<button class="ub-chip" data-page="ub-recovery">${ub_icon("defaulters")}${__("Defaulters and recovery")}</button><button class="ub-chip" data-report="1">${ub_icon("chart")}${__("All reports")}</button></div>
		<div class="ub-kpis">${kpis}</div>
		<div class="ub-insights-slot"></div>
		<div class="ub-grid3">
			<section class="ub-card ub-span2">
				<div class="ub-cardhead"><h2>${__("Billed vs. collected, last 12 months")}</h2>
					<div class="ub-legend"><span><i style="background:var(--ub-faint)"></i>${__("Billed")}</span><span><i style="background:var(--ub-blue)"></i>${__("Collected")}</span></div></div>
				${this.chart(d.series)}
			</section>
			<section class="ub-card"><div class="ub-cardhead"><h2>${__("Needs attention")}</h2></div>${attention}</section>
		</div>
		<div class="ub-grid3">
			<section class="ub-card"><h2>${__("{0} cycle status", [e(d.month_label.split(" ")[0])])}</h2>
				<div class="ub-steps">${steps}</div>${d.footer ? `<div class="ub-note" style="margin-top:14px">${e(d.footer)}</div>` : ""}</section>
			<section class="ub-card ub-span2"><h2>${__("Line losses by feeder")}</h2>
				<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr><th>${__("Feeder")}</th><th class="num">${__("Input kWh")}</th><th class="num">${__("Billed kWh")}</th>
					<th class="num">${__("Loss")}</th><th>${__("Status")}</th></tr></thead><tbody>${feeders}</tbody></table></div>
				${d.feeders_have_input ? "" : `<p class="ub-empty" style="padding-bottom:0">${__("Input kWh and loss appear once the DISCO bill and the feeder input units are recorded (DISCO Bill).")}</p>`}
			</section>
		</div>
		<div class="ub-grid3"><section class="ub-card"><h2>${__("Collections by channel")}</h2><div class="ub-rows">${channels}</div></section></div>`;
	}

	export_csv() {
		const d = this.data;
		if (!d) return;
		const q = (v) => `"${String(v).replace(/"/g, '""')}"`;
		const lines = [[__("Indicator"), __("Value"), __("Note")].map(q).join(",")];
		d.kpis.forEach((k) => lines.push([k.label, k.value, k.note].map(q).join(",")));
		lines.push("", [__("Month"), __("Billed"), __("Collected"), __("Collection rate %")].map(q).join(","));
		d.series.forEach((m) => lines.push([m.month, m.billed, m.collected, m.rate].map(q).join(",")));
		const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `dashboard-${d.society}-${d.month}.csv`;
		a.click();
		URL.revokeObjectURL(a.href);
	}
};
