// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Tariff editor (FS-CFG-04 to 08), designed after the mock-up "Tariff editor with impact preview" (2026-11-16). It edits the ordinary
// Tariff record: drafts are saved through its validation, sent through the Tariff Approval workflow, and approved tariffs are changed
// only by a new version. The impact panel applies the draft to last cycle's bills. Data: utility_billing.utility_billing.tariff_studio.

window.UtilityBillingTariffEditor = class UtilityBillingTariffEditor {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root ub-te"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.name = null;
		this.t = null;
		this.dirty = false;
		const root = this.$root;
		root.on("click", ".ub-te-method", (e) => this.edit({ pricing_method: $(e.currentTarget).attr("data-m") }, true));
		root.on("change", ".ub-te-f", (e) => this.edit({ [$(e.target).attr("data-f")]: e.target.value }));
		root.on("change", ".ub-te-slab", (e) => {
			const i = +$(e.target).attr("data-i");
			this.t.slabs[i][$(e.target).attr("data-k")] = e.target.value;
			this.edit({});
		});
		root.on("click", ".ub-te-addslab", () => {
			const last = this.t.slabs[this.t.slabs.length - 1];
			this.t.slabs.push({ from_units: last ? Number(last.to_units || last.from_units) : 0, to_units: null, rate: 0 });
			this.edit({}, true);
		});
		root.on("click", ".ub-te-delslab", (e) => {
			this.t.slabs.splice(+$(e.currentTarget).attr("data-i"), 1);
			this.edit({}, true);
		});
		root.on("change", ".ub-te-charge", (e) => {
			this.t.charges[+$(e.target).attr("data-i")].rate = e.target.value;
			this.edit({});
		});
		root.on("click", ".ub-te-delcharge", (e) => {
			this.t.charges.splice(+$(e.currentTarget).attr("data-i"), 1);
			this.edit({}, true);
		});
		root.on("change", ".ub-te-addcharge", (e) => {
			const ct = this.t.charge_types.find((c) => c.name === e.target.value);
			if (ct) this.t.charges.push({ charge_type: ct.name, label: ct.label, basis: ct.basis, rate: 0, applies_to: [] });
			this.edit({}, true);
		});
		root.on("click", ".ub-te-save", () => this.save());
		root.on("click", ".ub-te-action", (e) => this.action($(e.currentTarget).attr("data-a")));
		root.on("click", ".ub-te-version", () => this.newVersion());
		root.on("click", ".ub-te-history", () => this.history());
		root.on("click", ".ub-te-impact", () => this.impact(true));
		root.on("click", ".ub-te-addadj", () => frappe.new_doc("Tariff Adjustment", { society: this.t.society, utility: this.t.utility }));
		root.on("click", ".ub-link", (e) => {
			const [dt, name] = $(e.currentTarget).attr("data-go").split("|");
			frappe.set_route(name ? ["Form", dt, name] : ["List", dt]);
		});
		window.addEventListener("beforeunload", (e) => {
			if (this.dirty) e.preventDefault();
		});
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	n2(v) {
		return Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
	}
	rs(v) {
		const x = Math.round(Number(v || 0));
		return (x > 0 ? "+" : x < 0 ? "−" : "") + "Rs " + Math.abs(x).toLocaleString("en-US");
	}
	big(v) {
		const a = Math.abs(v || 0);
		const s = a >= 1e6 ? (a / 1e6).toFixed(1) + "M" : a >= 1e3 ? Math.round(a).toLocaleString("en-US") : Math.round(a);
		return (v > 0 ? "+" : v < 0 ? "−" : "") + "Rs " + s;
	}
	day(v) {
		return v ? moment(v).format("D MMM YYYY") : "";
	}

	open(name) {
		if (!name) {
			this.$root.html(`<div class="ub-card ub-empty">${__("Open a tariff from the Tariff list, then use “Open in Tariff Editor”.")}
				<div style="margin-top:10px">${`<a class="ub-link" data-go="Tariff|">${__("Go to the tariffs")}</a>`}</div></div>`);
			return;
		}
		this.name = name;
		this.$root.html(`<div class="ub-card ub-empty">${__("Loading…")}</div>`);
		frappe.xcall("utility_billing.utility_billing.tariff_studio.load", { name }).then((t) => {
			this.t = t;
			this.dirty = false;
			this.paint();
			this.impact(false);
		});
	}

	edit(values, repaint) {
		Object.assign(this.t, values);
		this.dirty = true;
		if (repaint) this.paint();
		this.$root.find(".ub-te-dirty").text(__("Unsaved changes"));
	}

	payload() {
		const t = this.t;
		return {
			pricing_method: t.pricing_method, flat_rate: t.flat_rate, minimum_charge: t.minimum_charge, minimum_applies_to: t.minimum_applies_to, effective_from: t.effective_from, notes: t.notes,
			slabs: t.pricing_method === "Flat rate" ? [] : t.slabs, charges: t.charges.map((c) => ({ charge_type: c.charge_type, rate: c.rate })),
		};
	}

	save() {
		return frappe
			.xcall("utility_billing.utility_billing.tariff_studio.save", { name: this.name, data: JSON.stringify(this.payload()) })
			.then((t) => {
				this.t = t;
				this.dirty = false;
				this.paint();
				frappe.show_alert({ message: __("Draft saved"), indicator: "green" });
				return t;
			});
	}

	action(a) {
		const go = () =>
			frappe.xcall("frappe.model.workflow.apply_workflow", { doc: { doctype: "Tariff", name: this.name }, action: a }).then(() => this.open(this.name));
		const run = () => (this.dirty ? this.save().then(go) : go());
		if (a === "Reject") {
			return frappe.prompt({ fieldtype: "Small Text", label: __("Why is it rejected?"), fieldname: "reason", reqd: 1 }, (v) =>
				frappe.xcall("utility_billing.utility_billing.doctype.tariff.tariff.set_rejection_reason", { tariff: this.name, reason: v.reason }).then(go), __("Reject"));
		}
		frappe.confirm(__("{0} this tariff?", [__(a)]), run);
	}

	newVersion() {
		frappe.xcall("utility_billing.utility_billing.tariff_studio.new_version", { name: this.name }).then((n) => {
			frappe.show_alert({ message: __("Draft {0} made", [n]), indicator: "green" });
			frappe.set_route("ub-tariff", { name: n });
			this.open(n);
		});
	}

	history() {
		const rows = this.t.versions
			.map((v) => `<tr><td>v${v.version}</td><td><a href="/app/ub-tariff?name=${encodeURIComponent(v.name)}">${this.esc(v.name)}</a></td>
				<td>${this.esc(this.day(v.from))} – ${v.to ? this.esc(this.day(v.to)) : __("open")}</td><td>${this.esc(__(v.state))}</td></tr>`)
			.join("");
		frappe.msgprint({ title: __("Version history"), message: `<table class="table table-sm"><tbody>${rows}</tbody></table>`, wide: true });
	}

	// ---------------------------------------------------------------- painting
	paint() {
		const t = this.t;
		const prev = t.previous;
		const stateTone = { Draft: "amber", Rejected: "red", "Pending Approval": "blue", Approved: "green" };
		const steps = [
			[__("Draft"), ["Draft", "Rejected"].includes(t.state)],
			[__("Submitted"), t.state === "Pending Approval"],
			[__("Approved"), t.state === "Approved"],
			[__("Active from {0}", [this.day(t.effective_from)]), t.state === "Approved" && moment(t.effective_from).isSameOrBefore(moment(), "day")],
		];
		const reached = steps.findIndex(([, on]) => on);
		const stepper = `<div class="ub-te-steps">${steps
			.map(([label], i) => `<span class="ub-te-step ${i < reached ? "done" : i === reached ? "on" : ""}">${this.esc(label)}</span>`)
			.join("")}</div>`;
		const actions = [];
		if (t.can_edit) actions.push(`<button class="ub-btn ub-te-save">${__("Save draft")}</button>`);
		for (const a of t.actions || []) actions.push(`<button class="ub-btn ${/Submit|Approve/.test(a) ? "ub-btn-primary" : ""} ub-te-action" data-a="${this.esc(a)}">${this.esc(__(a))}</button>`);
		if (t.can_version) actions.push(`<button class="ub-btn ub-btn-primary ub-te-version">${__("New version")}</button>`);
		const head = `
		<div class="ub-c360-crumb"><a class="ub-link" data-go="Tariff|">${__("Tariffs")}</a> / ${this.esc(t.utility)}, ${this.esc(t.category_name)}</div>
		<div class="ub-te-head">
			<div><div class="ub-c360-name"><h1>${this.esc(t.utility)}, ${this.esc(t.category_name)}</h1>${this.pill(`${__(t.state)} v${t.version}`, stateTone[t.state])}</div>
				<div class="ub-c360-facts">${__("Effective from {0}", [this.day(t.effective_from)])}${t.effective_to ? " " + __("to {0}", [this.day(t.effective_to)]) : ""}${prev
					? " · " + __("replaces v{0} (active since {1})", [prev.version, this.day(prev.effective_from)]) : ""}
					<span class="ub-te-dirty red"></span></div></div>
			<div class="ub-c360-actions"><button class="ub-btn ub-te-history">${__("Version history")}</button>${actions.join("")}</div>
		</div>
		${stepper}
		${t.rejection_reason && t.state === "Rejected" ? `<div class="ub-c360-alerts">${this.pill(__("Rejected: {0}", [t.rejection_reason]), "red")}</div>` : ""}`;
		this.$root.html(`${head}<div class="ub-te-grid"><div>${this.energy()}${this.charges()}${this.adjustments()}</div><div class="ub-te-side"></div></div>`);
		if (this.lastImpact) this.paintImpact(this.lastImpact);
	}

	pill(text, tone) {
		return `<span class="ub-pill ${tone || ""}">${this.esc(text)}</span>`;
	}

	energy() {
		const t = this.t;
		const prev = t.previous;
		const ro = t.can_edit ? "" : "disabled";
		const methods = [["Flat rate", __("Flat rate")], ["Progressive slabs", __("Slabs (telescopic)")], ["Whole-bill slabs", __("Slabs (whole consumption)")]];
		const seg = `<div class="ub-te-seg">${methods
			.map(([m, l]) => `<button class="ub-te-method ${t.pricing_method === m ? "on" : ""}" data-m="${m}" ${ro}>${this.esc(l)}</button>`)
			.join("")}</div>`;
		const prevRate = prev && prev.pricing_method === "Flat rate" ? this.n2(prev.flat_rate) : prev ? __(prev.pricing_method) : "—";
		const body =
			t.pricing_method === "Flat rate"
				? `<div class="ub-te-fields">
					<label>${__("Rate per unit (Rs)")}<input class="ub-select ub-te-f" data-f="flat_rate" type="number" step="0.01" value="${this.esc(t.flat_rate)}" ${ro}></label>
					<label>${prev ? __("Current (v{0})", [prev.version]) : __("Current")}<div class="ub-te-ro">${this.esc(prevRate)}</div></label>
					<label>${__("Minimum charge (Rs)")}<input class="ub-select ub-te-f" data-f="minimum_charge" type="number" step="1" value="${this.esc(t.minimum_charge)}" ${ro}></label>
					<label>${__("Minimum applies to")}<select class="ub-select ub-te-f" data-f="minimum_applies_to" ${ro}>${["Energy charge", "Whole current bill"].map((o) => `<option value="${o}" ${(t.minimum_applies_to || "Energy charge") === o ? "selected" : ""}>${__(o)}</option>`).join("")}</select></label></div>`
				: `<table class="ub-tbl"><thead><tr><th>${__("From units")}</th><th>${__("To units")}</th><th class="num">${__("Rate (Rs)")}</th><th></th></tr></thead><tbody>
					${(t.slabs || []).map((s, i) => `<tr><td><input class="ub-select ub-te-slab" data-i="${i}" data-k="from_units" type="number" value="${this.esc(s.from_units)}" ${ro}></td>
						<td><input class="ub-select ub-te-slab" data-i="${i}" data-k="to_units" type="number" placeholder="${__("and above")}" value="${s.to_units == null ? "" : this.esc(s.to_units)}" ${ro}></td>
						<td class="num"><input class="ub-select ub-te-slab" data-i="${i}" data-k="rate" type="number" step="0.01" value="${this.esc(s.rate)}" ${ro}></td>
						<td>${t.can_edit ? `<button class="ub-btn ub-te-delslab" data-i="${i}">×</button>` : ""}</td></tr>`).join("")}
					</tbody></table>${t.can_edit ? `<button class="ub-te-add ub-te-addslab">${__("+ Add slab")}</button>` : ""}
					<div class="ub-te-fields"><label>${__("Minimum charge (Rs)")}<input class="ub-select ub-te-f" data-f="minimum_charge" type="number" value="${this.esc(t.minimum_charge)}" ${ro}></label>
					<label>${__("Minimum applies to")}<select class="ub-select ub-te-f" data-f="minimum_applies_to" ${ro}>${["Energy charge", "Whole current bill"].map((o) => `<option value="${o}" ${(t.minimum_applies_to || "Energy charge") === o ? "selected" : ""}>${__(o)}</option>`).join("")}</select></label>
					<label>${__("Effective from")}<input class="ub-select ub-te-f" data-f="effective_from" type="date" value="${this.esc(t.effective_from)}" ${ro}></label></div>`;
		const eff = t.pricing_method === "Flat rate"
			? `<div class="ub-te-fields"><label>${__("Effective from")}<input class="ub-select ub-te-f" data-f="effective_from" type="date" value="${this.esc(t.effective_from)}" ${ro}></label></div>`
			: "";
		return `<section class="ub-card"><h2>${__("Energy charge")}</h2>${seg}${body}${eff}</section>`;
	}

	charges() {
		const t = this.t;
		const basis = { Fixed: __("Fixed per month"), "Per Unit": __("Per unit"), "Per Area": __("Per area"), "By Plot Size": __("By plot size") };
		const unused = t.charge_types.filter((c) => !t.charges.some((x) => x.charge_type === c.name));
		return `<section class="ub-card"><h2>${__("Charges on the same bill")}</h2>
			${t.charges.length ? `<table class="ub-tbl"><thead><tr><th>${__("Charge")}</th><th>${__("Type")}</th><th class="num">${__("Amount")}</th><th>${__("Applies to")}</th><th></th></tr></thead><tbody>
			${t.charges.map((c, i) => `<tr><td>${this.esc(c.label)}</td><td>${this.esc(basis[c.basis] || c.basis || "")}</td>
				<td class="num">${t.can_edit ? `<input class="ub-select ub-te-charge" data-i="${i}" type="number" step="0.01" value="${this.esc(c.rate)}">` : this.n2(c.rate)}</td>
				<td>${this.esc((c.applies_to || []).join(", ") || t.category_name)}</td>
				<td>${t.can_edit ? `<button class="ub-btn ub-te-delcharge" data-i="${i}">×</button>` : ""}</td></tr>`).join("")}</tbody></table>`
				: `<div class="ub-empty">${__("No other charges.")}</div>`}
			${t.can_edit && unused.length ? `<select class="ub-select ub-te-addcharge"><option value="">${__("+ Add charge")}</option>${unused
				.map((c) => `<option value="${this.esc(c.name)}">${this.esc(c.label)}</option>`).join("")}</select>` : ""}</section>`;
	}

	adjustments() {
		const t = this.t;
		const tone = { Approved: "green", "Pending Approval": "blue", Draft: "amber", Rejected: "red" };
		return `<section class="ub-card"><h2>${__("Tariff adjustments (FPA / QTA)")}</h2>
			${t.adjustments.length ? `<table class="ub-tbl"><thead><tr><th>${__("Adjustment")}</th><th class="num">${__("Rate")}</th><th>${__("Units basis")}</th>
				<th>${__("Billing month")}</th><th>${__("Status")}</th></tr></thead><tbody>
				${t.adjustments.map((a) => `<tr><td><a class="ub-link" data-go="Tariff Adjustment|${this.esc(a.name)}">${this.esc(a.label)}</a></td>
					<td class="num">${this.n2(a.rate)}</td><td>${this.esc(a.basis)}</td><td>${this.esc(a.billing_month)}</td><td>${this.pill(__(a.state), tone[a.state])}</td></tr>`).join("")}
				</tbody></table>` : `<div class="ub-empty">${__("No FPA or QTA yet.")}</div>`}
			<button class="ub-te-add ub-te-addadj">${__("+ Add adjustment from a NEPRA notification")}</button></section>`;
	}

	// ---------------------------------------------------------------- impact
	impact(saveFirst) {
		const side = this.$root.find(".ub-te-side");
		side.html(`<section class="ub-card ub-te-impact-card"><div class="ub-cardhead"><h2>${__("Impact preview")}</h2></div><div class="ub-kpi-note">${__("Working it out…")}</div></section>`);
		const run = () =>
			frappe
				.xcall("utility_billing.utility_billing.tariff_studio.impact", { name: this.name })
				.then((r) => {
					this.lastImpact = r;
					this.paintImpact(r);
				})
				.catch(() => side.html(""));
		saveFirst && this.dirty && this.t.can_edit ? this.save().then(run) : run();
	}

	paintImpact(r) {
		const side = this.$root.find(".ub-te-side");
		const stat = (label, value) => `<div class="ub-te-stat"><div class="ub-kpi-note">${this.esc(label)}</div><b>${value}</b></div>`;
		const body = r.bills
			? `<div class="ub-kpi-note">${__("This version applied to {0} readings, compared with the bills actually issued.", [this.esc(r.month)])}${
					this.dirty ? " " + __("Run again to include unsaved changes.") : ""}</div>
				<div class="ub-te-stats">${stat(__("Accounts affected"), (r.changed_bills || 0).toLocaleString("en-US"))}${stat(__("Total billing change"), this.big(r.difference))}
					${stat(__("Average bill change"), this.rs(r.average))}${stat(__("Change"), r.percent == null ? "—" : (r.percent > 0 ? "+" : "") + r.percent + "%")}</div>
				${r.bands.length ? `<table class="ub-tbl"><thead><tr><th>${__("Monthly units")}</th><th class="num">${__("Accounts")}</th><th class="num">${__("Avg change")}</th></tr></thead><tbody>
					${r.bands.map((b) => `<tr><td>${this.esc(b.band)}</td><td class="num">${b.accounts.toLocaleString("en-US")}</td><td class="num">${this.rs(b.average)}</td></tr>`).join("")}</tbody></table>` : ""}
				${r.example ? `<div class="ub-te-example">${__("Example: account {0}, {1} units → bill {2} instead of {3} ({4}).", [
					this.esc(r.example.connection), Math.round(r.example.units), Math.round(r.example.new).toLocaleString("en-US"),
					Math.round(r.example.old).toLocaleString("en-US"), this.rs(r.example.difference)])}</div>` : ""}
				${r.unavailable ? `<div class="ub-kpi-note">${__("{0} bills couldn't be worked out again (missing data).", [r.unavailable])}</div>` : ""}`
			: `<div class="ub-empty">${this.esc(r.message || __("No bills from last cycle in this category yet."))}</div>`;
		side.html(`<section class="ub-card ub-te-impact-card"><div class="ub-cardhead"><h2>${__("Impact preview")}</h2>
			<button class="ub-btn ub-te-impact">${__("Run again")}</button></div>${body}</section>`);
	}
};
