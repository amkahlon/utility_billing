// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Bill run wizard (FS-RUN-01 to 08), designed after the mock-up "Bill run wizard" (2026-11-16): the run's six steps, its figures, and the
// bills that need a look before approval, each accepted or held out of the run. Calculation, approval and posting stay on the ordinary
// Bill Run, so maker-checker rules are unchanged. Data: utility_billing.utility_billing.billrun_wizard.

window.UtilityBillingRunWizard = class UtilityBillingRunWizard {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root ub-br"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.d = null;
		this.kind = "";
		const root = this.$root;
		root.on("click", ".ub-br-chip", (e) => {
			this.kind = $(e.currentTarget).attr("data-k");
			this.paint();
		});
		root.on("click", ".ub-br-decide", (e) => this.decide($(e.currentTarget).attr("data-key"), $(e.currentTarget).attr("data-d")));
		root.on("click", ".ub-br-bulk", (e) => this.decide_many($(e.currentTarget).attr("data-d")));
		root.on("click", ".ub-br-calc", () => this.calculate());
		root.on("click", ".ub-br-print", () => this.print());
		root.on("click", ".ub-link", (e) => {
			const [dt, name] = $(e.currentTarget).attr("data-go").split("|");
			if (dt === "c360") return frappe.set_route("ub-consumer-360", { account: name });
			frappe.set_route(name ? ["Form", dt, name] : ["List", dt]);
		});
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	n(v) {
		return v === null || v === undefined || v === "" ? "—" : Math.round(Number(v)).toLocaleString("en-US");
	}
	rs(v) {
		const x = Number(v || 0);
		if (Math.abs(x) >= 1e6) return "Rs " + (x / 1e6).toFixed(1) + "M";
		if (Math.abs(x) >= 1e4) return "Rs " + (x / 1e3).toFixed(0) + "K";
		return "Rs " + this.n(x);
	}

	load(run) {
		if (run) this.run = run;
		if (!this.run) return this.$root.html(`<div class="ub-card ub-empty">${__("Open a bill run from its record to see it here.")}</div>`);
		frappe.xcall("utility_billing.utility_billing.billrun_wizard.load", { name: this.run }).then((d) => {
			this.d = d;
			this.paint();
		});
	}

	steps(d) {
		const now = d.steps.findIndex((s) => !s.done);
		return `<ol class="ub-br-steps">${d.steps
			.map((s, i) => `<li class="${s.done ? "done" : i === now ? "now" : ""}"><span>${s.done ? "✓" : i + 1}</span>${this.esc(s.label)}</li>`)
			.join("")}</ol>`;
	}

	kpis(d) {
		const c = d.cycle || {};
		const kpi = (label, value, note, tone) => `<div class="ub-kpi"><div class="ub-kpi-top"><span class="ub-kpi-label">${label}</span></div>
			<div class="ub-kpi-value">${value}</div><div class="ub-kpi-note ${tone || ""}">${note}</div></div>`;
		const groups = d.groups.slice(0, 3).map((g) => `${this.esc(__(g.group))} ${this.rs(g.amount)}`).join(" · ");
		const resolved = d.exceptions.length - d.left;
		return `<div class="ub-kpis">
			${kpi(__("Connections in cycle"), this.n(c.total_readings), d.cycle ? __("{0} read · {1} estimated · {2} defective", [this.n(c.valid_readings), this.n(c.estimated_readings), this.n(d.defective)]) : __("no reading cycle (flat-rate utility)"))}
			${kpi(__("Bills calculated"), this.n(d.bill_count), d.calculated_on ? __("on {0}", [moment(d.calculated_on).format("D MMM, HH:mm")]) : __(d.calculation_status || "Not calculated"), d.calculation_status === "Calculated" ? "" : "red")}
			${kpi(__("Current billing"), this.rs(d.total_current), groups || "—")}
			${kpi(__("Exceptions to review"), d.left ? __("{0} left", [d.left]) : d.exceptions.length ? __("All done") : "0", __("{0} of {1} resolved", [resolved, d.exceptions.length]), d.left ? "red" : "")}
		</div>`;
	}

	table(d) {
		if (d.calculation_status !== "Calculated") return `<div class="ub-kpi-note">${__("Calculate the bills first; their exceptions show here.")}</div>`;
		if (!d.exceptions.length) return `<div class="ub-allgood">${window.ub_icon ? window.ub_icon("check") : ""} ${__("No bills need a look in this run.")}</div>`;
		const kinds = {};
		d.exceptions.forEach((e) => (kinds[e.kind] = (kinds[e.kind] || 0) + 1));
		const chip = (k, label, n) => `<button class="ub-c360-tab ub-br-chip ${this.kind === k ? "on" : ""}" data-k="${this.esc(k)}">${this.esc(label)} ${n}</button>`;
		const chips = chip("", __("All"), d.exceptions.length) + Object.entries(kinds).map(([k, n]) => chip(k, __(k), n)).join("");
		const tone = { "Very high bill": "red", "Zero use": "amber", Estimated: "amber", "Defective meter": "red", "Couldn't bill": "red" };
		const rows = d.exceptions.filter((e) => !this.kind || e.kind === this.kind);
		const act = (e) => {
			if (e.decision) return `<span class="ub-pill ${e.decision === "Held" ? "amber" : "green"}">${this.esc(__(e.decision))}</span>
				${d.can_review ? `<button class="ub-btn ub-btn-sm ub-br-decide" data-key="${this.esc(e.key)}" data-d="">${__("Undo")}</button>` : ""}`;
			if (!d.can_review) return "";
			return `${e.kind === "Couldn't bill" ? "" : `<button class="ub-btn ub-btn-sm ub-br-decide" data-key="${this.esc(e.key)}" data-d="Accepted">${__("Accept")}</button>`}
				<button class="ub-btn ub-btn-sm ub-br-decide" data-key="${this.esc(e.key)}" data-d="Held">${__("Hold")}</button>
				${e.reading ? `<a class="ub-btn ub-btn-sm" href="/app/ub-review?cycle=${encodeURIComponent((d.cycle || {}).name || "")}">${__("Correct")}</a>` : ""}`;
		};
		const body = rows
			.map((e) => `<tr><td><span class="ub-pill ${tone[e.kind] || "amber"}">${this.esc(__(e.kind))}</span>${e.problem ? `<div class="sub">${this.esc(e.problem)}</div>` : ""}</td>
				<td><a class="ub-link" data-go="c360|${this.esc(e.connection)}"><b>${this.esc(e.consumer_name || e.connection)}</b></a><div class="sub">${this.esc(e.connection)}</div></td>
				<td>${this.esc(e.house || "")}</td><td class="num">${this.n(e.units)}</td><td class="num">${this.n(e.average)}</td>
				<td class="num">${e.bill ? `<a class="ub-link" data-go="Utility Bill|${this.esc(e.bill)}">${this.n(e.amount)}</a>` : "—"}</td>
				<td class="ub-br-act">${act(e)}</td></tr>`)
			.join("");
		// the same decision for every exception shown (increment 53): the chip above narrows them to one kind first
		const open = rows.filter((e) => !e.decision);
		const acceptable = open.filter((e) => e.kind !== "Couldn't bill");
		const decided = rows.filter((e) => e.decision);
		const what = this.kind ? __(this.kind).toLowerCase() : __("shown");
		const bulk = d.can_review && rows.length > 1
			? `<div class="ub-br-bulkbar">
				${acceptable.length ? `<button class="ub-btn ub-btn-sm ub-btn-primary ub-br-bulk" data-d="Accepted">${__("Accept all {0} {1}", [acceptable.length, what])}</button>` : ""}
				${open.length ? `<button class="ub-btn ub-btn-sm ub-br-bulk" data-d="Held">${__("Hold all {0} {1}", [open.length, what])}</button>` : ""}
				${decided.length ? `<button class="ub-btn ub-btn-sm ub-br-bulk" data-d="">${__("Undo {0} decisions", [decided.length])}</button>` : ""}
				<span class="ub-kpi-note">${this.kind ? __("Only the {0} bills are included.", [__(this.kind)]) : __("Choose a kind above to decide one kind at a time.")}</span></div>`
			: "";
		return `<div class="ub-c360-tabs">${chips}</div>${bulk}<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr><th>${__("Exception")}</th><th>${__("Consumer")}</th>
			<th>${__("House")}</th><th class="num">${__("Units")}</th><th class="num">${__("Avg 3 mo.")}</th><th class="num">${__("Bill (Rs)")}</th><th>${__("Action")}</th></tr></thead>
			<tbody>${body}</tbody></table></div>
			<div class="ub-kpi-note">${__("Held bills are left out of this run and can be billed in a supplementary run. Holding one means calculating the run again. Posting locks this cycle's tariffs and settings.")}</div>`;
	}

	paint() {
		const d = this.d;
		const c = d.cycle;
		const facts = [
			c && c.reading_from ? __("Reading cycle {0}–{1}", [moment(c.reading_from).format("D MMM"), moment(c.reading_to).format("D MMM")]) : null,
			__("issue date {0}", [moment(d.issue_date).format("D MMM")]),
			__("due date {0}", [moment(d.due_date).format("D MMM")]),
			__("started by {0}", [this.esc(d.owner)]),
		].filter(Boolean);
		const stale = d.calculation_status !== "Calculated";
		const approve = d.docstatus === 0 && !stale
			? `<a class="ub-btn ub-btn-primary" href="/app/bill-run/${encodeURIComponent(d.name)}">${d.left ? __("Submit for approval ({0} left)", [d.left]) : __("Go to approval")}</a>` : "";
		this.$root.html(`
			<div class="ub-head"><div><div class="ub-hello">${this.esc(d.name)} · <span class="ub-pill ${d.docstatus === 1 ? "green" : "blue"}">${this.esc(__(d.state))}</span></div>
				<h1>${__("Bill run: {0}, {1}", [this.esc(d.utility), this.esc(d.month)])}</h1><p>${facts.join(" · ")}</p></div>
				<div class="ub-tools"><a class="ub-btn ub-link" data-go="Bill Run|${this.esc(d.name)}">${__("Run record")}</a></div></div>
			${this.steps(d)}
			${d.posting_status === "Posting" ? `<div class="ub-card ub-kpi-note">${__("Posting the bills in the background. Reload in a few minutes.")}</div>` : ""}
			${d.calculation_status === "Calculating" ? `<section class="ub-card ub-br-progress"></section>` : ""}
			${this.kpis(d)}
			<section class="ub-card"><div class="ub-cardhead"><h2>${__("Review exceptions")}</h2></div>${this.table(d)}
				<div class="ub-cash-actions">
					${d.can_calculate && d.calculation_status !== "Calculating" ? `<button class="ub-btn ${stale ? "ub-btn-primary" : ""} ub-br-calc">${stale ? __("Calculate bills") : __("Recalculate")}</button>` : ""}
					${d.bill_count && !stale ? `<button class="ub-btn ub-br-print">${__("Print bills")}</button>` : ""}
					${approve}</div></section>`);
		if (d.calculation_status === "Calculating") this.watch();
	}

	// Accept, hold or undo every exception shown (of the chosen kind, or all), after a confirmation (increment 53).
	decide_many(decision) {
		const rows = this.d.exceptions.filter((e) => !this.kind || e.kind === this.kind);
		const keys = (decision === ""
			? rows.filter((e) => e.decision)
			: rows.filter((e) => !e.decision && (decision !== "Accepted" || e.kind !== "Couldn't bill"))).map((e) => e.key);
		if (!keys.length) return;
		const ask = {
			Accepted: __("Accept these {0} bills as they are? They stay in the run.", [keys.length]),
			Held: __("Hold these {0} accounts out of the run? They are billed later in a supplementary run, and this run must be calculated again.", [keys.length]),
			"": __("Undo the decision on these {0} exceptions?", [keys.length]),
		}[decision];
		frappe.confirm(ask, () =>
			frappe
				.call({ method: "utility_billing.utility_billing.billrun_wizard.decide_many", args: { name: this.d.name, keys: JSON.stringify(keys), decision },
				        freeze: true, freeze_message: __("Saving {0} decisions…", [keys.length]) })
				.then(({ message: d }) => {
					this.d = d;
					this.paint();
					const said = { Accepted: __("{0} accepted.", [d.changed]), Held: __("{0} held out of the run. Recalculate before approval.", [d.changed]),
					               "": __("{0} decisions undone.", [d.changed]) }[decision];
					frappe.show_alert({ message: said, indicator: decision === "Held" ? "orange" : "green" }, 7);
				})
		);
	}

	decide(key, decision) {
		frappe.xcall("utility_billing.utility_billing.billrun_wizard.decide", { name: this.d.name, key, decision }).then((d) => {
			this.d = d;
			this.paint();
			if (decision === "Held") frappe.show_alert({ message: __("Held out of the run. Recalculate before approval."), indicator: "orange" });
		});
	}

	calculate() {
		frappe
			.call({ method: "utility_billing.utility_billing.billing.calculate_run", args: { run: this.d.name }, freeze: true, freeze_message: __("Calculating bills...") })
			.then(() => this.load()); // a large run calculates in the background: load() shows its progress (increment 52)
	}

	// The calculation running in the background: a progress bar on the page until it ends, then the run as it now stands.
	watch() {
		const name = this.d.name;
		const bar = (p) =>
			this.$root.find(".ub-br-progress").html(`<div class="ub-cardhead"><h2>${__("Calculating bills")}</h2></div>
				<div class="progress" style="height:10px"><div class="progress-bar" style="width:${utility_billing.calculation_percent(p)}%"></div></div>
				<p class="ub-kpi-note" style="margin-top:8px">${utility_billing.calculation_text(p)}</p>`);
		bar(this.last_progress || { state: "Queued" });
		if (this.watching === name) return;
		this.watching = name;
		utility_billing.watch_calculation(
			name,
			(p) => {
				this.last_progress = p;
				bar(p);
			},
			(p) => {
				this.watching = null;
				this.last_progress = null;
				if (p.state === "Failed") frappe.msgprint({ title: __("Calculation didn't complete"), indicator: "red", message: frappe.utils.escape_html(p.error || "") });
				else frappe.show_alert({ message: __("Bills calculated."), indicator: "green" }, 7);
				this.load(name);
			},
			() => {
				const here = this.d && this.d.name === name && frappe.get_route_str().startsWith("ub-billrun");
				if (!here) this.watching = null;
				return here;
			}
		);
	}

	print() {
		const args = new URLSearchParams({ run: this.d.name });
		frappe.xcall("utility_billing.utility_billing.bill_print.run_print_groups", { run: this.d.name }).then((g) => {
			if (g.total > g.limit) return frappe.set_route("Form", "Bill Run", this.d.name).then(() => frappe.show_alert(__("Too many bills for one PDF; use Print Bills here to print a block or route at a time.")));
			window.open(`/api/method/utility_billing.utility_billing.bill_print.print_run_bills?${args}`);
		});
	}
};
