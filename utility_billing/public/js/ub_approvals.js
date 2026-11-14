// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Approval Inbox (FS-ACC-06): what the user can approve now, approved or rejected one by one or in bulk.
window.UtilityBillingApprovals = class UtilityBillingApprovals {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.picked = new Set();
		this.$root.on("change", ".ub-pick", (e) => {
			const k = $(e.target).attr("data-k");
			e.target.checked ? this.picked.add(k) : this.picked.delete(k);
			this.bar();
		});
		this.$root.on("change", ".ub-pick-all", (e) => {
			this.$root.find(".ub-pick").each((_i, el) => {
				el.checked = e.target.checked;
				e.target.checked ? this.picked.add($(el).attr("data-k")) : this.picked.delete($(el).attr("data-k"));
			});
			this.bar();
		});
		this.$root.on("click", ".ub-open", (e) => {
			const [dt, name] = $(e.currentTarget).attr("data-k").split("|");
			frappe.set_route("Form", dt, name);
		});
		this.$root.on("click", ".ub-approve", () => this.act("Approve"));
		this.$root.on("click", ".ub-reject", () => this.act("Reject"));
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	load() {
		this.picked.clear();
		this.$root.html(`<div class="ub-card ub-empty">${__("Loading…")}</div>`);
		frappe.xcall("utility_billing.utility_billing.access.inbox").then((rows) => {
			this.rows = rows;
			const body = rows
				.map((r) => {
					const k = `${r.doctype}|${r.name}`;
					return `<tr><td><input type="checkbox" class="ub-pick" data-k="${this.esc(k)}"></td>
						<td><a class="ub-open" data-k="${this.esc(k)}">${this.esc(r.title)}</a><div class="sub">${this.esc(__(r.doctype))} · ${this.esc(r.name)}</div></td>
						<td>${this.esc(r.society || "")}</td><td>${this.esc(r.requested_by)}</td>
						<td class="num">${r.days > 0 ? `<span class="ub-badge">${r.days} ${r.days === 1 ? __("day") : __("days")}</span>` : __("today")}</td></tr>`;
				})
				.join("");
			this.$root.html(`<div class="ub-head"><div><h1>${__("Approval Inbox")}</h1>
				<p>${__("What you can approve now, oldest first. Rejecting sends it back to whoever made it, with your reason.")}</p></div>
				<div><button class="ub-btn ub-approve" disabled>${__("Approve")}</button> <button class="ub-btn ub-reject" disabled>${__("Reject")}</button></div></div>
				<div class="ub-card">${
					rows.length
						? `<table class="ub-tbl"><thead><tr><th><input type="checkbox" class="ub-pick-all"></th><th>${__("Document")}</th><th>${__("Society")}</th>
							<th>${__("Made by")}</th><th class="num">${__("Waiting")}</th></tr></thead><tbody>${body}</tbody></table>`
						: `<div class="ub-empty">${__("Nothing is waiting for you.")}</div>`
				}</div>`);
		});
	}

	bar() {
		this.$root.find(".ub-approve, .ub-reject").prop("disabled", !this.picked.size);
	}

	act(action) {
		const items = [...this.picked].map((k) => ({ doctype: k.split("|")[0], name: k.split("|")[1] }));
		const go = (reason) =>
			frappe
				.call({ method: "utility_billing.utility_billing.access.bulk", args: { items: JSON.stringify(items), action, reason }, freeze: true })
				.then(({ message: r }) => {
					const failed = r.failed.map((f) => `<li>${this.esc(f.name)}: ${this.esc(f.error)}</li>`).join("");
					frappe.msgprint({
						title: __("{0} done", [r.done.length]),
						message: failed ? `<p>${__("Not done:")}</p><ul>${failed}</ul>` : __("All done."),
						indicator: failed ? "orange" : "green",
					});
					this.load();
				});
		if (action === "Reject") {
			frappe.prompt([{ fieldname: "reason", fieldtype: "Small Text", label: __("Why?"), reqd: 1 }], (v) => go(v.reason), __("Reject {0}", [items.length]));
		} else {
			frappe.confirm(__("Approve {0} document(s)?", [items.length]), () => go());
		}
	}
};
