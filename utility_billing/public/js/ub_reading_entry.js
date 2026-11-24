// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Reading Entry (increment 47): a reading cycle's meters, a row an account; only the reading is typed.
// Data: utility_billing.utility_billing.reading_entry.get_sheet and save_rows. Each row is saved as its Meter Reading document.

window.UtilityBillingReadingEntry = class UtilityBillingReadingEntry {
	constructor(wrapper) {
		this.$root = $('<div class="ub-root ub-re"></div>').appendTo($(wrapper).find(".layout-main-section"));
		this.q = { society: null, cycle: null, route: "", show: "all", search: "", start: 0 };
		this.dirty = new Set();
		const reload = (change) => this.leave(() => { Object.assign(this.q, change, change.start === undefined ? { start: 0 } : {}); this.load(); });
		this.$root.on("change", ".ub-re-f", (e) => {
			const k = $(e.target).attr("data-f");
			reload(k === "society" ? { society: e.target.value, cycle: null, route: "" } : k === "cycle" ? { cycle: e.target.value, route: "" } : { [k]: e.target.value });
		});
		this.$root.on("change", ".ub-re-only", (e) => reload({ show: e.target.checked ? "exceptions" : "all" }));
		this.$root.on("keydown", ".ub-re-search", (e) => e.key === "Enter" && reload({ search: e.target.value }));
		this.$root.on("click", ".ub-re-save", () => this.save());
		this.$root.on("click", ".ub-re-bulk", () => this.approve_by_kind());
		this.$root.on("click", ".ub-re-page", (e) => reload({ start: Math.max(0, this.q.start + Number($(e.currentTarget).attr("data-step")) * this.data.page) }));
		this.$root.on("click", ".ub-re-open", (e) => frappe.set_route("Form", "Meter Reading", $(e.currentTarget).attr("data-name")));
		this.$root.on("input change", ".ub-re-in", (e) => this.touched($(e.target).closest("tr")));
		this.$root.on("keydown", ".ub-re-in", (e) => this.move(e));
		$(document).on("keydown.ub_re", (e) => {
			if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && frappe.get_route_str() === "ub-reading-entry") {
				e.preventDefault();
				this.save();
			}
		});
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	num(v, digits = 0) {
		return v === null || v === undefined || v === "" ? "" : Number(v).toLocaleString("en-US", { maximumFractionDigits: digits });
	}

	// Leaving the page of rows (another filter or page): the typed rows are saved first, so nothing typed is lost.
	leave(then) {
		if (!this.dirty.size) return then();
		this.save().then((ok) => ok && then());
	}

	load() {
		this.$root.html(`<div class="ub-card ub-empty">${__("Loading the reading sheet…")}</div>`);
		return frappe
			.xcall("utility_billing.utility_billing.reading_entry.get_sheet", this.q)
			.then((d) => {
				this.data = d;
				this.dirty.clear();
				Object.assign(this.q, { society: d.society || this.q.society, cycle: d.cycle || this.q.cycle });
				this.$root.html(this.render(d));
				this.$root.find("tr[data-name] .ub-re-in:not(:disabled)").first().trigger("focus");
			})
			.catch(() => this.$root.html(`<div class="ub-card ub-empty">${__("The reading sheet could not be loaded.")}</div>`));
	}

	options(list, current, value = "name", label = "label") {
		return list.map((o) => `<option value="${this.esc(o[value])}" ${o[value] === current ? "selected" : ""}>${this.esc(o[label])}</option>`).join("");
	}

	head(d) {
		const routes = [{ name: "", label: __("All routes") }, ...(d.routes || []).map((r) => ({ name: r, label: r }))];
		return `<div class="ub-head">
			<div><h1>${__("Reading Entry")}</h1><p>${d.cycle ? `${this.esc(d.society_name || d.society)} · ${this.esc(d.utility)} · ${this.esc(d.month_label)}` : __("No reading cycle yet")}</p></div>
			<div class="ub-tools">
				${d.status === "Closed" ? `<span class="ub-badge">${__("Closed: readings can't change")}</span>` : ""}
				<select class="ub-select ub-re-f" data-f="society" aria-label="${__("Society")}">${this.options(d.societies, d.society)}</select>
				<select class="ub-select ub-re-f" data-f="cycle" aria-label="${__("Reading cycle")}">${this.options(d.cycles, d.cycle)}</select>
				${d.cycle ? `<select class="ub-select ub-re-f" data-f="route" aria-label="${__("Route")}">${this.options(routes, d.route)}</select>` : ""}
			</div>
		</div>`;
	}

	totals(t) {
		const card = (label, value, tone = "") => `<div class="ub-re-stat ${tone}"><div class="ub-re-stat-label">${label}</div><div class="ub-re-stat-value">${value}</div></div>`;
		return `<div class="ub-re-stats">
			${card(__("Records"), this.num(t.records))}
			${card(__("Entered"), this.num(t.entered), t.entered >= t.records && t.records ? "green" : "")}
			${card(__("To enter"), this.num(t.records - t.entered), t.records - t.entered ? "amber" : "green")}
			${card(__("Exceptions"), this.num(t.exceptions), t.exceptions ? "red" : "")}
			${card(__("Not read"), this.num(t.not_read))}
			${card(__("Import units"), this.num(t.import_units))}
			${card(__("Export units"), this.num(t.export_units))}
			${t.smart ? card(__("Smart meters"), this.num(t.smart)) : ""}
		</div>`;
	}

	tone(status) {
		return { Valid: "green", Approved: "green", Flagged: "red", "Re-read Requested": "amber", Estimated: "blue" }[status] || "";
	}

	// A smart meter's reading isn't typed unless the meter is marked as not communicating.
	locked(r) {
		return !this.data.can_write || (r.smart_meter && !r.smart_meter_mute);
	}

	row(r, i) {
		const esc = (v) => this.esc(v);
		const off = this.locked(r);
		const not_read = !!r.exception_code;
		const input = (field, value, extra = "") =>
			`<input class="ub-re-in ub-re-num" data-f="${field}" inputmode="decimal" value="${esc(value)}" ${off || not_read ? "disabled" : ""} ${extra} aria-label="${field}">`;
		const smart = r.smart_meter
			? `<span class="ub-pill blue" title="${__("Smart meter: its reading comes from the MDM")}">${__("Smart")}</span>
			   <label class="ub-re-mute" title="${__("Not communicating: read by the meter reader")}"><input type="checkbox" class="ub-re-in" data-f="smart_meter_mute" ${r.smart_meter_mute ? "checked" : ""} ${this.data.can_write ? "" : "disabled"}> ${__("Mute")}</label>`
			: "";
		const codes = [{ name: "", label: "" }, ...this.data.codes];
		return `<tr data-name="${esc(r.name)}" data-i="${i}" class="${r.entered ? "is-entered" : ""} ${off ? "is-locked" : ""}">
			<td class="ub-re-seq">${this.q.start + i + 1}</td>
			<td><button class="ub-re-open" data-name="${esc(r.name)}" title="${__("Open the reading")}">${esc(r.connection)}</button><div class="sub">${esc(r.consumer_name)}</div></td>
			<td>${esc(r.property)}<div class="sub">${esc(r.category)}${r.sanctioned_load_kw ? ` · ${this.num(r.sanctioned_load_kw, 2)} kW` : ""}</div></td>
			<td>${esc(r.meter)} ${smart}<div class="sub">×${this.num(r.multiplier, 2)} · ${r.digits} ${__("digits")}</div></td>
			<td class="num">${this.num(r.previous_reading, 2)}<div class="sub">${r.previous_reading_date ? frappe.datetime.str_to_user(r.previous_reading_date) : ""}</div></td>
			<td>${input("present_reading", r.entered && !not_read ? r.present_reading : "")}</td>
			<td>${r.net_metering ? input("present_export_reading", r.entered && !not_read ? r.present_export_reading : "", `title="${__("Previous export reading")}: ${this.num(r.previous_export_reading, 2)}"`) : ""}</td>
			<td>${input("mdi_kw", r.mdi_kw || "")}</td>
			<td><input type="date" class="ub-re-in ub-re-date" data-f="reading_date" value="${esc(r.reading_date)}" max="${esc(this.data.today)}" ${off || not_read ? "disabled" : ""} aria-label="reading_date"></td>
			<td><select class="ub-re-in ub-re-code" data-f="exception_code" ${off ? "disabled" : ""} aria-label="exception_code">${this.options(codes, r.exception_code || "")}</select></td>
			<td class="num ub-re-units">${this.units(r)}</td>
			<td class="ub-re-state">${this.state(r)}</td>
		</tr>`;
	}

	units(r) {
		if (!r.entered && r.status === "Pending") return "";
		return `${this.num(r.units, 2)}${r.net_metering && r.export_units ? `<div class="sub">${__("export")} ${this.num(r.export_units, 2)}</div>` : ""}`;
	}

	state(r) {
		const note = r.error || r.checks || "";
		return `<span class="ub-pill ${r.error ? "red" : this.tone(r.status)}">${r.error ? __("Not saved") : __(r.status)}</span>${
			r.source && r.source !== "Office" && r.entered ? ` <span class="ub-pill">${this.esc(r.source)}</span>` : ""
		}${note ? `<div class="sub ${r.error ? "ub-re-err" : ""}">${this.esc(note).replace(/\n/g, "<br>")}</div>` : ""}`;
	}

	render(d) {
		if (!d.cycle) return `${this.head(d)}<div class="ub-card ub-empty">${__("Create a reading cycle and its reading sheet first.")}</div>`;
		const rows = d.rows.map((r, i) => this.row(r, i)).join("");
		const from = d.rows.length ? d.start + 1 : 0;
		return `${this.head(d)}
			${this.totals(d.totals)}
			<section class="ub-card ub-re-card">
				<div class="ub-re-bar">
					<input class="ub-select ub-re-search" placeholder="${__("Find a reference, name or meter and press Enter")}" value="${this.esc(d.search)}" aria-label="${__("Search")}">
					<label class="ub-re-check"><input type="checkbox" class="ub-re-only" ${d.show === "exceptions" ? "checked" : ""}> ${__("Show exceptions only")}</label>
					<span class="ub-re-grow"></span>
					<span class="ub-re-count">${__("Rows {0} to {1}", [from, d.start + d.rows.length])}</span>
					<button class="ub-btn ub-re-page" data-step="-1" ${d.start ? "" : "disabled"}>${__("Previous")}</button>
					<button class="ub-btn ub-re-page" data-step="1" ${d.more ? "" : "disabled"}>${__("Next")}</button>
					${d.can_review && d.totals.exceptions ? `<button class="ub-btn ub-re-bulk">${__("Approve by Kind")}</button>` : ""}
					${d.can_write ? `<button class="ub-btn ub-btn-primary ub-re-save" disabled>${__("Save")}</button>` : ""}
				</div>
				<div class="ub-re-scroll"><table class="ub-tbl ub-re-tbl">
					<thead><tr><th>#</th><th>${__("Reference No")}</th><th>${__("Property")}</th><th>${__("Meter")}</th><th class="num">${__("Previous")}</th>
						<th>${__("Present")}</th><th>${__("Export")}</th><th>${__("MDI")}</th><th>${__("Read On")}</th><th>${__("Not Read")}</th>
						<th class="num">${__("Units")}</th><th>${__("Status")}</th></tr></thead>
					<tbody>${rows || `<tr><td colspan="12" class="ub-empty">${__("No readings match.")}</td></tr>`}</tbody>
				</table></div>
				<div class="ub-kpi-note ub-re-hint">${d.can_write
					? __("Type the present reading and press Enter to go to the next account. Units are worked out from the readings when you save (Ctrl+S). A smart meter is typed only when Mute is ticked.")
					: __("You can see these readings but not change them.")}</div>
			</section>`;
	}

	// A reviewer accepts every flagged reading of one kind (zero use, say) with one reason, instead of opening each (increment 49).
	approve_by_kind() {
		const RDG = "utility_billing.utility_billing.readings.";
		frappe.xcall(RDG + "exception_kinds", { cycle: this.q.cycle }).then((kinds) => {
			const list = kinds.filter((k) => k.kind);
			if (!list.length) {
				return frappe.msgprint(__("Each flagged reading here has more than one flag. Open them one at a time."));
			}
			const d = new frappe.ui.Dialog({
				title: __("Approve flagged readings of one kind"),
				fields: [
					{ fieldname: "kind", fieldtype: "Select", label: __("Kind of flag"), reqd: 1,
					  options: list.map((k) => ({ value: k.kind, label: `${__(k.kind)} (${this.num(k.count)})` })) },
					{ fieldname: "note", fieldtype: "Small Text", label: __("Why these readings are accepted"), reqd: 1,
					  description: __("Kept on each reading. A reading with another flag as well is left for a look of its own.") },
				],
				primary_action_label: __("Approve as Read"),
				primary_action: (v) => {
					d.hide();
					this.leave(() => this.approve_kind(RDG, v.kind, v.note, list.find((k) => k.kind === v.kind).count));
				},
			});
			d.show();
		});
	}

	approve_kind(RDG, kind, note, total) {
		const title = __("Approving the readings");
		let done = 0;
		const end = (message, indicator) => {
			frappe.hide_progress();
			frappe.show_alert({ message, indicator }, 8);
			this.load();
		};
		const next = () =>
			frappe.xcall(RDG + "approve_kind", { cycle: this.q.cycle, kind, note, limit: 200 }).then(
				(r) => {
					done += r.approved;
					frappe.show_progress(title, done, Math.max(total, done), __("{0} of {1}", [this.num(done), this.num(Math.max(total, done))]));
					if (r.remaining && r.approved) return next();
					end(done === 1 ? __("1 reading approved.") : __("{0} readings approved.", [this.num(done)]), "green");
				},
				() => end(__("{0} readings approved before it stopped.", [this.num(done)]), "orange")
			);
		frappe.show_progress(title, 0, total, __("Starting"));
		return next();
	}

	touched($tr) {
		const name = $tr.attr("data-name");
		if (!name) return;
		this.dirty.add(name);
		$tr.addClass("is-dirty");
		const r = this.data.rows[Number($tr.attr("data-i"))];
		// a reason for not reading the meter, or the Mute tick, changes which cells take typing
		const code = $tr.find('[data-f="exception_code"]').val();
		const mute = $tr.find('[data-f="smart_meter_mute"]').prop("checked");
		const off = !this.data.can_write || (r.smart_meter && !mute);
		$tr.find(".ub-re-num, .ub-re-date").prop("disabled", off || !!code);
		$tr.find(".ub-re-code").prop("disabled", off);
		this.$root.find(".ub-re-save").prop("disabled", false).text(__("Save ({0})", [this.dirty.size]));
	}

	// Enter and the arrow keys move down or up the same column, as on a keypunch.
	move(e) {
		const step = e.key === "Enter" || e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
		if (!step || $(e.target).is("select")) return;
		e.preventDefault();
		const field = $(e.target).attr("data-f");
		let $tr = $(e.target).closest("tr");
		for (let n = 0; n < 500; n++) {
			$tr = step > 0 ? $tr.next("tr") : $tr.prev("tr");
			if (!$tr.length) return;
			const $next = $tr.find(`[data-f="${field}"]:not(:disabled)`);
			if ($next.length) return $next.trigger("focus").trigger("select");
		}
	}

	collect($tr) {
		const row = { name: $tr.attr("data-name") };
		$tr.find(".ub-re-in").each((_i, el) => {
			const f = $(el).attr("data-f");
			row[f] = el.type === "checkbox" ? (el.checked ? 1 : 0) : el.value.trim();
		});
		const r = this.data.rows[Number($tr.attr("data-i"))];
		if (!r.net_metering) delete row.present_export_reading;
		if (!r.smart_meter) delete row.smart_meter_mute;
		return row;
	}

	save() {
		if (!this.dirty.size) return Promise.resolve(true);
		const bad = [];
		const rows = [...this.dirty].map((name) => {
			const $tr = this.$root.find(`tr[data-name="${name}"]`);
			const row = this.collect($tr);
			for (const f of ["present_reading", "present_export_reading", "mdi_kw"]) {
				if (row[f] !== undefined && row[f] !== "" && (isNaN(Number(row[f])) || Number(row[f]) < 0)) bad.push(row.name);
			}
			return row;
		});
		if (bad.length) {
			frappe.show_alert({ message: __("A reading must be a number, zero or more. Check the rows marked."), indicator: "red" });
			bad.forEach((name) => this.$root.find(`tr[data-name="${name}"]`).addClass("is-failed"));
			return Promise.resolve(false);
		}
		const $save = this.$root.find(".ub-re-save").prop("disabled", true).text(__("Saving…"));
		return frappe
			.xcall("utility_billing.utility_billing.reading_entry.save_rows", { cycle: this.q.cycle, rows })
			.then((res) => {
				res.rows.forEach((x) => {
					const $tr = this.$root.find(`tr[data-name="${x.name}"]`);
					const i = Number($tr.attr("data-i"));
					if (x.ok) {
						this.dirty.delete(x.name);
						const kept = this.data.rows[i];
						this.data.rows[i] = Object.assign({}, kept, x, { error: null });
						const $new = $(this.row(this.data.rows[i], i));
						$tr.replaceWith($new);
					} else {
						this.data.rows[i].error = x.error;
						$tr.addClass("is-failed").find(".ub-re-state").html(this.state(this.data.rows[i]));
					}
				});
				this.$root.find(".ub-re-stats").replaceWith(this.totals(res.totals));
				$save.prop("disabled", !this.dirty.size).text(this.dirty.size ? __("Save ({0})", [this.dirty.size]) : __("Save"));
				frappe.show_alert(
					res.failed
						? { message: __("{0} saved, {1} not saved: see the rows marked.", [res.saved, res.failed]), indicator: "orange" }
						: { message: res.saved === 1 ? __("1 reading saved.") : __("{0} readings saved.", [res.saved]), indicator: "green" }
				);
				return !res.failed;
			})
			.catch(() => {
				$save.prop("disabled", false).text(__("Save ({0})", [this.dirty.size]));
				return false;
			});
	}
};
