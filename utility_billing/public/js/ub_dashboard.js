// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Home dashboard (increment 61: rebuilt as an executive overview). Every figure comes from
// utility_billing.utility_billing.dashboard.get_dashboard, already worked out; this file only lays it out and draws the charts.
//
// Layout, top to bottom: the four headline figures (billed, collected, collection efficiency, owed); billing and collection
// over 12 months beside what consumers owe by age; the operating figures; where the money comes from (charges, categories,
// channels); the billing cycle, what needs attention and the insights; line losses by feeder.
// Charts follow one set of rules: one axis per chart, thin marks with 4px rounded ends, colours by role from the CSS tokens
// (validated for colour-blind readers in light and dark), a legend for two or more series, a tooltip on every mark and a table
// view for the main chart. They are drawn to the width of their card and redrawn when it changes.
// Loaded by the stable page loader in page/ub_dashboard/ub_dashboard.js with a version number from the server.

if (!window.ub_icon) {
	$.ajax({ url: `/assets/utility_billing/js/ub_icons.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", async: false, cache: true });
}

(() => {
	const e = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const n0 = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
	const compact = (v) => {
		const a = Math.abs(v);
		if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
		if (a >= 1e6) return `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
		if (a >= 1e3) return `${Math.round(v / 1e3)}K`;
		return `${Math.round(v)}`;
	};
	const rs = (v) => (Math.abs(v) >= 1e6 ? `Rs ${(v / 1e6).toFixed(1)}M` : `Rs ${n0(v)}`);
	// a "nice" axis: 4 intervals of 1, 2, 2.5 or 5 x a power of ten
	const nice = (top) => {
		const raw = Math.max(top, 1) / 4;
		const pow = Math.pow(10, Math.floor(Math.log10(raw)));
		const step = [1, 2, 2.5, 5, 10].map((k) => k * pow).find((s) => s >= raw);
		return { step, max: step * 4 };
	};
	// a bar with 4px rounded data end, square at the baseline
	const bar = (x, y, w, h, cls, tip) => {
		if (h <= 0.5) return "";
		const r = Math.min(4, w / 2, h);
		return `<path class="${cls}" d="M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z"${tip ? ` data-tip="${e(tip)}"` : ""}/>`;
	};
	const hbar = (x, y, w, h, cls) => {
		if (w <= 0.5) return "";
		const r = Math.min(4, h / 2, w);
		return `<path class="${cls}" d="M${x} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h - r}Q${x + w} ${y + h} ${x + w - r} ${y + h}H${x}Z"/>`;
	};
	const ARROW = {
		up: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 2.5 10 7H7v2.5H5V7H2z"/></svg>',
		down: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 9.5 2 5h3V2.5h2V5h3z"/></svg>',
		flat: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 5h8v2H2z"/></svg>',
	};
	const REFRESH = '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3"/></svg>';
	const AGE_CLASS = ["age-due", "age-1", "age-2", "age-3", "age-4", "age-5", "age-go"];
	// the operating tiles, in this order, from the server's cards (keyed by their icon)
	const OPS = [
		["connections", "active-connections"], ["units", "Consumption Analytics"], ["defaulters", "ub-recovery"],
		["losses", "AT&C Losses"], ["meters", "Defective Meters"], ["service", "Service Requests and Service Standards"],
	];

	window.UtilityBillingDashboard = class UtilityBillingDashboard {
		constructor(wrapper) {
			this.$root = $('<div class="ub-root ub-dash"></div>').appendTo($(wrapper).find(".layout-main-section"));
			this.society = null;
			this.month = null;
			this.data = null;
			this.view = "chart";
			this.$tip = $('<div class="ub-tip" role="tooltip"></div>').appendTo(document.body).hide();
			const $r = this.$root;
			$r.on("change", ".ub-society", (ev) => this.reload({ society: ev.target.value, month: null }));
			$r.on("change", ".ub-month", (ev) => this.reload({ month: ev.target.value }));
			$r.on("click", ".ub-export", () => this.export_csv());
			$r.on("click", ".ub-refresh", () => this.load({ refresh: 1 }));
			$r.on("click", "[data-view]", (ev) => { this.view = $(ev.currentTarget).attr("data-view"); this.paint_trend(); });
			$r.on("click keydown", "[data-drill]", (ev) => {
				if (ev.type === "keydown" && ev.key !== "Enter" && ev.key !== " ") return;
				this.drill($(ev.currentTarget).attr("data-drill"));
			});
			$r.on("click", "[data-new]", (ev) => frappe.new_doc($(ev.currentTarget).attr("data-new")));
			$r.on("click", "[data-page]", (ev) => frappe.set_route($(ev.currentTarget).attr("data-page")));
			$r.on("click", "[data-report]", (ev) => frappe.set_route("query-report", $(ev.currentTarget).attr("data-report")));
			$r.on("click", ".ub-attn-item", (ev) => {
				const item = this.data.attention[$(ev.currentTarget).attr("data-i")];
				frappe.route_options = item.options || {};
				frappe.set_route(...item.route);
			});
			$r.on("mouseover focusin", "[data-tip]", (ev) => this.tip(ev.currentTarget));
			$r.on("mousemove", "[data-tip]", (ev) => this.place_tip(ev));
			$r.on("mouseout focusout", "[data-tip]", () => this.$tip.hide());
			if (window.ResizeObserver) {
				let width = 0;
				this.observer = new ResizeObserver(() => {
					const w = this.$root.width();
					if (this.data && !this.data.empty && Math.abs(w - width) > 8) { width = w; this.draw(); }
				});
				this.observer.observe(this.$root[0]);
			}
		}

		reload(change) {
			Object.assign(this, change);
			this.load();
		}

		// The kept figures come back at once; when they are from before a change ("stale") they are shown, marked, and fresh ones
		// are asked for straight after.
		load(opts = {}) {
			if (!this.data || opts.refresh || this.data.society !== this.society) this.$root.html(this.skeleton());
			else this.$root.find(".ub-refresh").addClass("is-busy");
			const args = { society: this.society, month: this.month, allow_stale: opts.refresh ? 0 : 1, refresh: opts.refresh ? 1 : 0 };
			const asked = (this.asked = (this.asked || 0) + 1);
			frappe
				.xcall("utility_billing.utility_billing.dashboard.get_dashboard", args)
				.then((d) => {
					if (asked !== this.asked) return;
					this.data = d;
					this.society = d.society || null;
					this.month = d.month || null;
					if (d.empty) return this.$root.html(`<div class="ub-card ub-empty">${e(d.message)}</div>`);
					this.$root.html(this.render(d));
					this.draw();
					this.insights(d);
					if (d.stale) this.load_fresh();
				})
				.catch(() => this.$root.html(`<div class="ub-card ub-empty">${__("The dashboard could not be loaded.")}</div>`));
		}

		load_fresh() {
			const asked = (this.asked = (this.asked || 0) + 1);
			frappe.xcall("utility_billing.utility_billing.dashboard.get_dashboard", { society: this.society, month: this.month })
				.then((d) => {
					if (asked !== this.asked || d.empty) return;
					this.data = d;
					this.$root.html(this.render(d));
					this.draw();
					this.insights(d);
				})
				.catch(() => this.$root.find(".ub-updated").text(__("Couldn't update; Refresh to try again")));
		}

		drill(to) {
			if (!to) return;
			if (to.startsWith("ub-")) return frappe.set_route(to);
			if (to === "active-connections") return frappe.set_route("List", "Connection", { society: this.society || undefined, status: "Active" });
			frappe.route_options = { society: this.society || undefined };
			frappe.set_route("query-report", to);
		}

		// ------------------------------------------------------------ tooltip
		tip(el) {
			this.$tip.html(e($(el).attr("data-tip")).replace(/\n/g, "<br>")).show();
			const r = el.getBoundingClientRect();
			this.move_tip(r.left + r.width / 2, r.top);
		}

		place_tip(ev) {
			this.move_tip(ev.clientX, ev.clientY - 6);
		}

		move_tip(x, y) {
			const w = this.$tip.outerWidth(), h = this.$tip.outerHeight();
			const left = Math.min(Math.max(8, x - w / 2), window.innerWidth - w - 8);
			const top = y - h - 10 < 8 ? y + 22 : y - h - 10;
			this.$tip.css({ left: left + window.scrollX, top: top + window.scrollY });
		}

		// ------------------------------------------------------------ insights (loaded after, so a slow AI call never holds the page)
		insights(d) {
			const slot = this.$root.find(".ub-insights-slot");
			frappe
				.xcall("utility_billing.utility_billing.ai.insights", { society: d.society, month: d.month })
				.then((r) => {
					if (!r.facts.length) return slot.html(`<div class="ub-empty">${__("Nothing to report for this month yet.")}</div>`);
					const linkify = (text) => e(text).replace(/\[(\d+)\]/g, (_m, k) => `<sup class="ub-fact-ref">[${k}]</sup>`);
					const body = r.summary
						? `<p class="ub-insight-text">${linkify(r.summary)}</p>`
						: `<ul class="ub-insight-facts">${r.facts.slice(0, 5).map((f) => `<li>${e(f.text)}</li>`).join("")}</ul>`;
					const facts = r.summary
						? `<details class="ub-insight-sources"><summary>${__("The facts behind it")}</summary><ol>${r.facts.map((f) => `<li>${e(f.text)}</li>`).join("")}</ol></details>`
						: "";
					const note = r.error ? `<div class="ub-kpi-note red">${e(r.error)}</div>`
						: r.ai ? `<div class="ub-dnote">${__("Written by AI from the facts below; every figure comes from the system's own reports.")}</div>`
						: `<div class="ub-dnote">${__("Switch on the AI summary in AI Settings for a written overview.")}</div>`;
					slot.html(`${body}${note}${facts}`);
				})
				.catch(() => slot.html(`<div class="ub-empty">${__("Insights are not available right now.")}</div>`));
		}

		skeleton() {
			const box = (cls) => `<div class="ub-skeleton ${cls}"></div>`;
			return `<div class="ub-dh"><div><div class="ub-dh-eyebrow">${__("Dashboard")}</div><h1>${__("Overview")}</h1></div></div>
				<div class="ub-dgrid">${[1, 2, 3, 4].map(() => box("s-hero")).join("")}${box("s-wide")}${box("s-side")}</div>`;
		}

		// ------------------------------------------------------------ layout
		render(d) {
			const opt = (list, current, key, label) => list.map((o) => `<option value="${e(o[key])}" ${o[key] === current ? "selected" : ""}>${e(o[label])}</option>`).join("");
			const actions = [
				["Bill Run", __("New bill run")], ["Payment Receipt", __("Receive payment")],
				["Reading Cycle", __("Open reading cycle")], ["DISCO Bill", __("Enter DISCO bill")],
			].filter(([dt]) => frappe.model.can_create(dt));
			const menu = `<div class="dropdown ub-dmenu"><button class="ub-btn ub-btn-ghost" data-toggle="dropdown" data-bs-toggle="dropdown" aria-haspopup="true" aria-expanded="false">${__("Actions")} ▾</button>
				<div class="dropdown-menu dropdown-menu-right dropdown-menu-end">
					${actions.map(([dt, label]) => `<a class="dropdown-item" data-new="${dt}">${e(label)}</a>`).join("")}
					<a class="dropdown-item" data-page="ub-recovery">${__("Defaulters and recovery")}</a>
					<a class="dropdown-item" data-report="Management Dashboard">${__("All reports")}</a>
					<a class="dropdown-item ub-export">${__("Export figures (CSV)")}</a></div></div>`;
			const updated = d.stale ? __("Updating…") : __("Updated {0}", [e(d.updated_label || "")]);
			return `
			<header class="ub-dh">
				<div class="ub-dh-title">
					<div class="ub-dh-eyebrow">${e(d.society_name || d.society)}${d.data_label ? ` <span class="ub-badge">${e(d.data_label)}</span>` : ""}</div>
					<h1>${__("Overview")}</h1>
					<p>${e(d.subtitle)}</p>
				</div>
				<div class="ub-dh-tools">
					<label class="ub-slicer"><span>${__("Society")}</span><select class="ub-select ub-society">${opt(d.societies, d.society, "name", "label")}</select></label>
					<label class="ub-slicer"><span>${__("Billing month")}</span><select class="ub-select ub-month">${opt(d.months, d.month, "value", "label")}</select></label>
					<button class="ub-btn ub-btn-ghost ub-refresh ${d.stale ? "is-busy" : ""}" title="${__("Work the figures out again")}">${REFRESH}<span class="ub-updated">${updated}</span></button>
					${menu}
				</div>
			</header>
			<div class="ub-dgrid">
				${d.headline.map((h) => this.hero(h)).join("")}
				<section class="ub-card ub-dcard span-8">
					<div class="ub-dcard-head"><div><h2>${__("Billing and collection")}</h2><div class="ub-dsub">${__("Current bills issued and payments received, last 12 months")}</div></div>
						<div class="ub-seg" role="tablist"><button data-view="chart" class="${this.view === "chart" ? "on" : ""}">${__("Chart")}</button><button data-view="table" class="${this.view === "table" ? "on" : ""}">${__("Table")}</button></div></div>
					<div class="ub-legend"><span><i class="k-billed"></i>${__("Billed")}</span><span><i class="k-coll"></i>${__("Collected")}</span></div>
					<div class="ub-chart-box" data-chart="trend"></div>
				</section>
				<section class="ub-card ub-dcard span-4">${this.ageing(d.ageing)}</section>
				<section class="ub-ops span-12">${this.ops(d)}</section>
				<section class="ub-card ub-dcard span-4"><div class="ub-dcard-head"><div><h2>${__("Billing by charge")}</h2><div class="ub-dsub">${e(d.month_label)}</div></div></div>
					<div class="ub-chart-box" data-chart="mix"></div></section>
				<section class="ub-card ub-dcard span-4"><div class="ub-dcard-head"><div><h2>${__("Billing by category")}</h2><div class="ub-dsub">${e(d.month_label)}</div></div></div>
					<div class="ub-chart-box" data-chart="categories"></div></section>
				<section class="ub-card ub-dcard span-4"><div class="ub-dcard-head"><div><h2>${__("Collections by channel")}</h2><div class="ub-dsub">${e(d.month_label)}</div></div></div>
					<div class="ub-chart-box" data-chart="channels"></div></section>
				<section class="ub-card ub-dcard span-4">${this.cycle(d)}</section>
				<section class="ub-card ub-dcard span-4">${this.attention(d)}</section>
				<section class="ub-card ub-dcard span-4"><div class="ub-dcard-head"><div><h2>${__("Insights")}</h2><div class="ub-dsub">${__("What changed this month")}</div></div></div>
					<div class="ub-insights-slot"><div class="ub-skeleton s-line"></div><div class="ub-skeleton s-line"></div><div class="ub-skeleton s-line short"></div></div></section>
				<section class="ub-card ub-dcard span-12">${this.feeders(d)}</section>
			</div>`;
		}

		hero(h) {
			const dl = h.delta;
			const chip = dl
				? `<span class="ub-delta ${dl.good === true ? "good" : dl.good === false ? "bad" : "flat"}">${ARROW[dl.dir]}${e(dl.text)}</span><span class="ub-vs">${e(h.vs)}</span>`
				: `<span class="ub-vs">${__("No earlier month to compare")}</span>`;
			const gauge = h.key === "efficiency" ? `<div class="ub-gauge" data-gauge="${h.gauge == null ? "" : h.gauge}" data-target="${h.target}"></div>` : "";
			const tone = h.tone ? `<span class="ub-status ${e(h.tone)}"><i></i>${h.tone === "green" ? __("On target") : h.tone === "amber" ? __("Below target") : __("Well below target")}</span>` : "";
			return `<article class="ub-card ub-hero span-3" data-drill="${e(h.drill)}" role="button" tabindex="0" aria-label="${e(h.label)}: ${e(h.value)}">
				<div class="ub-hero-label">${e(h.label)}</div>
				<div class="ub-hero-row"><div><div class="ub-hero-value">${e(h.value)}</div><div class="ub-hero-sub">${e(h.sub)}</div></div>${gauge}</div>
				<div class="ub-hero-delta">${chip}${tone}</div>
				${h.spark ? `<div class="ub-chart-box ub-hero-spark" data-chart="spark" data-key="${e(h.key)}"></div>` : `<div class="ub-hero-spark"></div>`}
			</article>`;
		}

		ageing(a) {
			const bands = a.bands;
			const shown = bands.filter((b) => b.amount > 0);
			const seg = shown.length
				? `<div class="ub-segbar" role="img" aria-label="${e(shown.map((b) => `${b.label} ${b.value}`).join(", "))}">${bands
						.map((b, i) => (b.amount > 0 ? `<span class="${AGE_CLASS[i]}" style="flex:${b.amount}" data-tip="${e(`${b.label}\n${b.value} · ${b.pct}%\n${n0(b.accounts)} ${__("accounts")}`)}" tabindex="0">${b.pct >= 12 ? `${Math.round(b.pct)}%` : ""}</span>` : ""))
						.join("")}</div>`
				: `<div class="ub-empty">${__("Nobody owes anything.")}</div>`;
			const rows = bands
				.map((b, i) => `<tr class="${b.amount > 0 ? "" : "is-zero"}"><td><i class="ub-key ${AGE_CLASS[i]}"></i>${e(b.label)}</td><td class="num">${e(b.value)}</td><td class="num muted">${b.amount > 0 ? `${b.pct}%` : "–"}</td></tr>`)
				.join("");
			return `<div class="ub-dcard-head"><div><h2>${__("What consumers owe")}</h2><div class="ub-dsub">${__("By how long it has been due")}</div></div></div>
				<div class="ub-owe-total" data-drill="Arrears Ageing" role="button" tabindex="0">${e(a.total)}<span>${__("{0} accounts owing", [n0(a.owing)])}</span></div>
				${seg}
				<table class="ub-dtbl ub-agetbl"><tbody>${rows}</tbody></table>
				<div class="ub-dnote">${__("Credit held for {0} accounts: {1}", [n0(a.in_credit), e(a.credit)])}. ${__("Before go-live: brought over from the previous system, its age not known.")}</div>`;
		}

		ops(d) {
			const by = {};
			d.kpis.forEach((k) => (by[k.icon] = k));
			return OPS.filter(([key]) => by[key])
				.map(([key, drill]) => {
					const k = by[key];
					const empty = k.raw === null || k.raw === undefined;
					return `<div class="ub-op ${empty ? "is-empty" : ""}" data-drill="${e(drill)}" role="button" tabindex="0">
						<div class="ub-op-icon">${ub_icon(key)}</div>
						<div class="ub-op-body"><div class="ub-op-label">${e(k.label)}</div><div class="ub-op-value">${e(k.value)}</div>
						<div class="ub-op-note"><span class="ub-dot ${e(k.tone)}"></span>${e(k.note)}</div></div></div>`;
				})
				.join("");
		}

		cycle(d) {
			const steps = d.steps
				.map((s) => `<li class="ub-cstep ${s.state}"><span class="ub-cmark">${e(s.mark)}</span><div><div class="ub-ctitle">${e(s.title)}</div><div class="ub-cdetail">${e(s.detail)}</div></div></li>`)
				.join("");
			const done = d.steps.filter((s) => s.done).length;
			return `<div class="ub-dcard-head"><div><h2>${__("Billing cycle")}</h2><div class="ub-dsub">${e(d.month_label)} · ${__("{0} of {1} steps done", [done, d.steps.length])}</div></div></div>
				<div class="ub-cprogress"><i style="width:${(done / d.steps.length) * 100}%"></i></div>
				<ol class="ub-cycle">${steps}</ol>${d.footer ? `<div class="ub-dnote">${e(d.footer)}</div>` : ""}`;
		}

		attention(d) {
			const body = d.attention.length
				? `<div class="ub-attn">${d.attention
						.map((a, i) => `<button class="ub-attn-item ${e(a.tone)}" data-i="${i}"><span class="ub-attn-icon">${ub_icon(a.tone === "blue" ? "info" : "alert")}</span>
							<span><div class="ub-attn-title">${e(a.title)}</div><div class="ub-attn-detail">${e(a.detail)}</div></span><span class="ub-attn-go">›</span></button>`)
						.join("")}</div>`
				: `<div class="ub-allgood"><span style="width:20px;height:20px;display:inline-flex">${ub_icon("check")}</span>${__("Nothing needs attention right now.")}</div>`;
			return `<div class="ub-dcard-head"><div><h2>${__("Needs attention")}</h2><div class="ub-dsub">${d.attention.length ? __("Most serious first") : __("All clear")}</div></div></div>${body}`;
		}

		feeders(d) {
			const max = Math.max(1, ...d.feeders.map((f) => Number(String(f.billed_kwh).replace(/,/g, "")) || 0));
			const rows = d.feeders.length
				? d.feeders
						.map((f) => {
							const billed = Number(String(f.billed_kwh).replace(/,/g, "")) || 0;
							return `<tr><td><b>${e(f.name)}</b><div class="sub">${n0(f.connections)} ${__("connections")}</div></td>
								<td class="num">${e(f.input_kwh)}</td>
								<td class="num"><div class="ub-inbar"><span>${e(f.billed_kwh)}</span><i style="width:${(billed / max) * 100}%"></i></div></td>
								<td class="num">${e(f.loss)}</td><td><span class="ub-pill ${e(f.tone)}">${e(f.status)}</span></td></tr>`;
						})
						.join("")
				: `<tr><td colspan="5" class="ub-empty">${__("No bills in this cycle yet.")}</td></tr>`;
			return `<div class="ub-dcard-head"><div><h2>${__("Line losses by feeder")}</h2><div class="ub-dsub">${__("Energy received against energy billed")}</div></div>
					<button class="ub-btn ub-btn-ghost" data-report="Line Losses by Feeder and Transformer">${__("Open report")}</button></div>
				<div class="ub-tblwrap"><table class="ub-tbl ub-dtbl"><thead><tr><th>${__("Feeder")}</th><th class="num">${__("Input kWh")}</th><th class="num">${__("Billed kWh")}</th>
					<th class="num">${__("Loss")}</th><th>${__("Status")}</th></tr></thead><tbody>${rows}</tbody></table></div>
				${d.feeders_have_input ? "" : `<div class="ub-dnote">${__("Input kWh and loss appear once the DISCO bill and the feeder input units are recorded (DISCO Bill).")}</div>`}`;
		}

		// ------------------------------------------------------------ charts, drawn to their box's width
		draw() {
			const d = this.data;
			this.paint_trend();
			this.$root.find('[data-chart="spark"]').each((_i, el) => {
				const h = d.headline.find((x) => x.key === el.getAttribute("data-key"));
				el.innerHTML = this.spark(h, el.clientWidth || 200);
			});
			this.$root.find("[data-gauge]").each((_i, el) => (el.innerHTML = this.gauge(el.getAttribute("data-gauge"), el.getAttribute("data-target"))));
			this.$root.find('[data-chart="mix"]').html(this.hbars(d.mix, __("No bills in this month yet.")));
			this.$root.find('[data-chart="channels"]').html(this.hbars(d.channels.map((c) => ({ name: c.name, value: c.amount, raw: c.raw, pct: c.pct })), __("No payments received in this month.")));
			this.$root.find('[data-chart="categories"]').html(this.donut(d.categories));
		}

		paint_trend() {
			const box = this.$root.find('[data-chart="trend"]');
			if (!box.length || !this.data) return;
			this.$root.find("[data-view]").each((_i, b) => b.classList.toggle("on", b.getAttribute("data-view") === this.view));
			this.$root.find(".ub-legend").toggle(this.view === "chart");
			box.html(this.view === "table" ? this.trend_table(this.data.series) : this.trend(this.data.series, box[0].clientWidth || 640));
		}

		trend(series, W) {
			const H = W < 520 ? 240 : 330, P = { l: 44, r: 8, t: 12, b: 26 };
			const { step, max } = nice(Math.max(...series.map((m) => Math.max(m.billed, m.collected))));
			const n = series.length, band = (W - P.l - P.r) / n;
			const bw = Math.min(14, Math.max(3, (band - 10) / 2));
			const y = (v) => P.t + (1 - v / max) * (H - P.t - P.b);
			const grid = [0, 1, 2, 3, 4].map((k) => `<line class="grid${k ? "" : " base"}" x1="${P.l}" x2="${W - P.r}" y1="${y(step * k)}" y2="${y(step * k)}"/><text class="tick" x="${P.l - 8}" y="${y(step * k) + 4}" text-anchor="end">${compact(step * k)}</text>`).join("");
			const marks = series
				.map((m, i) => {
					const cx = P.l + band * i + band / 2;
					const tip = `${m.month}\n${__("Billed")}: ${rs(m.billed)}\n${__("Collected")}: ${rs(m.collected)}${m.bills ? `\n${__("Collection efficiency")}: ${m.rate}%` : ""}`;
					const label = i % (W < 520 ? 2 : 1) === 0 || i === n - 1 ? `<text class="tick${i === n - 1 ? " now" : ""}" x="${cx}" y="${H - 8}" text-anchor="middle">${e(m.month.split(" ")[0])}</text>` : "";
					return `${bar(cx - bw - 1, y(m.billed), bw, y(0) - y(m.billed), "m-billed")}${bar(cx + 1, y(m.collected), bw, y(0) - y(m.collected), "m-coll")}${label}
						<rect class="hit" x="${P.l + band * i}" y="${P.t}" width="${band}" height="${H - P.t - P.b}" data-tip="${e(tip)}" tabindex="0"/>`;
				})
				.join("");
			const last = series[n - 1];
			const lx = P.l + band * (n - 1) + band / 2;
			const label = last.billed ? `<text class="endlabel" x="${Math.min(lx, W - P.r - 2)}" y="${Math.max(y(last.billed) - 8, 10)}" text-anchor="end">${compact(last.billed)}</text>` : "";
			return `<svg class="ub-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${__("Billed and collected, last 12 months")}">${grid}${marks}${label}</svg>`;
		}

		trend_table(series) {
			const rows = series.slice().reverse()
				.map((m) => `<tr><td>${e(m.month)}</td><td class="num">${n0(m.bills)}</td><td class="num">${rs(m.billed)}</td><td class="num">${rs(m.payable)}</td><td class="num">${rs(m.collected)}</td><td class="num">${m.bills ? `${m.rate}%` : "–"}</td></tr>`)
				.join("");
			return `<div class="ub-tblwrap"><table class="ub-tbl ub-dtbl"><thead><tr><th>${__("Month")}</th><th class="num">${__("Bills")}</th><th class="num">${__("Billed")}</th>
				<th class="num">${__("Payable")}</th><th class="num">${__("Collected")}</th><th class="num">${__("Efficiency")}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
		}

		spark(h, W) {
			const values = h.spark || [];
			const H = 40;
			if (values.length < 2 || !values.some((v) => v)) return "";
			const hi = Math.max(...values), lo = Math.min(0, ...values), span = hi - lo || 1;
			const x = (i) => 2 + (i / (values.length - 1)) * (W - 8);
			const y = (v) => H - 4 - ((v - lo) / span) * (H - 10);
			const line = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
			const months = this.data.series.map((m) => m.month);
			const fmt = (v) => (h.key === "efficiency" ? `${v}%` : rs(v));
			const hits = values.map((v, i) => `<rect class="hit" x="${x(i) - (W - 8) / (values.length - 1) / 2}" y="0" width="${(W - 8) / (values.length - 1)}" height="${H}" data-tip="${e(`${months[i]}: ${fmt(v)}`)}"/>`).join("");
			const end = values.length - 1;
			return `<svg class="ub-svg ub-spark2" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
				<path class="s-area" d="${line} L${x(end)} ${H} L${x(0)} ${H}Z"/><path class="s-line" d="${line}"/>
				<circle class="s-dot" cx="${x(end)}" cy="${y(values[end])}" r="4"/>${hits}</svg>`;
		}

		gauge(value, target) {
			const S = 76, R = 30, C = 2 * Math.PI * R;
			const v = value === "" || value == null ? null : Math.max(0, Math.min(100, Number(value)));
			const arc = v == null ? 0 : (v / 100) * C;
			const t = (Number(target) / 100) * 2 * Math.PI - Math.PI / 2;
			const tx1 = S / 2 + (R - 7) * Math.cos(t), ty1 = S / 2 + (R - 7) * Math.sin(t), tx2 = S / 2 + (R + 7) * Math.cos(t), ty2 = S / 2 + (R + 7) * Math.sin(t);
			return `<svg class="ub-svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}" role="img" aria-label="${v == null ? "" : `${v}%`}">
				<circle class="g-track" cx="${S / 2}" cy="${S / 2}" r="${R}"/>
				${v ? `<circle class="g-fill" cx="${S / 2}" cy="${S / 2}" r="${R}" stroke-dasharray="${arc} ${C}" transform="rotate(-90 ${S / 2} ${S / 2})"/>` : ""}
				<line class="g-target" x1="${tx1}" y1="${ty1}" x2="${tx2}" y2="${ty2}" data-tip="${e(__("Target {0}%", [target]))}"/></svg>`;
		}

		hbars(rows, empty) {
			if (!rows.length) return `<div class="ub-empty">${e(empty)}</div>`;
			const max = Math.max(...rows.map((r) => r.raw), 1);
			return `<div class="ub-hbars">${rows
				.slice(0, 8)
				.map((r) => `<div class="ub-hbar" data-tip="${e(`${r.name}\n${r.value} · ${r.pct}%`)}" tabindex="0"><div class="ub-hbar-top"><span>${e(r.name)}</span><span class="v">${e(r.value)}<em>${r.pct}%</em></span></div>
					<svg class="ub-svg" width="100%" height="10" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="h-track" x="0" y="2" width="100" height="6" rx="3"/>${hbar(0, 2, (r.raw / max) * 100, 6, "h-fill")}</svg></div>`)
				.join("")}</div>`;
		}

		donut(rows) {
			if (!rows.length) return `<div class="ub-empty">${__("No bills in this month yet.")}</div>`;
			const S = 150, R = 58, W = 20, C = 2 * Math.PI * R, total = rows.reduce((s, r) => s + Math.max(r.raw, 0), 0) || 1;
			let at = 0;
			const gap = rows.length > 1 ? 2 : 0;
			const arcs = rows
				.map((r, i) => {
					const len = (Math.max(r.raw, 0) / total) * C;
					const seg = `<circle class="d-seg c${(i % 3) + 1}" cx="${S / 2}" cy="${S / 2}" r="${R}" stroke-width="${W}" stroke-dasharray="${Math.max(len - gap, 0)} ${C}" stroke-dashoffset="${-at}" transform="rotate(-90 ${S / 2} ${S / 2})" data-tip="${e(`${r.name}\n${r.value} · ${r.pct}%\n${n0(r.bills)} ${__("bills")}, ${n0(r.units)} kWh`)}" tabindex="0"/>`;
					at += len;
					return seg;
				})
				.join("");
			const legend = rows
				.map((r, i) => `<tr><td><i class="ub-key c${(i % 3) + 1}"></i>${e(r.name)}</td><td class="num">${e(r.value)}</td><td class="num muted">${r.pct}%</td></tr>`)
				.join("");
			const sum = rows.reduce((s, r) => s + r.raw, 0);
			return `<div class="ub-donut"><svg class="ub-svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}" role="img" aria-label="${e(rows.map((r) => `${r.name} ${r.pct}%`).join(", "))}">${arcs}
					<text class="d-total" x="${S / 2}" y="${S / 2 + 2}" text-anchor="middle">${e(rs(sum))}</text><text class="d-cap" x="${S / 2}" y="${S / 2 + 20}" text-anchor="middle">${__("current bills")}</text></svg>
				<table class="ub-dtbl"><tbody>${legend}</tbody></table></div>`;
		}

		export_csv() {
			const d = this.data;
			if (!d) return;
			const q = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
			const lines = [[__("Indicator"), __("Value"), __("Note")].map(q).join(",")];
			d.headline.forEach((h) => lines.push([h.label, h.value, h.sub].map(q).join(",")));
			d.kpis.forEach((k) => lines.push([k.label, k.value, k.note].map(q).join(",")));
			lines.push("", [__("Owed, by age"), __("Amount"), __("Accounts")].map(q).join(","));
			d.ageing.bands.forEach((b) => lines.push([b.label, b.amount, b.accounts].map(q).join(",")));
			lines.push("", [__("Charge"), __("Amount")].map(q).join(","));
			d.mix.forEach((m) => lines.push([m.name, m.raw].map(q).join(",")));
			lines.push("", [__("Month"), __("Bills"), __("Billed"), __("Payable"), __("Collected"), __("Collection rate %")].map(q).join(","));
			d.series.forEach((m) => lines.push([m.month, m.bills, m.billed, m.payable, m.collected, m.rate].map(q).join(",")));
			const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
			const a = document.createElement("a");
			a.href = URL.createObjectURL(blob);
			a.download = `dashboard-${d.society}-${d.month}.csv`;
			a.click();
			URL.revokeObjectURL(a.href);
		}
	};
})();
