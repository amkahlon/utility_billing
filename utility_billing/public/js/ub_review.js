// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Reading review (FS-RDG-06 to 09), designed after the mock-up "Reading review" (2026-11-16): the cycle's flagged readings as one queue,
// filtered by route and exception, and the chosen reading beside it: photo, reader, when it was taken, what the checks found and what
// approving it will do; approve, correct or send for a re-read. Data: utility_billing.utility_billing.reading_review.

window.UtilityBillingReadingReview = class UtilityBillingReadingReview {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root ub-rv"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.d = null;
		this.sel = null;
		this.q = { cycle: null, route: "", code: "" };
		const root = this.$root;
		root.on("change", ".ub-rv-f", (e) => {
			const f = $(e.target).attr("data-f");
			this.q[f] = e.target.value;
			// each filter narrows the next: a new cycle or route offers only its own exception types (and routes)
			if (f === "cycle") Object.assign(this.q, { route: "", code: "" });
			if (f === "route") this.q.code = "";
			this.load();
		});
		root.on("click", ".ub-rv-row", (e) => {
			this.sel = $(e.currentTarget).attr("data-n");
			this.paint();
		});
		root.on("click", ".ub-rv-approve", () => this.decide("approve"));
		root.on("click", ".ub-rv-correct", () => this.decide("correct"));
		root.on("click", ".ub-rv-reread", () => this.decide("reread"));
		root.on("click", ".ub-link", (e) => {
			const [dt, name] = $(e.currentTarget).attr("data-go").split("|");
			frappe.set_route(name ? ["Form", dt, name] : ["List", dt]);
		});
		document.addEventListener("keydown", (e) => {
			if (frappe.get_route_str() !== "ub-review" || !this.d || ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
			const rows = this.d.rows;
			const i = rows.findIndex((r) => r.name === this.sel);
			if (e.key === "ArrowDown" || e.key === "j") this.pick(rows[Math.min(i + 1, rows.length - 1)]);
			if (e.key === "ArrowUp" || e.key === "k") this.pick(rows[Math.max(i - 1, 0)]);
		});
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	n(v) {
		return v === null || v === undefined || v === "" ? "—" : Math.round(Number(v)).toLocaleString("en-US");
	}

	pick(r) {
		if (!r) return;
		this.sel = r.name;
		this.paint();
	}

	load(cycle) {
		if (cycle) this.q.cycle = cycle;
		frappe.xcall("utility_billing.utility_billing.reading_review.queue", this.q).then((d) => {
			this.d = d;
			this.q.cycle = d.cycle;
			if (!d.rows || !d.rows.some((r) => r.name === this.sel)) this.sel = d.rows && d.rows.length ? d.rows[0].name : null;
			this.paint();
		});
	}

	paint() {
		const d = this.d;
		if (!d.cycle) return this.$root.html(`<div class="ub-card ub-empty">${__("No reading cycle yet.")}</div>`);
		const tone = (r) => (r.exception_code ? "amber" : "red");
		const cycles = d.cycles.map((c) => `<option value="${this.esc(c.name)}" ${c.name === d.cycle ? "selected" : ""}>${this.esc(c.utility)} ${this.esc(moment(c.billing_month).format("MMMM YYYY"))}${c.status === "Closed" ? " (" + __("closed") + ")" : ""}</option>`).join("");
		const list = d.rows.length
			? `<div style="overflow-x:auto"><table class="ub-tbl ub-rv-list"><thead><tr><th>${__("House / meter")}</th><th>${__("Flag")}</th><th class="num">${__("Previous")}</th>
				<th class="num">${__("Present")}</th><th class="num">${__("Units")}</th><th class="num">${__("Expected")}</th></tr></thead><tbody>${d.rows
					.map((r) => `<tr class="ub-rv-row ${r.name === this.sel ? "on" : ""}" data-n="${this.esc(r.name)}">
						<td><b>${this.esc(r.house)}</b><div class="sub">${this.esc(r.meter || "")} · ${this.esc(r.route || "")}</div></td>
						<td><span class="ub-pill ${tone(r)}">${this.esc(r.flag)}</span></td><td class="num">${this.n(r.previous_reading)}</td>
						<td class="num">${r.present_reading ? this.n(r.present_reading) : "—"}</td><td class="num">${r.present_reading ? this.n(r.units) : "—"}</td>
						<td class="num">${r.expected_high ? `${this.n(r.expected_low)}–${this.n(r.expected_high)}` : "—"}</td></tr>`)
					.join("")}</tbody></table></div>`
			: `<div class="ub-allgood">${window.ub_icon ? window.ub_icon("check") : ""} ${__("Nothing waiting for review in this cycle.")}</div>`;
		this.$root.html(`
			<div class="ub-head"><div><h1>${__("Reading review")}</h1>
				<p>${__("{0} cycle · {1} of {2} read · {3} flagged readings waiting", [this.esc(d.month_label), d.counts.done, d.counts.total, d.counts.waiting])}</p></div>
				<div class="ub-tools"><select class="ub-select ub-rv-f" data-f="cycle">${cycles}</select>
					<select class="ub-select ub-rv-f" data-f="route"><option value="">${__("All routes")}</option>${d.routes.map((r) => `<option ${r === d.route ? "selected" : ""}>${this.esc(r)}</option>`).join("")}</select>
					<select class="ub-select ub-rv-f" data-f="code"><option value="">${__("All exception types")}</option>${d.codes.map((c) => `<option ${c === d.code ? "selected" : ""}>${this.esc(c)}</option>`).join("")}</select></div></div>
			<div class="ub-rv-grid"><section class="ub-card">${list}</section><div class="ub-rv-side">${this.detail()}</div></div>`);
	}

	detail() {
		const r = this.d.rows.find((x) => x.name === this.sel);
		if (!r) return "";
		const can = this.d.can_decide;
		return `<section class="ub-card">
			<div class="ub-cardhead"><h2>${this.esc(r.house)} · ${__("meter")} ${this.esc(r.meter || "")}</h2><span class="ub-pill ${r.exception_code ? "amber" : "red"}">${this.esc(r.flag)}</span></div>
			${r.photo ? `<a href="${this.esc(r.photo)}" target="_blank"><img class="ub-rv-photo" src="${this.esc(r.photo)}" alt="${__("Meter photo")}"></a>`
				: `<div class="ub-rv-nophoto">${__("No photo with this reading")}</div>`}
			<dl class="ub-c360-dl">
				<dt>${__("Account")}</dt><dd><a class="ub-link" data-go="Connection|${this.esc(r.connection)}">${this.esc(r.connection)}</a> · ${this.esc(r.consumer_name || "")}</dd>
				<dt>${__("Reader")}</dt><dd>${this.esc(r.reader || __("not assigned"))}${r.route ? ", " + __("route {0}", [this.esc(r.route)]) : ""}</dd>
				<dt>${__("Taken")}</dt><dd>${r.reading_date ? this.esc(moment(r.reading_date).format("D MMM YYYY")) : "—"} · ${this.esc(__(r.source || ""))}</dd>
				<dt>${__("Usual")}</dt><dd>${r.expected_average ? __("about {0} units ({1}–{2})", [this.n(r.expected_average), this.n(r.expected_low), this.n(r.expected_high)]) : "—"}</dd>
				${r.checks ? `<dt>${__("Checks found")}</dt><dd>${this.esc(r.checks)}</dd>` : ""}
				${r.review_note ? `<dt>${__("Note")}</dt><dd>${this.esc(r.review_note)}</dd>` : ""}
			</dl>
			<div class="ub-te-example">${this.esc(r.consequence)}</div>
			${can ? `<div class="ub-te-fields" style="grid-template-columns:1fr">
				<label>${__("Corrected reading (if the reader made an error)")}<input class="ub-select ub-rv-value" type="number" step="1" placeholder="${this.esc(r.present_reading || "")}"></label>
				<label>${__("Why (kept with the reading)")}<input class="ub-select ub-rv-note" placeholder="${__("For example: confirmed with the consumer by phone")}"></label></div>
				<div class="ub-cash-actions"><button class="ub-btn ub-rv-reread">${__("Send for re-read")}</button><button class="ub-btn ub-rv-correct">${__("Save correction")}</button>
					<button class="ub-btn ub-btn-primary ub-rv-approve">${r.exception_code ? __("Approve as {0}", [this.esc(r.exception_code)]) : __("Approve as read")}</button></div>
				<div class="ub-kpi-note">${__("↑ ↓ move through the queue")}</div>` : ""}
			</section>`;
	}

	decide(what) {
		const r = this.d.rows.find((x) => x.name === this.sel);
		const note = this.$root.find(".ub-rv-note").val() || "";
		const base = "utility_billing.utility_billing.readings.";
		let call;
		if (what === "approve") call = frappe.xcall(base + "approve_reading", { name: r.name, note });
		if (what === "reread") call = frappe.xcall(base + "request_reread", { name: r.name, note });
		if (what === "correct") {
			const value = this.$root.find(".ub-rv-value").val();
			if (!value) return frappe.msgprint(__("Enter the corrected reading."));
			call = frappe.xcall(base + "correct_reading", { name: r.name, present_reading: value, reason: note });
		}
		call.then(() => {
			frappe.show_alert({ message: __("Done: {0}", [r.house]), indicator: "green" });
			const i = this.d.rows.findIndex((x) => x.name === r.name);
			const next = this.d.rows[i + 1] || this.d.rows[i - 1];
			this.sel = next ? next.name : null;
			this.load();
		});
	}
};
