// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Home dashboard, drawn from the approved mock-up (Management dashboard). All figures come from
// utility_billing.utility_billing.dashboard.get_dashboard, already formatted.

frappe.pages["ub-dashboard"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Dashboard"), single_column: true });
	$(wrapper).addClass("ub-page");
	wrapper.ub_dashboard = new UtilityBillingDashboard(wrapper);
};

frappe.pages["ub-dashboard"].on_page_show = function (wrapper) {
	wrapper.ub_dashboard && wrapper.ub_dashboard.load();
};

class UtilityBillingDashboard {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.society = null;
		this.month = null;
		this.data = null;
		this.$root.on("change", ".ub-society", (e) => this.reload({ society: e.target.value, month: null }));
		this.$root.on("change", ".ub-month", (e) => this.reload({ month: e.target.value }));
		this.$root.on("click", ".ub-export", () => this.export_csv());
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
			})
			.catch(() => this.$root.html(`<div class="ub-card ub-empty">${__("The dashboard could not be loaded.")}</div>`));
	}

	skeleton() {
		const cards = Array.from({ length: 8 }, () => '<div class="ub-skeleton"></div>').join("");
		return `<div class="ub-head"><div><h1>${__("Dashboard")}</h1></div></div><div class="ub-kpis">${cards}</div>`;
	}

	render(d) {
		const e = frappe.utils.escape_html;
		const options = (list, current, key, label) =>
			list.map((o) => `<option value="${e(o[key])}" ${o[key] === current ? "selected" : ""}>${e(o[label])}</option>`).join("");
		const top = Math.max(1, ...d.series.map((m) => Math.max(m.billed, m.collected)));
		const bars = d.series
			.map(
				(m) => `<div class="ub-bar" title="${e(m.month)}: ${__("billed")} ${format_currency(m.billed)}, ${__("collected")} ${format_currency(m.collected)}">
					<b class="billed" style="height:${Math.round((m.billed / top) * 100)}%"></b><b class="collected" style="height:${Math.round((m.collected / top) * 100)}%"></b></div>`
			)
			.join("");
		const axis = d.series.map((m) => `<span>${e(m.month.split(" ")[0])}</span>`).join("");
		const kpis = d.kpis
			.map(
				(k) => `<div class="ub-kpi ${k.raw === null || k.raw === undefined ? "is-empty" : ""}">
					<div class="ub-kpi-label">${e(k.label)}</div><div class="ub-kpi-value">${e(k.value)}</div>
					<div class="ub-kpi-note ${e(k.tone)}">${e(k.note)}</div></div>`
			)
			.join("");
		const steps = d.steps
			.map(
				(s) => `<div class="ub-step ${s.state}"><div class="ub-step-mark">${e(s.mark)}</div>
					<div><div class="ub-step-title">${e(s.title)}</div><div class="ub-step-detail">${e(s.detail)}</div></div></div>`
			)
			.join("");
		const feeders = d.feeders.length
			? d.feeders
					.map(
						(f) => `<tr><td><b>${e(f.name)}</b><div class="sub">${f.connections.toLocaleString()} ${__("connections")}</div></td>
							<td class="num">—</td><td class="num">${e(f.billed_kwh)}</td><td class="num">—</td>
							<td><span class="ub-pill">${e(f.status)}</span></td></tr>`
					)
					.join("")
			: `<tr><td colspan="5" class="ub-empty">${__("No bills in this cycle yet.")}</td></tr>`;
		const channels = d.channels.length
			? d.channels
					.map(
						(c) => `<div><div class="ub-row-top"><span>${e(c.name)}</span><span>${e(c.amount)} · ${c.pct}%</span></div>
							<div class="ub-meter"><i style="width:${c.pct}%"></i></div></div>`
					)
					.join("")
			: `<div class="ub-empty">${__("No payments received in this month.")}</div>`;

		return `
		<div class="ub-head">
			<div><h1>${__("Dashboard")}</h1><p>${e(d.subtitle)}</p></div>
			<div class="ub-tools">
				${d.data_label ? `<span class="ub-badge">${e(d.data_label)}</span>` : ""}
				<select class="ub-select ub-society" aria-label="${__("Society")}">${options(d.societies, d.society, "name", "label")}</select>
				<select class="ub-select ub-month" aria-label="${__("Month")}">${options(d.months, d.month, "value", "label")}</select>
				<button class="ub-btn ub-btn-primary ub-export">${__("Export")}</button>
			</div>
		</div>
		<div class="ub-kpis">${kpis}</div>
		<div class="ub-grid3">
			<section class="ub-card ub-span2">
				<div class="ub-cardhead"><h2>${__("Billed vs. collected, last 12 months")}</h2>
					<div class="ub-legend"><span><i style="background:#94A3B8"></i>${__("Billed")}</span><span><i style="background:#1D4ED8"></i>${__("Collected")}</span></div></div>
				<div class="ub-bars">${bars}</div><div class="ub-axis">${axis}</div>
			</section>
			<section class="ub-card"><h2>${__("{0} cycle status", [e(d.month_label.split(" ")[0])])}</h2>
				<div class="ub-steps">${steps}</div>
				${d.footer ? `<div class="ub-note" style="margin-top:14px">${e(d.footer)}</div>` : ""}
			</section>
		</div>
		<div class="ub-grid3">
			<section class="ub-card ub-span2"><h2>${__("Line losses by feeder")}</h2>
				<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr><th>${__("Feeder")}</th><th class="num">${__("Input kWh")}</th>
					<th class="num">${__("Billed kWh")}</th><th class="num">${__("Loss")}</th><th>${__("Status")}</th></tr></thead><tbody>${feeders}</tbody></table></div>
				<p class="ub-empty" style="padding-bottom:0">${__("Input kWh and loss appear once the DISCO bill and the bulk meter units are recorded.")}</p>
			</section>
			<section class="ub-card"><h2>${__("Collections by channel")}</h2><div class="ub-rows">${channels}</div></section>
		</div>`;
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
}
