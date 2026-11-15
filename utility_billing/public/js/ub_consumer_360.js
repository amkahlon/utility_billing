// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Consumer 360 page (FS-MST-12, 13, 15). Data comes from utility_billing.utility_billing.consumer360.
// Loaded by the stable page loader in page/ub_consumer_360/ub_consumer_360.js with a version number from the server.

window.UtilityBillingConsumer360 = class UtilityBillingConsumer360 {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.consumer = null;
		this.timer = null;
		const root = this.$root;
		root.on("input", ".ub-c360-q", (e) => {
			clearTimeout(this.timer);
			const q = e.target.value;
			this.timer = setTimeout(() => this.search(q), 250);
		});
		root.on("click", ".ub-c360-pick", (e) => this.open($(e.currentTarget).attr("data-c")));
		root.on("click", ".ub-link", (e) => {
			const [dt, name] = $(e.currentTarget).attr("data-go").split("|");
			frappe.set_route(name ? ["Form", dt, name] : ["List", dt]);
		});
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	rs(v) {
		return "Rs " + Math.round(Number(v || 0)).toLocaleString("en-US");
	}
	d(v) {
		return v ? frappe.datetime.str_to_user(v) : "";
	}
	link(dt, name, text) {
		return `<a class="ub-link" data-go="${this.esc(dt)}|${this.esc(name)}">${this.esc(text || name)}</a>`;
	}

	shell(body) {
		return `<div class="ub-head"><div><h1>${__("Consumer 360")}</h1>
			<p>${__("Find a consumer by name, CNIC, phone, house number, reference number or meter number.")}</p></div>
			<input class="ub-select ub-c360-q" style="min-width:320px" placeholder="${__("Search…")}" autocomplete="off"></div>
			<div class="ub-c360-results"></div><div class="ub-c360-body">${body || ""}</div>`;
	}

	search(q) {
		const out = this.$root.find(".ub-c360-results");
		if ((q || "").trim().length < 2) return out.html("");
		frappe.xcall("utility_billing.utility_billing.consumer360.search", { text: q }).then((rows) => {
			out.html(
				rows.length
					? `<div class="ub-card"><table class="ub-tbl"><tbody>${rows
							.map(
								(r) => `<tr class="ub-c360-pick" data-c="${this.esc(r.name)}" style="cursor:pointer"><td><b>${this.esc(r.consumer_name)}</b>
						<div class="sub">${this.esc(r.name)}</div></td><td>${this.esc(r.mobile_no)}</td><td>${this.esc(r.id_number)}</td></tr>`
							)
							.join("")}</tbody></table></div>`
					: `<div class="ub-card ub-empty">${__("No consumer matches.")}</div>`
			);
		});
	}

	open(consumer) {
		if (!this.$root.find(".ub-c360-q").length) this.$root.html(this.shell());
		this.$root.find(".ub-c360-results").html("");
		if (!consumer) return this.$root.find(".ub-c360-body").html(`<div class="ub-card ub-empty">${__("Search for a consumer to see everything about them on one page.")}</div>`);
		this.consumer = consumer;
		this.$root.find(".ub-c360-body").html(`<div class="ub-card ub-empty">${__("Loading…")}</div>`);
		frappe
			.xcall("utility_billing.utility_billing.consumer360.view", { consumer })
			.then((v) => this.$root.find(".ub-c360-body").html(this.render(v)))
			.catch(() => this.$root.find(".ub-c360-body").html(`<div class="ub-card ub-empty">${__("This consumer could not be loaded.")}</div>`));
	}

	table(title, cols, rows, empty) {
		const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${this.esc(c.label)}</th>`).join("");
		const body = rows.map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.cell(r)}</td>`).join("")}</tr>`).join("");
		return `<div class="ub-card"><h2>${this.esc(title)}</h2>${
			rows.length ? `<div style="overflow-x:auto"><table class="ub-tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>` : `<div class="ub-empty">${this.esc(empty)}</div>`
		}</div>`;
	}

	render(v) {
		const c = v.consumer;
		const kpi = (label, value, note) =>
			`<div class="ub-kpi"><div class="ub-kpi-top"><span class="ub-kpi-label">${this.esc(label)}</span></div><div class="ub-kpi-value">${value}</div>
			<div class="ub-kpi-foot"><span class="ub-kpi-note">${note || ""}</span></div></div>`;
		const profile = [
			[__("Type"), c.consumer_type], [c.id_type || __("ID"), c.id_number], [__("Mobile"), c.mobile_no], [__("Email"), c.email],
			[__("Language"), c.preferred_language], [__("Messages by"), c.preferred_channel], [__("Tax status"), c.tax_status],
			[__("Registration No"), c.registration_no], [__("Tax No"), c.tax_no], [__("Address"), c.address],
		].filter(([, x]) => x);
		const reps = (c.representatives || []).map((r) => {
			const may = [[r.pay, __("pay")], [r.notices, __("notices")], [r.sign, __("sign")], [r.changes, __("changes")]].filter(([x]) => x).map(([, t]) => t);
			return `${this.esc(r.name)} <span class="sub">(${this.esc(r.relation || "")}: ${this.esc(may.join(", "))})</span>`;
		});
		return `
		<div class="ub-cardhead"><h2 style="font-size:22px">${this.esc(c.consumer_name)}</h2>${this.link("Consumer", c.name, __("Open record"))}</div>
		<div class="ub-kpis">
			${kpi(__("Accounts"), v.accounts.length, __("connections of this consumer"))}
			${kpi(__("Owes now"), this.rs(v.total_owed), __("all accounts together"))}
			${kpi(__("Deposit held"), this.rs(v.total_deposit), __("security deposits"))}
			${kpi(__("Open items"), v.open_items.length, v.open_items.map((o) => this.esc(o.label)).join(", "))}
		</div>
		<div class="ub-grid3" style="grid-template-columns:1fr 2fr">
			<div class="ub-card"><h2>${__("Profile")}</h2><table class="ub-tbl"><tbody>${profile
				.map(([k, x]) => `<tr><td class="sub">${this.esc(k)}</td><td>${this.esc(x)}</td></tr>`)
				.join("")}</tbody></table>${reps.length ? `<h2 style="margin-top:14px">${__("Representatives")}</h2><div>${reps.join("<br>")}</div>` : ""}
				${c.cash_only ? `<p class="ub-badge" style="margin-top:12px;display:inline-block">${__("Cash only")}</p>` : ""}</div>
			${this.table(__("Accounts"), [
				{ label: __("Account"), cell: (a) => `${this.link("Connection", a.name, a.reference_no || a.name)}<div class="sub">${this.esc(a.society)} · ${this.esc(a.utility)}</div>` },
				{ label: __("Property"), cell: (a) => `${this.esc(a.property_no)}<div class="sub">${this.esc(a.block || "")}</div>` },
				{ label: __("Category / status"), cell: (a) => `${this.esc(a.consumer_category)}<div class="sub">${this.esc(a.status)}</div>` },
				{ label: __("Meter / route"), cell: (a) => `${this.esc(a.current_meter || "")}<div class="sub">${this.esc(a.route || "")}</div>` },
				{
					label: __("Load kW (sanctioned / MDI)"),
					num: 1,
					cell: (a) => `${this.esc(a.sanctioned_load_kw || 0)} / ${a.last_mdi_kw ? this.esc(a.last_mdi_kw) : "–"}${
						a.last_mdi_kw > a.sanctioned_load_kw && a.sanctioned_load_kw ? ` <span class="ub-badge">${__("over")}</span>` : ""}`,
				},
				{ label: __("Flags"), cell: (a) => (a.flags.length ? `<span class="ub-badge">${this.esc(a.flags.join(", "))}</span>` : "") },
				{ label: __("Deposit"), num: 1, cell: (a) => this.rs(a.security_deposit) },
				{ label: __("Owes"), num: 1, cell: (a) => `<b>${this.rs(a.owes)}</b>` },
			], v.accounts, __("No connections yet."))}
		</div>
		${v.open_items.length ? `<div class="ub-card"><h2>${__("Open items")}</h2>${v.open_items.map((o) => `${this.esc(o.label)}: ${this.link(o.doctype, o.name)}`).join(" · ")}</div>` : ""}
		<div class="ub-grid3" style="grid-template-columns:1fr 1fr">
			${this.table(__("Bills"), [
				{ label: __("Month"), cell: (b) => this.link("Utility Bill", b.name, moment(b.billing_month).format("MMM YYYY")) },
				{ label: __("Units"), num: 1, cell: (b) => this.esc(b.units) },
				{ label: __("Bill"), num: 1, cell: (b) => this.rs(b.current_amount) },
				{ label: __("Payable"), num: 1, cell: (b) => this.rs(b.payable_within_due) },
				{ label: __("Due / delivery"), cell: (b) => `${this.d(b.due_date)}<div class="sub">${this.esc(b.delivery_status || "")}</div>` },
			], v.bills, __("No bills yet."))}
			${this.table(__("Payments"), [
				{ label: __("Date"), cell: (p) => this.link("Payment Receipt", p.name, this.d(p.payment_date)) },
				{ label: __("How"), cell: (p) => `${this.esc(p.mode)}<div class="sub">${this.esc(p.payment_channel)}</div>` },
				{ label: __("Amount"), num: 1, cell: (p) => this.rs(p.amount) },
			], v.payments, __("No payments yet."))}
		</div>
		${this.table(__("Account ledger (latest first)"), [
			{ label: __("Date"), cell: (e) => this.d(e.posting_date) },
			{ label: __("Account"), cell: (e) => this.esc(e.connection) },
			{ label: __("Entry"), cell: (e) => `${this.esc(__(e.entry_type))}<div class="sub">${this.esc(e.voucher_no)}</div>` },
			{ label: __("Amount"), num: 1, cell: (e) => this.rs(e.amount) },
			{ label: __("Balance"), num: 1, cell: (e) => this.rs(e.balance) },
		], v.ledger, __("Nothing posted yet."))}
		<div class="ub-grid3" style="grid-template-columns:1fr 1fr">
			${this.table(__("Readings"), [
				{ label: __("Month"), cell: (r) => this.link("Meter Reading", r.name, moment(r.billing_month).format("MMM YYYY")) },
				{ label: __("Reading"), num: 1, cell: (r) => this.esc(r.present_reading) },
				{ label: __("Units"), num: 1, cell: (r) => this.esc(r.units) },
				{ label: __("Status"), cell: (r) => `${this.esc(r.status)}<div class="sub">${this.esc(r.consumption_type || "")}</div>` },
			], v.readings, __("No readings yet."))}
			${this.table(__("Security deposits"), [
				{ label: __("Date"), cell: (s) => this.link("Security Deposit", s.name, this.d(s.posting_date)) },
				{ label: __("What"), cell: (s) => this.esc(__(s.deposit_type)) },
				{ label: __("Amount"), num: 1, cell: (s) => this.rs(s.amount) },
			], v.deposits, __("No deposit recorded."))}
		</div>
		${this.table(__("Properties owned or occupied"), [
			{ label: __("Property"), cell: (o) => this.link("Property", o.parent) },
			{ label: __("As"), cell: (o) => `${this.esc(o.occupant_role)}${o.is_billed ? ` <span class="sub">(${__("billed")})</span>` : ""}` },
			{ label: __("Share"), num: 1, cell: (o) => (o.share_percent ? `${o.share_percent}%` : "") },
			{ label: __("From – to"), cell: (o) => `${this.d(o.from_date)} – ${o.to_date ? this.d(o.to_date) : __("now")}` },
		], v.occupancy, __("Not recorded on any property."))}
		${this.table(__("Service requests"), [
			{ label: __("Request"), cell: (s) => this.link("Service Request", s.name, s.request_title || s.name) },
			{ label: __("What"), cell: (s) => this.esc(__(s.kind)) },
			{ label: __("Status"), cell: (s) => this.esc(__(s.status)) },
			{ label: __("Requested / due"), cell: (s) => `${this.d(s.requested_on)}<div class="sub">${this.d(s.due_by)}</div>` },
		], v.service_requests || [], __("No service requests."))}`;
	}
};
