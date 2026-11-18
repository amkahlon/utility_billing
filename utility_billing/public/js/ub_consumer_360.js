// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Consumer 360 (FS-MST-12, 13, 15), redesigned after the mock-up "Consumer 360 view" (2026-11-16): an identity header with the actions
// an officer needs, tabs, and an Overview of one account: what is payable, the latest bill line by line, the connection and meter,
// twelve months of units and what happened lately. Kept from before: several accounts per consumer (a switcher with each balance),
// flags and open items (an alert strip), representatives, the ledger, deposits, occupancy, and masked personal data.
// Data: utility_billing.utility_billing.consumer360.view. Loaded by page/ub_consumer_360/ub_consumer_360.js.

window.UtilityBillingConsumer360 = class UtilityBillingConsumer360 {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root ub-c360"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.consumer = null;
		this.account = null;
		this.tab = "overview";
		this.v = null;
		this.timer = null;
		const root = this.$root;
		root.on("input", ".ub-c360-q", (e) => {
			clearTimeout(this.timer);
			const q = e.target.value;
			this.timer = setTimeout(() => this.search(q), 250);
		});
		root.on("click", ".ub-c360-pick", (e) => this.open($(e.currentTarget).attr("data-c"), $(e.currentTarget).attr("data-a") || null));
		root.on("keydown", ".ub-c360-q", (e) => {
			const hits = this.$root.find(".ub-c360-pick");
			if (!hits.length) return;
			let i = hits.index(hits.filter(".on"));
			if (e.key === "ArrowDown" || e.key === "ArrowUp") {
				e.preventDefault();
				i = e.key === "ArrowDown" ? Math.min(i + 1, hits.length - 1) : Math.max(i - 1, 0);
				hits.removeClass("on").eq(i).addClass("on")[0].scrollIntoView({ block: "nearest" });
			} else if (e.key === "Enter") {
				hits.eq(Math.max(i, 0)).trigger("click");
			} else if (e.key === "Escape") {
				this.$root.find(".ub-c360-results").html("");
			}
		});
		// Ctrl+K or "/" anywhere on the page goes to the search box, as in the mock-up's top bar.
		document.addEventListener("keydown", (e) => {
			if (frappe.get_route_str() !== "ub-consumer-360") return;
			const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.isContentEditable;
			if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) {
				e.preventDefault();
				this.$root.find(".ub-c360-q").trigger("focus").trigger("select");
			}
		});
		root.on("click", ".ub-c360-acc", (e) => this.open(this.consumer, $(e.currentTarget).attr("data-a")));
		root.on("click", ".ub-c360-tab", (e) => {
			this.tab = $(e.currentTarget).attr("data-t");
			this.paint();
		});
		root.on("click", ".ub-c360-act", (e) => this.act($(e.currentTarget).attr("data-act")));
		root.on("click", ".ub-link", (e) => {
			const [dt, name] = $(e.currentTarget).attr("data-go").split("|");
			frappe.set_route(name ? ["Form", dt, name] : ["List", dt]);
		});
	}

	// ---------------------------------------------------------------- helpers
	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	rs(v) {
		return "Rs " + Math.round(Number(v || 0)).toLocaleString("en-US");
	}
	n(v) {
		return Math.round(Number(v || 0)).toLocaleString("en-US");
	}
	d(v) {
		return v ? frappe.datetime.str_to_user(v) : "";
	}
	day(v) {
		return v ? moment(v).format("D MMM YYYY") : "";
	}
	link(dt, name, text) {
		return `<a class="ub-link" data-go="${this.esc(dt)}|${this.esc(name)}">${this.esc(text || name)}</a>`;
	}
	pill(text, tone) {
		return `<span class="ub-pill ${tone || ""}">${this.esc(text)}</span>`;
	}
	rows(pairs) {
		return `<dl class="ub-c360-dl">${pairs
			.filter((p) => p && p[1] !== null && p[1] !== undefined && p[1] !== "")
			.map(([k, v, cls]) => `<dt>${this.esc(k)}</dt><dd class="${cls || ""}">${v}</dd>`)
			.join("")}</dl>`;
	}

	// ---------------------------------------------------------------- loading
	shell() {
		return `<div class="ub-c360-search"><input class="ub-select ub-c360-q" placeholder="${__("Find a consumer: name, CNIC, phone, house, reference or meter number")}  (Ctrl+K)" autocomplete="off"></div>
			<div class="ub-c360-results"></div><div class="ub-c360-body"></div>`;
	}

	go(w) {
		if (w && w.account) {
			return frappe.xcall("utility_billing.utility_billing.consumer360.consumer_of", { account: w.account }).then((r) => this.open(r.consumer, r.account));
		}
		this.open(w && w.consumer);
	}

	search(q) {
		const out = this.$root.find(".ub-c360-results");
		if ((q || "").trim().length < 2) return out.html("");
		const why = { reference: __("reference"), meter: __("meter"), house: __("house"), cnic: __("CNIC"), phone: __("phone"), name: __("name") };
		frappe.xcall("utility_billing.utility_billing.consumer360.search", { text: q }).then((rows) => {
			if (q !== this.$root.find(".ub-c360-q").val()) return; // a newer search is on its way
			const money = (v) => (v == null ? "" : `<span class="ub-c360-bal ${v > 0 ? "due" : ""}">${v < 0 ? __("Credit") + " " : ""}Rs ${Math.abs(Math.round(v)).toLocaleString("en-US")}</span>`);
			out.html(
				rows.length
					? `<div class="ub-card ub-c360-found" role="listbox">${rows
							.map((r, i) => `<div class="ub-c360-pick ${i === 0 ? "on" : ""}" role="option" data-c="${this.esc(r.name)}" data-a="${this.esc(r.account || "")}">
								<div class="ub-c360-hit"><b>${this.esc(r.consumer_name)}</b>
									<span class="sub">${r.account ? `${this.esc(r.reference_no || r.account)} · ${this.esc(r.utility || "")}${r.house ? " · " + __("house {0}", [this.esc(r.house)]) : ""}`
										: __("no account yet")}${r.mobile_no ? " · " + this.esc(r.mobile_no) : ""}</span></div>
								<div class="ub-c360-hit-end">${r.account_status && r.account_status !== "Active" ? `<span class="ub-pill amber">${this.esc(__(r.account_status))}</span>` : ""}
									${money(r.balance)}<span class="ub-c360-why">${__("matched {0}", [why[r.matched] || ""])}</span></div></div>`)
							.join("")}<div class="ub-kpi-note">${__("↑ ↓ to move, Enter to open, Esc to close")}</div></div>`
					: `<div class="ub-card ub-empty">${__("No consumer matches.")}</div>`
			);
		});
	}

	open(consumer, account) {
		if (!this.$root.find(".ub-c360-q").length) this.$root.html(this.shell());
		this.$root.find(".ub-c360-results").html("");
		const body = this.$root.find(".ub-c360-body");
		if (!consumer) return body.html(`<div class="ub-card ub-empty">${__("Search for a consumer to see everything about them on one page.")}</div>`);
		if (consumer !== this.consumer) this.tab = "overview";
		this.consumer = consumer;
		body.html(`<div class="ub-card ub-empty">${__("Loading…")}</div>`);
		frappe
			.xcall("utility_billing.utility_billing.consumer360.view", { consumer, account: account || null })
			.then((v) => {
				this.v = v;
				this.account = v.selected;
				this.paint();
			})
			.catch(() => body.html(`<div class="ub-card ub-empty">${__("This consumer could not be loaded.")}</div>`));
	}

	paint() {
		this.$root.find(".ub-c360-body").html(this.render(this.v));
	}

	// ---------------------------------------------------------------- actions (each within the user's own rights)
	act(what) {
		const v = this.v;
		const o = v.overview;
		if (what === "print" && o && o.bill)
			window.open(`/api/method/utility_billing.utility_billing.bill_print.download_bill?name=${encodeURIComponent(o.bill.name)}`);
		if (what === "statement" && o)
			window.open(`/api/method/utility_billing.utility_billing.account_print.download_statement?connection=${encodeURIComponent(o.name)}`);
		if (what === "request" && o) frappe.new_doc("Service Request", { connection: o.name });
		if (what === "pay" && o)
			frappe.new_doc("Payment Receipt", { reference_no: o.reference_no || o.name, payment_channel: "Counter",
				amount: o.payable.amount_due > 0 ? o.payable.amount_due : null });
		if (what === "record") frappe.set_route("Form", "Consumer", v.consumer.name);
	}

	// ---------------------------------------------------------------- the page
	render(v) {
		const c = v.consumer;
		const o = v.overview;
		const initials = (c.consumer_name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
		const current = v.occupancy.find((x) => !x.to_date && x.is_billed) || v.occupancy.find((x) => !x.to_date) || {};
		const statusTone = { Active: "green", Disconnected: "amber", Closed: "red", Applied: "blue" };
		const facts = o
			? [
					[o.property_no ? `${__("House")} ${o.property_no}` : "", o.block ? `${__("Block")} ${o.block}` : ""].filter(Boolean).join(", "),
					`${__("Ref.")} ${o.reference_no || o.name}`,
					o.category_name || o.category,
					o.load ? __("Sanctioned load {0} kW", [o.load]) : "",
					c.mobile_no,
			  ]
			: [c.mobile_no, c.email];
		const actions = [
			v.can.print && o && o.bill ? ["print", __("Print bill")] : null,
			o ? ["statement", __("Statement")] : null,
			v.can.request && o ? ["request", __("New request")] : null,
			["record", __("Consumer record")],
		].filter(Boolean);
		const header = `
		<div class="ub-c360-crumb">${this.link("Consumer", "", __("Consumers"))} / ${this.esc(o ? o.property_no || o.reference_no : c.name)}</div>
		<section class="ub-card ub-c360-head">
			<div class="ub-c360-id">
				<div class="ub-c360-avatar">${this.esc(initials)}</div>
				<div><div class="ub-c360-name"><h1>${this.esc(c.consumer_name)}</h1>${o ? this.pill(__(o.status), statusTone[o.status]) : ""}
					${current.occupant_role ? this.pill(__(current.occupant_role)) : ""}${c.cash_only ? this.pill(__("Cash only"), "amber") : ""}</div>
				<div class="ub-c360-facts">${facts.filter(Boolean).map((x) => this.esc(x)).join(" · ")}</div></div>
			</div>
			<div class="ub-c360-actions">${actions.map(([k, t]) => `<button class="ub-btn ub-c360-act" data-act="${k}">${this.esc(t)}</button>`).join("")}
				${v.can.pay && o ? `<button class="ub-btn ub-btn-primary ub-c360-act" data-act="pay">${__("Record payment")}</button>` : ""}</div>
		</section>`;
		const switcher =
			v.accounts.length > 1
				? `<div class="ub-c360-accounts">${v.accounts
						.map((a) => `<button class="ub-c360-acc ${a.name === v.selected ? "on" : ""}" data-a="${this.esc(a.name)}">
							<b>${this.esc(a.utility)}</b> ${this.esc(a.property_no || "")}<span>${this.rs(a.owes)}</span></button>`)
						.join("")}<div class="ub-c360-total">${__("All accounts")}: <b>${this.rs(v.total_owed)}</b></div></div>`
				: "";
		const alerts =
			o && o.alerts.length
				? `<div class="ub-c360-alerts">${o.alerts
						.map((a) => `<span class="ub-pill ${a.tone} ${a.go ? "ub-link" : ""}" ${a.go ? `data-go="${this.esc(a.go[0])}|${this.esc(a.go[1])}"` : ""}>${this.esc(a.text)}</span>`)
						.join("")}</div>`
				: "";
		const tabs = [
			["overview", __("Overview")], ["bills", __("Bills")], ["payments", __("Payments")], ["readings", __("Readings")],
			["meter", __("Meter and connection")], ["requests", __("Requests")], ["complaints", __("Complaints")], ["messages", __("Messages")],
			["ledger", __("Ledger")], ["profile", __("Profile")],
		];
		const tabbar = `<nav class="ub-c360-tabs">${tabs
			.map(([k, t]) => `<button class="ub-c360-tab ${this.tab === k ? "on" : ""}" data-t="${k}">${this.esc(t)}</button>`)
			.join("")}</nav>`;
		return header + switcher + alerts + tabbar + `<div class="ub-c360-pane">${this[`tab_${this.tab}`](v)}</div>`;
	}

	// ---------------------------------------------------------------- Overview: the mock-up's first screen
	tab_overview(v) {
		const o = v.overview;
		if (!o) return `<div class="ub-card ub-empty">${__("This consumer has no account yet.")}</div>`;
		const p = o.payable;
		const lp = p.last_payment;
		const due = p.due_date ? __("Payable by {0}", [this.day(p.due_date)]) : __("Owed now");
		const month = p.bill_month ? p.bill_month.split(" ")[0].slice(0, 3) : "";
		const payable = `<section class="ub-card ub-c360-pay">
			<div class="ub-kpi-note">${this.esc(due)}</div>
			<div class="ub-c360-big">${this.rs(p.amount_due > 0 ? p.amount_due : 0)}</div>
			${p.amount_due < 0 ? `<div class="ub-kpi-note green">${__("In credit by {0}", [this.rs(-p.amount_due)])}</div>` : ""}
			${p.after_due > p.within_due && p.amount_due > 0 ? `<div class="ub-kpi-note">${__("{0} after due date", [this.rs(p.after_due)])}</div>` : ""}
			${this.rows([
				[month ? __("Current bill ({0})", [month]) : __("Current bill"), this.n(p.current), "num"],
				[__("Arrears"), this.n(p.arrears), "num"],
				[__("Security deposit held"), this.n(p.deposit), "num"],
				[__("Last payment"), lp ? `${this.n(lp.amount)} · ${this.esc(moment(lp.payment_date).format("D MMM"))} · ${this.esc(lp.mode || "")}` : "—", "num"],
				p.former ? [__("Owed by former occupants"), this.n(p.former), "num"] : null,
			])}</section>`;
		const b = o.bill;
		const billCard = b
			? `<section class="ub-card"><h2>${__("{0} bill", [this.esc(b.month.split(" ")[0])])}${b.final ? " " + this.pill(__("Final bill"), "amber") : ""}</h2>
				<table class="ub-tbl ub-c360-bill"><tbody>
				<tr><td>${b.previous !== null ? __("Units: {0} − {1}", [this.n(b.present), this.n(b.previous)]) : this.esc(b.consumption_type || __("Units"))}</td><td class="num">${this.n(b.solar ? b.solar.imported : b.units)} kWh</td></tr>
				${b.solar ? `<tr><td>${__("Solar exported")}</td><td class="num">${this.n(b.solar.exported)} kWh</td></tr>
				<tr><td>${b.solar.method === "Buyback rate" ? __("Buyback credit") : __("Solar units credited")}</td><td class="num">${b.solar.method === "Buyback rate" ? this.rs(b.solar.buyback_credit) : this.n(b.solar.credited) + " kWh"}</td></tr>
				<tr><td>${__("Units billed")}</td><td class="num">${this.n(b.units)} kWh</td></tr>
				${b.solar.carried_out ? `<tr><td>${__("Solar credit carried forward")}</td><td class="num">${this.n(b.solar.carried_out)} kWh</td></tr>` : ""}` : ""}
				${b.lines.map((l) => `<tr><td>${this.esc(l.label)}${l.note ? `<div class="sub">${this.esc(l.note)}</div>` : ""}</td><td class="num">${this.n(l.amount)}</td></tr>`).join("")}
				${b.tax ? `<tr><td>${__("Taxes")}</td><td class="num">${this.n(b.tax)}</td></tr>` : ""}
				<tr class="strong"><td>${__("Current month bill")}</td><td class="num">${this.n(b.current)}</td></tr>
				<tr><td>${b.installment ? __("Installment") : __("Arrears")}</td><td class="num">${this.n(b.installment || b.arrears)}</td></tr>
				<tr class="strong"><td>${__("Payable within due date")}</td><td class="num">${this.n(b.within_due)}</td></tr>
				<tr><td>${__("Surcharge")}</td><td class="num">${this.n(b.surcharge)}</td></tr>
				<tr class="strong"><td>${__("Payable after due date")}</td><td class="num">${this.n(b.after_due)}</td></tr>
				</tbody></table>${b.note ? `<div class="ub-kpi-note">${this.esc(b.note)}</div>` : ""}</section>`
			: `<section class="ub-card"><h2>${__("Latest bill")}</h2><div class="ub-empty">${__("No bill posted yet.")}</div></section>`;
		const meterCard = `<section class="ub-card"><h2>${__("Connection and meter")}</h2>${this.meterRows(o)}</section>`;
		return `<div class="ub-c360-grid3">${payable}${billCard}${meterCard}</div>
			<div class="ub-c360-grid2">
				<section class="ub-card"><h2>${__("Consumption, last 12 months (kWh)")}</h2>${this.chart(o.consumption)}</section>
				<section class="ub-card"><h2>${__("Recent activity")}</h2>${this.timeline(o.activity)}</section>
			</div>`;
	}

	meterRows(o) {
		const m = o.meter;
		const net = o.network;
		return this.rows([
			[__("Meter no."), m ? this.esc(m.serial_no) : __("No meter (not metered)")],
			m ? [__("Meter status"), this.esc(__(m.status)) + (m.status === "Installed" ? ", " + __("working") : "")] : null,
			m ? [__("Multiplying factor"), this.esc(m.multiplier)] : null,
			[__("Feeder"), this.esc(net.feeder || "—")],
			[__("Transformer"), net.transformer ? `${this.esc(net.transformer)}${net.kva ? ` (${this.esc(net.kva)} kVA)` : ""}` : "—"],
			[__("Route"), this.esc(net.route || "—")],
			[__("Tariff category"), this.esc(o.category_name || o.category)],
			[__("Sanctioned / max demand"), `${this.esc(o.load || 0)} / ${net.mdi ? this.esc(net.mdi) : "—"} kW`],
			[__("Notifications"), this.esc(o.notifications.join(" · ") || __("none"))],
		]);
	}

	chart(rows) {
		if (!rows.some((r) => r.units !== null)) return `<div class="ub-empty">${__("No readings yet.")}</div>`;
		const hi = Math.max(...rows.map((r) => r.units || 0), 1);
		return `<div class="ub-c360-bars">${rows
			.map((r, i) => {
				const none = r.units === null;
				const h = none ? 0 : Math.max(2, Math.round((r.units / hi) * 150));
				const last = i === rows.length - 1;
				const tip = none ? __("{0}: not read", [r.full]) : `${r.full}: ${this.n(r.units)} kWh${r.estimated ? " (" + __("estimated") + ")" : ""}`;
				return `<div class="ub-c360-bar" title="${this.esc(tip)}">
					<span class="v">${none ? "" : this.n(r.units)}</span><i class="${last ? "last" : ""} ${r.estimated ? "est" : ""}" style="height:${h}px"></i><span class="m">${this.esc(r.month)}</span></div>`;
			})
			.join("")}</div>${rows.some((r) => r.estimated) ? `<div class="ub-kpi-note">${__("Striped: estimated, not read")}</div>` : ""}`;
	}

	timeline(items) {
		if (!items.length) return `<div class="ub-empty">${__("Nothing yet.")}</div>`;
		const tone = { bill: "blue", reading: "green", payment: "green", request: "amber" };
		return `<ul class="ub-c360-tl">${items
			.map((a) => `<li><i class="${tone[a.kind] || ""}"></i><div><a class="ub-link" data-go="${this.esc(a.go[0])}|${this.esc(a.go[1])}">${this.esc(a.text)}</a>
				<div class="sub">${this.esc(this.day(a.date))}${a.note ? " · " + this.esc(a.note) : ""}</div></div></li>`)
			.join("")}</ul>`;
	}

	// ---------------------------------------------------------------- the other tabs
	table(cols, rows, empty) {
		if (!rows.length) return `<div class="ub-card ub-empty">${this.esc(empty)}</div>`;
		const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${this.esc(c.label)}</th>`).join("");
		const body = rows.map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.cell(r)}</td>`).join("")}</tr>`).join("");
		return `<div class="ub-card"><div style="overflow-x:auto"><table class="ub-tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></div>`;
	}

	mine(rows) {
		return rows.filter((r) => !this.account || r.connection === this.account);
	}

	tab_bills(v) {
		return this.table([
			{ label: __("Month"), cell: (b) => this.link("Utility Bill", b.name, moment(b.billing_month).format("MMM YYYY")) },
			{ label: __("Units"), num: 1, cell: (b) => this.n(b.units) + (b.nm_method ? `<div class="sub">${__("{0} in, {1} out", [this.n(b.import_units), this.n(b.export_units)])}${b.units_carried_out ? " · " + __("{0} carried", [this.n(b.units_carried_out)]) : ""}</div>` : "") },
			{ label: __("Bill"), num: 1, cell: (b) => this.rs(b.current_amount) },
			{ label: __("Payable"), num: 1, cell: (b) => this.rs(b.payable_within_due) },
			{ label: __("Due"), cell: (b) => this.d(b.due_date) },
			{ label: __("Delivery"), cell: (b) => this.esc(b.delivery_status || "") },
			{ label: __("Surcharge"), cell: (b) => this.esc(__(b.surcharge_status || "")) },
		], this.mine(v.bills), __("No bills yet."));
	}

	tab_payments(v) {
		return this.table([
			{ label: __("Date"), cell: (p) => this.link("Payment Receipt", p.name, this.d(p.payment_date)) },
			{ label: __("How"), cell: (p) => `${this.esc(p.mode)}<div class="sub">${this.esc(p.payment_channel)}</div>` },
			{ label: __("Amount"), num: 1, cell: (p) => this.rs(p.amount) },
		], this.mine(v.payments), __("No payments yet."));
	}

	tab_readings(v) {
		return this.table([
			{ label: __("Month"), cell: (r) => this.link("Meter Reading", r.name, moment(r.billing_month).format("MMM YYYY")) },
			{ label: __("Read on"), cell: (r) => this.d(r.reading_date) },
			{ label: __("Reading"), num: 1, cell: (r) => this.esc(r.present_reading) },
			{ label: __("Units"), num: 1, cell: (r) => this.n(r.units) + (r.net_metering ? `<div class="sub">${__("exported {0}", [this.n(r.export_units)])}</div>` : "") },
			{ label: __("Status"), cell: (r) => `${this.esc(__(r.status))}<div class="sub">${this.esc(r.consumption_type || "")}</div>` },
		], this.mine(v.readings), __("No readings yet."));
	}

	tab_meter(v) {
		const o = v.overview;
		if (!o) return `<div class="ub-card ub-empty">${__("No account.")}</div>`;
		const deposits = this.table([
			{ label: __("Date"), cell: (s) => this.link("Security Deposit", s.name, this.d(s.posting_date)) },
			{ label: __("What"), cell: (s) => this.esc(__(s.deposit_type)) },
			{ label: __("Amount"), num: 1, cell: (s) => this.rs(s.amount) },
		], this.mine(v.deposits), __("No deposit recorded."));
		return `<section class="ub-card"><h2>${__("Connection and meter")}</h2>${this.meterRows(o)}
			<div style="margin-top:10px">${this.link("Connection", o.name, __("Open the account"))}</div></section>
			<h2 class="ub-c360-sub">${__("Security deposits")}</h2>${deposits}`;
	}

	tab_requests(v) {
		return this.table([
			{ label: __("Request"), cell: (s) => this.link("Service Request", s.name, s.request_title || s.name) },
			{ label: __("What"), cell: (s) => this.esc(__(s.kind)) },
			{ label: __("Status"), cell: (s) => this.esc(__(s.status)) },
			{ label: __("Requested / due"), cell: (s) => `${this.d(s.requested_on)}<div class="sub">${this.d(s.due_by)}</div>` },
		], v.service_requests || [], __("No service requests."));
	}

	tab_complaints(v) {
		return this.table([
			{ label: __("Dispute"), cell: (s) => this.link("Bill Dispute", s.name) },
			{ label: __("Bill"), cell: (s) => (s.utility_bill ? this.link("Utility Bill", s.utility_bill) : "") },
			{ label: __("Status"), cell: (s) => this.esc(__(s.status || "")) },
			{ label: __("Raised"), cell: (s) => this.d(s.creation) },
		], this.mine(v.complaints || []), __("No complaints or disputes."));
	}

	tab_messages(v) {
		const o = v.overview;
		const tone = { Sent: "green", Delivered: "green", Failed: "red", Skipped: "amber", Queued: "blue" };
		return this.table([
			{ label: __("When"), cell: (m) => this.esc(moment(m.creation).format("D MMM YYYY, HH:mm")) },
			{ label: __("By"), cell: (m) => this.esc(m.channel) },
			{ label: __("About"), cell: (m) => `${this.esc(m.subject || m.event)}<div class="sub">${this.esc((m.body || "").slice(0, 140))}</div>` },
			{ label: __("Status"), cell: (m) => this.pill(__(m.status), tone[m.status]) },
		], (o && o.messages) || [], __("No messages sent to this account."));
	}

	tab_ledger(v) {
		return this.table([
			{ label: __("Date"), cell: (e) => this.d(e.posting_date) },
			{ label: __("Entry"), cell: (e) => `${this.esc(__(e.entry_type))}<div class="sub">${this.esc(e.voucher_no)}</div>` },
			{ label: __("Amount"), num: 1, cell: (e) => this.rs(e.amount) },
			{ label: __("Balance"), num: 1, cell: (e) => this.rs(e.balance) },
		], this.mine(v.ledger), __("Nothing posted yet."));
	}

	tab_profile(v) {
		const c = v.consumer;
		const reps = (c.representatives || []).map((r) => {
			const may = [[r.pay, __("pay")], [r.notices, __("notices")], [r.sign, __("sign")], [r.changes, __("changes")]].filter(([x]) => x).map(([, t]) => t);
			return `${this.esc(r.name)} <span class="sub">(${this.esc(r.relation || "")}: ${this.esc(may.join(", "))})</span>`;
		});
		const occupancy = this.table([
			{ label: __("Property"), cell: (x) => this.link("Property", x.parent) },
			{ label: __("As"), cell: (x) => `${this.esc(__(x.occupant_role))}${x.is_billed ? ` <span class="sub">(${__("billed")})</span>` : ""}` },
			{ label: __("Share"), num: 1, cell: (x) => (x.share_percent ? `${x.share_percent}%` : "") },
			{ label: __("From – to"), cell: (x) => `${this.d(x.from_date)} – ${x.to_date ? this.d(x.to_date) : __("now")}` },
		], v.occupancy, __("Not recorded on any property."));
		return `<div class="ub-c360-grid2"><section class="ub-card"><h2>${__("Profile")}</h2>${this.rows([
			[__("Type"), this.esc(c.consumer_type)], [c.id_type || __("ID"), this.esc(c.id_number)], [__("Mobile"), this.esc(c.mobile_no)],
			[__("Email"), this.esc(c.email)], [__("Language"), this.esc(c.preferred_language)], [__("Messages by"), this.esc(c.preferred_channel)],
			[__("Tax status"), this.esc(c.tax_status)], [__("Registration No"), this.esc(c.registration_no)], [__("Tax No"), this.esc(c.tax_no)],
			[__("Address"), this.esc(c.address)],
		])}</section><section class="ub-card"><h2>${__("Representatives")}</h2>${reps.length ? reps.join("<br>") : `<div class="ub-empty">${__("None recorded.")}</div>`}</section></div>${occupancy}`;
	}
};
