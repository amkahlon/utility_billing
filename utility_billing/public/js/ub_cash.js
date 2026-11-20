// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Cash counter (FS-PAY-01, 05, 07, 13), designed after the mock-up "Cash counter" (2026-11-16): scan the bill's barcode or type the
// reference, see what is payable, take the money (cash with change, cheque, card), post and print (F9), clear (Esc). Each payment is an
// ordinary Payment Receipt at the counter. Data: utility_billing.utility_billing.cashdesk.

window.UtilityBillingCashCounter = class UtilityBillingCashCounter {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root ub-cash"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.s = null;
		this.acc = null;
		this.mode = "Cash";
		this.busy = false;
		const root = this.$root;
		root.on("keydown", ".ub-cash-ref", (e) => {
			if (e.key === "Enter") this.lookup(e.target.value);
		});
		root.on("click", ".ub-cash-find", () => this.lookup(root.find(".ub-cash-ref").val()));
		root.on("input", ".ub-cash-amount, .ub-cash-tendered", () => this.change());
		root.on("click", ".ub-cash-mode", (e) => {
			this.mode = $(e.currentTarget).attr("data-m");
			this.paintPay();
		});
		root.on("click", ".ub-cash-post", () => this.post());
		root.on("click", ".ub-cash-clear", () => this.clear());
		root.on("click", ".ub-cash-close", () => frappe.set_route("Form", "Cashier Day", this.s.day));
		root.on("click", ".ub-cash-open", () => frappe.new_doc("Cashier Day"));
		root.on("click", ".ub-link", (e) => {
			const [dt, name] = $(e.currentTarget).attr("data-go").split("|");
			frappe.set_route(name ? ["Form", dt, name] : ["List", dt]);
		});
		this.keys = (e) => {
			if (frappe.get_route_str() !== "ub-cash") return;
			if (e.key === "F9") {
				e.preventDefault();
				this.post();
			} else if (e.key === "Escape") this.clear();
		};
		document.addEventListener("keydown", this.keys);
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	n(v) {
		return Math.round(Number(v || 0)).toLocaleString("en-US");
	}
	rs(v) {
		return "Rs " + this.n(v);
	}

	load() {
		frappe.xcall("utility_billing.utility_billing.cashdesk.state").then((s) => {
			this.s = s;
			this.paint();
		});
	}

	paint() {
		const s = this.s;
		if (!s.open) {
			this.$root.html(`<div class="ub-card ub-empty"><h2>${__("Your cashier day isn't open")}</h2>
				<p>${__("Open a cashier day at your counter (with its opening float) to take payments.")}</p>
				<button class="ub-btn ub-btn-primary ub-cash-open">${__("Open a cashier day")}</button></div>`);
			return;
		}
		const t = s.totals;
		this.$root.html(`
			<div class="ub-cash-top">${this.esc(s.counter_name || s.counter)}${s.office ? " · " + this.esc(s.office) : ""} · ${__("Cashier")}: ${this.esc(s.cashier)} ·
				${__("Day opened {0}", [moment(s.opened).format("HH:mm")])}
				${s.stale ? `<span class="ub-pill red">${__("This day is for {0}: close it and open today's", [moment(s.business_date).format("D MMM")])}</span>` : ""}</div>
			<div class="ub-cash-grid">
				<section class="ub-card"><h2>${__("Receive payment")}</h2>
					<div class="ub-cash-scan"><input class="ub-select ub-cash-ref" placeholder="${__("Scan the bill barcode, or type the reference number or consumer ID")}" autocomplete="off" autofocus>
						<button class="ub-btn ub-cash-find">${__("Find")}</button></div>
					<div class="ub-cash-acc"></div>
					<div class="ub-cash-pay"></div>
				</section>
				<div>
					<section class="ub-card"><h2>${__("Today at this counter")}</h2>
						<dl class="ub-c360-dl">
							<dt>${__("Receipts")}</dt><dd>${t.receipts}</dd>
							<dt>${__("Collected")}</dt><dd><b>${this.rs(t.collected)}</b></dd>
							<dt>${__("Cash")}</dt><dd>${this.rs(t.cash)}</dd>
							<dt>${__("Cheques")}</dt><dd>${this.rs(t.cheque)} (${t.cheques})</dd>
							${t.card ? `<dt>${__("Card")}</dt><dd>${this.rs(t.card)}</dd>` : ""}
						</dl>
						<button class="ub-btn ub-cash-close" style="width:100%;margin-top:14px">${__("Close day and count cash")}</button></section>
					<section class="ub-card"><h2>${__("Recent receipts")}</h2>${s.recent.length
						? `<table class="ub-tbl"><thead><tr><th>${__("Receipt")}</th><th>${__("House")}</th><th class="num">Rs</th></tr></thead><tbody>${s.recent
								.map((r) => `<tr><td><a class="ub-link" data-go="Payment Receipt|${this.esc(r.name)}">${this.esc(r.name)}</a>
									<div class="sub">${this.esc(moment(r.time).format("HH:mm"))} · ${this.esc(r.mode)}</div></td><td>${this.esc(r.house || "")}</td>
									<td class="num">${this.n(r.amount)}</td></tr>`).join("")}</tbody></table>`
						: `<div class="ub-empty">${__("No receipts yet today.")}</div>`}</section>
				</div>
			</div>`);
		this.$root.find(".ub-cash-ref").trigger("focus");
	}

	lookup(ref) {
		ref = (ref || "").trim();
		if (!ref) return;
		const out = this.$root.find(".ub-cash-acc").html(`<div class="ub-kpi-note">${__("Looking it up…")}</div>`);
		frappe
			.xcall("utility_billing.utility_billing.cashdesk.lookup", { reference: ref })
			.then((a) => {
				if (a.choose) {
					// a consumer ID with several accounts: the cashier picks the one being paid (increment 31)
					this.acc = null;
					this.$root.find(".ub-cash-pay").html("");
					out.html(`<div class="ub-cash-who"><b>${this.esc(a.consumer_name || a.consumer)}</b>
						<div class="sub">${__("{0} accounts. Choose the one being paid.", [a.choose.length])}</div></div>
						<table class="ub-table ub-cash-choose">${a.choose.map((c) => `<tr>
							<td><b>${this.esc(c.reference)}</b></td><td>${this.esc(__(c.utility || ""))}</td>
							<td>${c.property_no ? __("House") + " " + this.esc(c.property_no) : ""}</td>
							<td>${c.status !== "Active" ? `<span class="ub-pill amber">${this.esc(__(c.status))}</span>` : ""}</td>
							<td class="text-right">${this.rs(Math.max(c.amount_due, 0))}</td>
							<td class="text-right"><button class="ub-btn ub-cash-pick" data-ref="${this.esc(c.reference)}">${__("Choose")}</button></td></tr>`).join("")}</table>`);
					out.find(".ub-cash-pick").on("click", (ev) => {
						const picked = $(ev.currentTarget).attr("data-ref");
						this.$root.find(".ub-cash-ref").val(picked);
						this.lookup(picked);
					});
					return;
				}
				this.acc = a;
				const due = a.due_date ? moment(a.due_date) : null;
				const within = due && !due.isBefore(moment(), "day");
				out.html(`<div class="ub-cash-who"><b>${this.esc(a.consumer_name)}</b>
					<div class="sub">${a.property_no ? `${__("House")} ${this.esc(a.property_no)}${a.block ? ", " + __("Block") + " " + this.esc(a.block) : ""} · ` : ""}${__("Ref.")} ${this.esc(a.connection)}${a.bill_month ? " · " + __("{0} bill", [this.esc(a.bill_month)]) : ""}</div>
					${due ? `<span class="ub-pill ${within ? "green" : "red"}">${within ? __("Within due date ({0})", [due.format("D MMM")]) : __("After due date ({0})", [due.format("D MMM")])}</span>` : ""}
					${a.status && a.status !== "Active" ? `<span class="ub-pill amber">${this.esc(__(a.status))}</span>` : ""}</div>
					<dl class="ub-c360-dl">
						${a.current !== null && a.current !== undefined ? `<dt>${__("Current bill")}</dt><dd>${this.n(a.current)}</dd>` : ""}
						${a.arrears !== null && a.arrears !== undefined ? `<dt>${__("Arrears")}</dt><dd>${this.n(a.arrears)}</dd>` : ""}
						<dt><b>${__("Payable now")}</b></dt><dd class="ub-cash-due">${this.rs(Math.max(a.amount_due, 0))}</dd>
					</dl>`);
				this.paintPay();
			})
			.catch(() => {
				this.acc = null;
				out.html("");
				this.$root.find(".ub-cash-pay").html("");
			});
	}

	paintPay() {
		const a = this.acc;
		if (!a) return;
		const amount = this.$root.find(".ub-cash-amount").val() || (a.amount_due > 0 ? Math.round(a.amount_due * 100) / 100 : "");
		const tendered = this.$root.find(".ub-cash-tendered").val() || "";
		const modes = [["Cash", __("Cash")], ["Cheque", __("Cheque")], ["Card", __("Card (POS)")]];
		this.$root.find(".ub-cash-pay").html(`
			<div class="ub-te-fields">
				<label>${__("Amount received")}<input class="ub-select ub-cash-amount" type="number" step="0.01" value="${this.esc(amount)}"></label>
				${this.mode === "Cash" ? `<label>${__("Cash tendered")}<input class="ub-select ub-cash-tendered" type="number" step="1" value="${this.esc(tendered)}"></label>
					<label>${__("Change to return")}<div class="ub-cash-change">—</div></label>` : ""}
				${this.mode === "Cheque" ? `<label>${__("Cheque number")}<input class="ub-select ub-cash-inst" value=""></label>
					<label>${__("Bank")}<input class="ub-select ub-cash-bank" value=""></label>` : ""}
				${this.mode === "Card" ? `<label>${__("Card slip number")}<input class="ub-select ub-cash-inst" value=""></label>` : ""}
			</div>
			<div class="ub-cash-modes">${__("Mode")}: ${modes.map(([m, l]) => `<button class="ub-te-method ub-cash-mode ${this.mode === m ? "on" : ""}" data-m="${m}">${this.esc(l)}</button>`).join("")}</div>
			<div class="ub-kpi-note">${__("Applied in the society's posting order ({0}). The receipt prints and the consumer gets it by their usual channel.", [
				this.esc((this.s.rules && this.s.rules.payment_posting_order) || __("arrears first"))])}</div>
			<div class="ub-cash-actions"><button class="ub-btn ub-cash-clear">${__("Clear (Esc)")}</button>
				<button class="ub-btn ub-btn-primary ub-cash-post">${__("Post and print receipt (F9)")}</button></div>`);
		this.change();
	}

	change() {
		const amount = Number(this.$root.find(".ub-cash-amount").val() || 0);
		const tendered = Number(this.$root.find(".ub-cash-tendered").val() || 0);
		const out = this.$root.find(".ub-cash-change");
		if (!out.length) return;
		if (!tendered) return out.text("—").removeClass("red");
		const diff = tendered - amount;
		out.text(diff >= 0 ? this.rs(diff) : __("{0} short", [this.rs(-diff)])).toggleClass("red", diff < 0);
	}

	post() {
		if (this.busy || !this.acc) return;
		const amount = Number(this.$root.find(".ub-cash-amount").val() || 0);
		const tendered = Number(this.$root.find(".ub-cash-tendered").val() || 0);
		if (amount <= 0) return frappe.msgprint(__("Enter the amount received."));
		if (this.mode === "Cash" && tendered && tendered < amount) return frappe.msgprint(__("The cash tendered is less than the amount."));
		this.busy = true;
		frappe
			.xcall("utility_billing.utility_billing.cashdesk.post", {
				reference: this.acc.connection, amount, mode: this.mode,
				instrument_no: this.$root.find(".ub-cash-inst").val() || null, bank_name: this.$root.find(".ub-cash-bank").val() || null,
			})
			.then((r) => {
				frappe.show_alert({ message: __("Receipt {0} posted", [r.name]), indicator: "green" });
				window.open(`/api/method/utility_billing.utility_billing.account_print.download_receipt?name=${encodeURIComponent(r.name)}`);
				this.acc = null;
				this.load();
			})
			.finally(() => (this.busy = false));
	}

	clear() {
		this.acc = null;
		this.$root.find(".ub-cash-acc, .ub-cash-pay").html("");
		this.$root.find(".ub-cash-ref").val("").trigger("focus");
	}
};
