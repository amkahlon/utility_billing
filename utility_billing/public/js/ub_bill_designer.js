// Bill Designer (increment 27): pick a ready-made bill layout, or design a society's own from blocks, with a live preview.
// The layout is sections -> rows -> cells -> blocks; every block's options come from the server (bill_designer.BLOCKS), so
// a new block needs no change here. The preview is rendered by the server with the same code as the printed PDF.
(() => {
	const M = "utility_billing.utility_billing.bill_designer.";
	const e = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const clone = (x) => JSON.parse(JSON.stringify(x));
	const MM = 96 / 25.4; // CSS pixels per millimetre

	const CSS = `
	.ub-bd { --bd-line: #d9dee3; --bd-soft: #f4f6f8; --bd-accent: #1f5f8b; }
	.ub-bd .bd-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
	.ub-bd .bd-bar .form-control, .ub-bd .bd-bar select { height: 32px; width: auto; min-width: 140px; }
	.ub-bd .bd-bar .bd-name { min-width: 260px; font-weight: 600; }
	.ub-bd .bd-state { font-size: 12px; padding: 3px 10px; border-radius: 12px; background: var(--bd-soft); border: 1px solid var(--bd-line); }
	.ub-bd .bd-grow { flex: 1; }
	.ub-bd .bd-main { display: grid; grid-template-columns: 330px minmax(0, 1fr) 300px; gap: 12px; align-items: start; }
	.ub-bd .bd-panel { background: #fff; border: 1px solid var(--bd-line); border-radius: 10px; padding: 10px; max-height: calc(100vh - 210px); overflow: auto; }
	.ub-bd .bd-panel h4 { font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #5b6670; margin: 2px 0 8px; }
	.ub-bd .bd-sec { border: 1px solid var(--bd-line); border-radius: 8px; margin-bottom: 10px; background: var(--bd-soft); }
	.ub-bd .bd-sec-h { display: flex; align-items: center; gap: 4px; padding: 6px 8px; font-weight: 600; font-size: 12px; cursor: pointer; }
	.ub-bd .bd-sec-h .bd-t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.ub-bd .bd-row { display: flex; gap: 4px; padding: 4px 6px; border-top: 1px dashed var(--bd-line); align-items: stretch; }
	.ub-bd .bd-row-tools { display: flex; flex-direction: column; gap: 2px; }
	.ub-bd .bd-cell { background: #fff; border: 1px solid var(--bd-line); border-radius: 6px; padding: 4px; min-height: 38px; min-width: 0; cursor: pointer; }
	.ub-bd .bd-cell.drop { outline: 2px dashed var(--bd-accent); }
	.ub-bd .bd-cell-w { font-size: 10px; color: #8a959e; display: flex; justify-content: space-between; }
	.ub-bd .bd-chip { display: block; font-size: 11px; padding: 3px 6px; margin: 3px 0; border-radius: 5px; background: #eef2f5; border: 1px solid transparent;
		cursor: grab; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.ub-bd .bd-chip:hover { border-color: var(--bd-line); }
	.ub-bd .sel { box-shadow: 0 0 0 2px var(--bd-accent); }
	.ub-bd .bd-x { border: 0; background: none; color: #6b7680; padding: 0 4px; font-size: 12px; line-height: 16px; cursor: pointer; }
	.ub-bd .bd-x:hover { color: #1d2329; }
	.ub-bd .bd-add { font-size: 11px; padding: 2px 6px; }
	.ub-bd .bd-palette { display: flex; flex-wrap: wrap; gap: 5px; }
	.ub-bd .bd-pal { font-size: 12px; padding: 4px 8px; border-radius: 6px; background: #fff; border: 1px solid var(--bd-line); cursor: grab; user-select: none; }
	.ub-bd .bd-pal:hover { border-color: var(--bd-accent); color: var(--bd-accent); }
	.bd-drag-cover { position: fixed; inset: 0; z-index: 2000; cursor: grabbing; }
	.bd-drag-ghost { position: fixed; z-index: 2001; pointer-events: none; background: #1f5f8b; color: #fff; font: 12px sans-serif; padding: 4px 9px; border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
	.ub-bd .bd-preview { background: #e9edf1; border: 1px solid var(--bd-line); border-radius: 10px; overflow: hidden; }
	.ub-bd .bd-preview-bar { display: flex; gap: 8px; align-items: center; padding: 8px; background: #fff; border-bottom: 1px solid var(--bd-line); flex-wrap: wrap; }
	.ub-bd .bd-preview-bar select { height: 30px; width: auto; }
	.ub-bd .bd-fit { font-size: 12px; padding: 3px 10px; border-radius: 12px; }
	.ub-bd .bd-fit.ok { background: #e7f6ec; color: #1b6b35; }
	.ub-bd .bd-fit.over { background: #fdf0ef; color: #8f1d12; }
	.ub-bd .bd-frame-wrap { height: calc(100vh - 260px); overflow: auto; position: relative; }
	.ub-bd .bd-frame { border: 0; width: 100%; transform-origin: 0 0; background: transparent; }
	.ub-bd .bd-opt { margin-bottom: 10px; }
	.ub-bd .bd-opt label { font-size: 12px; color: #3b4650; display: block; margin-bottom: 3px; }
	.ub-bd .bd-opt .form-control { height: 30px; font-size: 13px; }
	.ub-bd .bd-opt textarea.form-control { height: 90px; }
	.ub-bd .bd-opt .bd-check { display: flex; gap: 6px; align-items: center; font-size: 13px; color: #1d2329; margin: 2px 0; }
	.ub-bd .bd-field { display: flex; gap: 4px; align-items: center; margin-bottom: 4px; }
	.ub-bd .bd-field span { flex: 0 0 110px; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.ub-bd .bd-field input { height: 26px; font-size: 12px; }
	.ub-bd .bd-help { font-size: 12px; color: #6b7680; margin-bottom: 8px; }
	.ub-bd .bd-banner { padding: 8px 12px; border-radius: 8px; margin-bottom: 10px; background: #fff7e6; border: 1px solid #f3d9a4; font-size: 13px; }
	.ub-bd .bd-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 16px; }
	.ub-bd .bd-card { background: #fff; border: 1px solid var(--bd-line); border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; }
	.ub-bd .bd-thumb { height: 330px; overflow: hidden; background: #e9edf1; position: relative; }
	.ub-bd .bd-thumb iframe { border: 0; width: 840px; height: 1250px; transform: scale(.3); transform-origin: 0 0; pointer-events: none; position: absolute; left: 0; top: 0; }
	.ub-bd .bd-card-b { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
	.ub-bd .bd-card-b .bd-actions { display: flex; gap: 6px; flex-wrap: wrap; }
	.ub-bd .bd-card h3 { font-size: 15px; margin: 0; }
	.ub-bd .bd-mine td, .ub-bd .bd-mine th { padding: 6px 8px; border-bottom: 1px solid var(--bd-line); font-size: 13px; }
	.ub-bd .bd-mine { width: 100%; background: #fff; border: 1px solid var(--bd-line); border-radius: 10px; }
	@media (max-width: 1200px) { .ub-bd .bd-main { grid-template-columns: 1fr; } .ub-bd .bd-panel, .ub-bd .bd-frame-wrap { max-height: none; height: auto; } }
	`;

	class BillDesigner {
		constructor(wrapper) {
			this.wrapper = wrapper;
			if (!document.getElementById("ub-bd-css")) $(`<style id="ub-bd-css">${CSS}</style>`).appendTo("head");
			this.$root = $('<div class="ub-root ub-bd"></div>').appendTo($(wrapper).find(".layout-main-section"));
			this.undo = [];
			this.bill = "";
		}

		// ------------------------------------------------------------ loading
		async load(template) {
			this.meta = this.meta || (await frappe.xcall(M + "designer_data", { template: template || null }));
			if (template) {
				const data = await frappe.xcall(M + "designer_data", { template });
				this.meta.template = data.template;
				this.open_template(data.template);
			} else {
				this.society = this.society || (this.meta.societies[0] || {}).name;
				this.gallery();
			}
		}

		block_meta(type) {
			return this.meta.blocks.find((b) => b.type === type);
		}

		default_options(type) {
			const out = {};
			(this.block_meta(type).options || []).forEach((o) => (out[o.key] = clone(o.default)));
			return out;
		}

		// ------------------------------------------------------------ gallery: ready-made layouts, the society's templates
		async gallery() {
			this.mode = "gallery";
			const socs = this.meta.societies;
			this.$root.html(`
				<div class="ub-head"><div><div class="ub-hello">${__("Bill printing")}</div><h1>${__("Bill Designer")}</h1>
					<p>${__("Choose a ready-made bill layout and use it as it is, or customise it block by block. A template is used for bills once it is approved.")}</p></div>
					<div class="ub-tools"><select class="ub-select bd-soc">${socs.map((s) => `<option value="${e(s.name)}" ${s.name === this.society ? "selected" : ""}>${e(s.society_name)}</option>`).join("")}</select></div></div>
				<section class="ub-card"><div class="ub-cardhead"><h2>${__("Ready-made layouts")}</h2></div>
					<div class="bd-help">${__("Shown with this society's logo, notices and bank details, on a sample bill.")}</div>
					<div class="bd-gallery"></div>
					<div style="margin-top:14px; display:flex; gap:8px; flex-wrap:wrap;">
						<button class="ub-btn bd-blank">${__("Start from a blank page")}</button>
						<button class="ub-btn bd-import">${__("Import a template file")}</button>
					</div></section>
				<section class="ub-card"><div class="ub-cardhead"><h2>${__("This society's templates")}</h2></div><div class="bd-mine-wrap"></div></section>`);
			this.$root.find(".bd-soc").on("change", (ev) => {
				this.society = ev.target.value;
				this.gallery();
			});
			this.$root.find(".bd-blank").on("click", () => this.new_template({ sections: [{ label: __("Consumer copy"), copy_label: __("Consumer copy"), rows: [] }] }, {}, __("New bill template")));
			this.$root.find(".bd-import").on("click", () => this.import_file());
			const $g = this.$root.find(".bd-gallery");
			for (const s of this.meta.starters) {
				const $c = $(`<div class="bd-card"><div class="bd-thumb"><iframe></iframe></div><div class="bd-card-b"><h3>${e(s.label)}</h3>
					<div class="bd-actions"><button class="ub-btn ub-btn-primary bd-use">${__("Use this layout")}</button>
					<button class="ub-btn bd-custom">${__("Customise")}</button></div></div></div>`).appendTo($g);
				frappe.xcall(M + "starter", { key: s.key }).then(async (st) => {
					$c.find(".bd-use").on("click", () => this.use_starter(s, st));
					$c.find(".bd-custom").on("click", () => this.new_template(st.layout, st.page, s.label));
					const html = await frappe.xcall(M + "preview", { society: this.society, layout: JSON.stringify(st.layout), page: JSON.stringify(st.page) });
					$c.find("iframe").attr("srcdoc", html);
				});
			}
			const mine = await frappe.db.get_list("Bill Template", {
				filters: { society: this.society }, fields: ["name", "template_name", "workflow_state", "docstatus", "effective_from", "utility", "consumer_category"],
				order_by: "modified desc", limit: 50,
			});
			this.$root.find(".bd-mine-wrap").html(mine.length
				? `<table class="bd-mine"><tr><th>${__("Template")}</th><th>${__("Status")}</th><th>${__("Used from")}</th><th>${__("For")}</th><th></th></tr>${mine.map((t) => `
					<tr><td>${e(t.template_name)} <span class="text-muted">${e(t.name)}</span></td><td>${e(t.workflow_state || "Draft")}</td>
					<td>${frappe.datetime.str_to_user(t.effective_from)}</td><td>${e([t.utility, t.consumer_category].filter(Boolean).join(", ") || __("All bills"))}</td>
					<td class="text-right"><button class="ub-btn bd-open" data-n="${e(t.name)}">${t.docstatus === 0 ? __("Edit") : __("View")}</button>
					<button class="ub-btn bd-copy" data-n="${e(t.name)}">${__("Copy")}</button></td></tr>`).join("")}</table>`
				: `<div class="bd-help">${__("No templates yet. Until one is approved, this society's bills print with the standard layout.")}</div>`);
			this.$root.find(".bd-open").on("click", (ev) => this.load($(ev.currentTarget).attr("data-n")));
			this.$root.find(".bd-copy").on("click", async (ev) => {
				const d = await frappe.xcall(M + "designer_data", { template: $(ev.currentTarget).attr("data-n") });
				const t = d.template;
				this.new_template(t.layout, t, __("Copy of {0}", [t.template_name]));
			});
		}

		async use_starter(s, st) {
			const name = await frappe.xcall(M + "save_template", { values: JSON.stringify(Object.assign({ template_name: s.label, society: this.society, effective_from: frappe.datetime.get_today(), layout: st.layout }, st.page)) });
			frappe.show_alert({ message: __("Template {0} saved as a draft. Submit it for approval to use it for bills.", [name]), indicator: "green" });
			frappe.set_route("Form", "Bill Template", name);
		}

		new_template(layout, page, name) {
			this.open_template(Object.assign({ name: null, template_name: name, society: this.society, utility: null, consumer_category: null,
				effective_from: frappe.datetime.get_today(), paper_size: "A4", font_family: "Helvetica / Arial", font_size: 9, margin_top: 6, margin_side: 8, pages_per_bill: "1",
				accent_colour: null, docstatus: 0, can_write: true }, clone(page || {}), { name: null, docstatus: 0, workflow_state: null, can_write: true, layout: clone(layout) }));
			this.dirty = true;
			this.render_state();
		}

		// ------------------------------------------------------------ editor
		open_template(t) {
			this.mode = "editor";
			this.t = t;
			this.society = t.society;
			this.layout = clone(t.layout || { sections: [] });
			this.sel = null;
			this.dirty = false;
			this.undo = [];
			const ro = !t.can_write;
			this.$root.html(`
				${ro ? `<div class="bd-banner">${__("This template is {0} and can't change. To change it, amend it from its form; the new version is approved again.", [e(t.workflow_state || __("approved"))])}</div>` : ""}
				<div class="bd-bar">
					<button class="ub-btn bd-back" title="${__("All layouts")}">← ${__("Layouts")}</button>
					<input class="form-control bd-name" value="${e(t.template_name)}" placeholder="${__("Template name")}" ${ro ? "disabled" : ""}>
					<span class="bd-state"></span>
					<span class="bd-grow"></span>
					<button class="ub-btn bd-undo" ${ro ? "disabled" : ""}>${__("Undo")}</button>
					<button class="ub-btn bd-page" ${ro ? "disabled" : ""}>${__("Page and use")}</button>
					<button class="ub-btn bd-fitcheck">${__("Check fit")}</button>
					<button class="ub-btn bd-pdf">${__("PDF")}</button>
					<button class="ub-btn bd-export" ${t.name ? "" : "disabled"}>${__("Export")}</button>
					<button class="ub-btn bd-form" ${t.name ? "" : "disabled"}>${__("Approval")}</button>
					<button class="ub-btn ub-btn-primary bd-save" ${ro ? "disabled" : ""}>${__("Save")}</button>
				</div>
				<div class="bd-main">
					<div class="bd-panel bd-tree"></div>
					<div class="bd-preview"><div class="bd-preview-bar">
						<select class="bd-bill"><option value="">${__("Sample bill")}</option></select>
						<select class="bd-zoom"><option value="fit">${__("Fit width")}</option><option value="1">100%</option><option value="0.75">75%</option></select>
						<span class="bd-fit"></span><span class="bd-grow"></span><span class="text-muted small">${__("The PDF button shows the exact print.")}</span></div>
						<div class="bd-frame-wrap"><iframe class="bd-frame"></iframe></div></div>
					<div class="bd-panel bd-opts"></div>
				</div>`);
			this.ro = ro;
			const $r = this.$root;
			$r.find(".bd-back").on("click", () => this.leave(() => this.gallery()));
			$r.find(".bd-name").on("input", () => (this.dirty = true));
			$r.find(".bd-undo").on("click", () => this.do_undo());
			$r.find(".bd-page").on("click", () => this.page_dialog());
			$r.find(".bd-save").on("click", () => this.save());
			$r.find(".bd-pdf").on("click", () => this.pdf());
			$r.find(".bd-fitcheck").on("click", () => this.fit_check());
			$r.find(".bd-export").on("click", () => window.open(`/api/method/${M}export_template?name=${encodeURIComponent(this.t.name)}`));
			$r.find(".bd-form").on("click", () => this.leave(() => frappe.set_route("Form", "Bill Template", this.t.name)));
			$r.find(".bd-zoom").on("change", () => this.zoom());
			$r.find(".bd-bill").on("change", (ev) => {
				this.bill = ev.target.value;
				this.refresh();
			});
			frappe.xcall(M + "society_bills", { society: this.society }).then((bills) => {
				$r.find(".bd-bill").append(bills.map((b) => `<option value="${e(b.name)}">${e(b.name)} · ${e(b.consumer_name || "")}</option>`).join(""));
				if (this.bill && bills.some((b) => b.name === this.bill)) $r.find(".bd-bill").val(this.bill);
				else this.bill = "";
			});
			$(window).off("resize.ubbd").on("resize.ubbd", () => { this.zoom(); this.place_handles && this.idoc && this.place_handles(); });
			$(document).off("keydown.ubbd").on("keydown.ubbd", (ev) => {
				if (frappe.get_route_str().includes("ub-bill-designer") && this.mode === "editor") this.on_key(ev);
			});
			this.render_state();
			this.tree();
			this.options();
			this.refresh();
		}

		leave(go) {
			if (this.dirty && !this.ro) {
				frappe.confirm(__("Leave without saving your changes?"), go);
			} else go();
		}

		render_state() {
			const t = this.t;
			this.$root.find(".bd-state").text(t.name ? `${t.name} · ${t.workflow_state || __("Draft")}` : __("Not saved yet"));
		}

		change(fn) {
			if (this.ro) return;
			this.undo.push(JSON.stringify(this.layout));
			if (this.undo.length > 60) this.undo.shift();
			fn();
			this.dirty = true;
			this.tree();
			this.options();
			this.refresh();
		}

		do_undo() {
			if (!this.undo.length) return;
			this.layout = JSON.parse(this.undo.pop());
			this.sel = null;
			this.dirty = true;
			this.tree();
			this.options();
			this.refresh();
		}

		// ------------------------------------------------------------ the layout tree
		tree() {
			const L = this.layout, ro = this.ro;
			const btn = (cls, label, title, data) => (ro ? "" : `<button class="bd-x ${cls}" title="${e(title)}" ${data}>${label}</button>`);
			const isSel = (kind, ...path) => this.sel && this.sel.kind === kind && path.every((p, i) => this.sel.path[i] === p);
			const html = L.sections.map((s, si) => `
				<div class="bd-sec ${isSel("section", si) ? "sel" : ""}">
					<div class="bd-sec-h" data-s="${si}"><span class="bd-t">${e(s.label || __("Section {0}", [si + 1]))}${s.copy_label ? ` · <span class="text-muted">${e(s.copy_label)}</span>` : ""}</span>
						${btn("bd-sec-up", "↑", __("Move up"), `data-s="${si}"`)}${btn("bd-sec-down", "↓", __("Move down"), `data-s="${si}"`)}${btn("bd-sec-del", "✕", __("Delete section"), `data-s="${si}"`)}</div>
					${s.rows.map((r, ri) => `
						<div class="bd-row">
							${r.cells.map((c, ci) => `
								<div class="bd-cell ${isSel("cell", si, ri, ci) ? "sel" : ""}" style="flex: ${c.width} 1 0;" data-p="${si},${ri},${ci}">
									<div class="bd-cell-w"><span>${c.width}%</span>${btn("bd-cell-del", "✕", __("Delete column"), `data-p="${si},${ri},${ci}"`)}</div>
									${c.blocks.map((b, bi) => `<span class="bd-chip ${isSel("block", si, ri, ci, bi) ? "sel" : ""}" draggable="${!ro}" data-p="${si},${ri},${ci},${bi}">${e((this.block_meta(b.type) || {}).label || b.type)}</span>`).join("")}
									${ro ? "" : `<select class="form-control bd-add bd-add-block" data-p="${si},${ri},${ci}"><option value="">+ ${__("block")}</option>${this.meta.blocks.map((m) => `<option value="${m.type}">${e(m.label)}</option>`).join("")}</select>`}
								</div>`).join("")}
							<div class="bd-row-tools">${btn("bd-row-up", "↑", __("Move row up"), `data-p="${si},${ri}"`)}${btn("bd-row-down", "↓", __("Move row down"), `data-p="${si},${ri}"`)}${btn("bd-cell-add", "+", __("Add a column"), `data-p="${si},${ri}"`)}${btn("bd-row-del", "✕", __("Delete row"), `data-p="${si},${ri}"`)}</div>
						</div>`).join("")}
					${ro ? "" : `<div style="padding: 6px;"><button class="ub-btn bd-add bd-row-add" data-s="${si}">+ ${__("row")}</button></div>`}
				</div>`).join("");
			const palette = ro ? "" : `<h4>${__("Blocks")}</h4>
				<div class="bd-help">${__("Drag a block onto the bill. On the bill: drag a box to move it; click it, then drag its grips to change its width or height; double-click a title, heading, label or text to type over it.")}</div>
				<div class="bd-palette">${this.meta.blocks.map((m) => `<span class="bd-pal" data-t="${m.type}" title="${e(m.description)}">${e(m.label)}</span>`).join("")}</div>`;
			const $t = this.$root.find(".bd-tree").html(`${palette}<h4 style="margin-top:12px">${__("Outline")}</h4>${html}
				${ro ? "" : `<button class="ub-btn bd-sec-add">+ ${__("section (e.g. a tear-off copy)")}</button>`}`);
			const P = (el, a = "data-p") => $(el).attr(a).split(",").map(Number);
			$t.find(".bd-pal").on("mousedown", (ev) => {
				if (ev.button !== 0) return;
				ev.preventDefault();
				this.start_drag({ kind: "new", type: $(ev.currentTarget).attr("data-t"), label: $(ev.currentTarget).text() }, ev.clientX, ev.clientY);
			});
			$t.find(".bd-sec-h").on("click", (ev) => { if (!$(ev.target).is("button")) this.select("section", [+$(ev.currentTarget).attr("data-s")]); });
			$t.find(".bd-cell").on("click", (ev) => { if (ev.target === ev.currentTarget || $(ev.target).closest(".bd-cell-w").length && !$(ev.target).is("button")) this.select("cell", P(ev.currentTarget)); });
			$t.find(".bd-chip").on("click", (ev) => { ev.stopPropagation(); this.select("block", P(ev.currentTarget)); });
			$t.find(".bd-sec-add").on("click", () => this.change(() => {
				L.sections.push({ label: __("Copy"), copy_label: __("Office copy"), cut_line_before: 1, cut_text: __("cut here"), new_page: 0, rows: [{ cells: [{ width: 100, blocks: [] }] }] });
				this.sel = { kind: "section", path: [L.sections.length - 1] };
			}));
			$t.find(".bd-sec-up, .bd-sec-down").on("click", (ev) => this.change(() => this.move(L.sections, +$(ev.currentTarget).attr("data-s"), $(ev.currentTarget).hasClass("bd-sec-up") ? -1 : 1)));
			$t.find(".bd-sec-del").on("click", (ev) => frappe.confirm(__("Delete this section and its blocks?"), () => this.change(() => { L.sections.splice(+$(ev.currentTarget).attr("data-s"), 1); this.sel = null; })));
			$t.find(".bd-row-add").on("click", (ev) => this.change(() => L.sections[+$(ev.currentTarget).attr("data-s")].rows.push({ cells: [{ width: 100, blocks: [] }] })));
			$t.find(".bd-row-up, .bd-row-down").on("click", (ev) => { const [si, ri] = P(ev.currentTarget); this.change(() => this.move(L.sections[si].rows, ri, $(ev.currentTarget).hasClass("bd-row-up") ? -1 : 1)); });
			$t.find(".bd-row-del").on("click", (ev) => { const [si, ri] = P(ev.currentTarget); this.change(() => { L.sections[si].rows.splice(ri, 1); this.sel = null; }); });
			$t.find(".bd-cell-add").on("click", (ev) => {
				const [si, ri] = P(ev.currentTarget);
				this.change(() => {
					const cells = L.sections[si].rows[ri].cells;
					const n = cells.length + 1, w = Math.floor(100 / n);
					cells.forEach((c) => (c.width = w));
					cells.push({ width: 100 - w * (n - 1), blocks: [] });
				});
			});
			$t.find(".bd-cell-del").on("click", (ev) => {
				ev.stopPropagation();
				const [si, ri, ci] = P(ev.currentTarget);
				this.change(() => {
					const row = L.sections[si].rows[ri];
					const [gone] = row.cells.splice(ci, 1);
					if (row.cells.length) row.cells[Math.max(0, ci - 1)].width += gone.width;
					else L.sections[si].rows.splice(ri, 1);
					this.sel = null;
				});
			});
			$t.find(".bd-add-block").on("click", (ev) => ev.stopPropagation()).on("change", (ev) => {
				const type = ev.target.value;
				if (!type) return;
				const [si, ri, ci] = P(ev.currentTarget);
				this.change(() => {
					const blocks = L.sections[si].rows[ri].cells[ci].blocks;
					blocks.push({ type, options: this.default_options(type) });
					this.sel = { kind: "block", path: [si, ri, ci, blocks.length - 1] };
				});
			});
			// drag a block to another column
			$t.find(".bd-chip").on("dragstart", (ev) => ev.originalEvent.dataTransfer.setData("text/plain", $(ev.currentTarget).attr("data-p")));
			$t.find(".bd-cell").on("dragover", (ev) => { ev.preventDefault(); $(ev.currentTarget).addClass("drop"); })
				.on("dragleave", (ev) => $(ev.currentTarget).removeClass("drop"))
				.on("drop", (ev) => {
					ev.preventDefault();
					const from = (ev.originalEvent.dataTransfer.getData("text/plain") || "").split(",").map(Number);
					const [si, ri, ci] = P(ev.currentTarget);
					if (from.length !== 4) return;
					this.change(() => {
						const [b] = L.sections[from[0]].rows[from[1]].cells[from[2]].blocks.splice(from[3], 1);
						const to = L.sections[si].rows[ri].cells[ci].blocks;
						to.push(b);
						this.sel = { kind: "block", path: [si, ri, ci, to.length - 1] };
					});
				});
		}

		move(list, i, d) {
			const j = i + d;
			if (j < 0 || j >= list.length) return;
			[list[i], list[j]] = [list[j], list[i]];
			this.sel = null;
		}

		select(kind, path) {
			this.sel = { kind, path };
			this.tree();
			this.options();
			this.paint_selection();
		}

		// ------------------------------------------------------------ options panel
		options() {
			const $o = this.$root.find(".bd-opts");
			const s = this.sel, L = this.layout, ro = this.ro ? "disabled" : "";
			if (!s) {
				$o.html(`<h4>${__("Options")}</h4><div class="bd-help">${__("Select a section, a column or a block in the layout to change it.")}</div>
					<div class="bd-help">${__("Blocks")}: ${this.meta.blocks.map((b) => `<div><b>${e(b.label)}</b> — ${e(b.description)}</div>`).join("")}</div>`);
				return;
			}
			if (s.kind === "section") {
				const sec = L.sections[s.path[0]];
				$o.html(`<h4>${__("Section")}</h4>
					<div class="bd-opt"><label>${__("Name (for you)")}</label><input class="form-control" data-k="label" value="${e(sec.label)}" ${ro}></div>
					<div class="bd-opt"><label>${__("Copy label printed on the bill")}</label><input class="form-control" data-k="copy_label" value="${e(sec.copy_label)}" placeholder="${__("e.g. Bank Copy")}" ${ro}></div>
					<div class="bd-opt"><label class="bd-check"><input type="checkbox" data-k="cut_line_before" ${sec.cut_line_before ? "checked" : ""} ${ro}> ${__("Cut line before this section")}</label></div>
					<div class="bd-opt"><label>${__("Cut line text")}</label><input class="form-control" data-k="cut_text" value="${e(sec.cut_text)}" ${ro}></div>
					<div class="bd-opt"><label class="bd-check"><input type="checkbox" data-k="new_page" ${sec.new_page ? "checked" : ""} ${ro}> ${__("Start on a new page")}</label></div>
					<div class="bd-opt"><label class="bd-check"><input type="checkbox" data-k="english_only" ${sec.english_only ? "checked" : ""} ${ro}> ${__("Labels in English only (saves space on a tear-off copy)")}</label></div>`);
				$o.find("[data-k]").on("change", (ev) => {
					const k = $(ev.target).attr("data-k");
					this.change(() => (sec[k] = ev.target.type === "checkbox" ? (ev.target.checked ? 1 : 0) : ev.target.value));
				});
				return;
			}
			if (s.kind === "cell") {
				const row = L.sections[s.path[0]].rows[s.path[1]], cell = row.cells[s.path[2]];
				const others = row.cells.reduce((n, c, i) => n + (i === s.path[2] ? 0 : c.width), 0);
				$o.html(`<h4>${__("Column")}</h4>
					<div class="bd-opt"><label>${__("Width (% of the page)")}</label><input type="number" min="5" max="${100 - others}" class="form-control" value="${cell.width}" ${ro}></div>
					<div class="bd-help">${__("The other columns in this row take {0}%. Together they can't pass 100%.", [others])}</div>`);
				$o.find("input").on("change", (ev) => this.change(() => (cell.width = Math.max(5, Math.min(100 - others, parseInt(ev.target.value, 10) || 5)))));
				return;
			}
			const [si, ri, ci, bi] = s.path;
			const block = L.sections[si].rows[ri].cells[ci].blocks[bi];
			const meta = this.block_meta(block.type);
			const o = block.options;
			const fieldLabel = (k) => (this.meta.fields.find((f) => f.key === k) || {}).label || k;
			const parts = meta.options.map((op) => {
				const v = o[op.key];
				const id = `data-k="${op.key}"`;
				if (op.type === "check") return `<div class="bd-opt"><label class="bd-check"><input type="checkbox" ${id} ${v ? "checked" : ""} ${ro}> ${e(op.label)}</label></div>`;
				if (op.type === "int") return `<div class="bd-opt"><label>${e(op.label)}</label><input type="number" class="form-control" ${id} min="${op.min}" max="${op.max}" value="${e(v)}" ${ro}></div>`;
				if (op.type === "select") return `<div class="bd-opt"><label>${e(op.label)}</label><select class="form-control" ${id} ${ro}>${op.choices.map((c) => `<option value="${e(c)}" ${c === v ? "selected" : ""}>${e(__(c))}</option>`).join("")}</select></div>`;
				if (op.type === "textarea") return `<div class="bd-opt"><label>${e(op.label)}</label><textarea class="form-control" ${id} ${ro}>${e(v)}</textarea><div class="bd-help">${__("Placeholders")}: ${this.meta.fields.map((f) => `{${f.key}}`).join(" ")}</div></div>`;
				if (op.type === "text") return `<div class="bd-opt"><label>${e(op.label)}</label><input class="form-control" ${id} value="${e(v)}" ${ro}></div>`;
				if (op.type === "multi") return `<div class="bd-opt"><label>${e(op.label)}</label>${op.choices.map(([k, l]) => `<label class="bd-check"><input type="checkbox" data-m="${op.key}" value="${k}" ${(v || []).includes(k) ? "checked" : ""} ${ro}> ${e(l)}</label>`).join("")}</div>`;
				if (op.type === "image") return `<div class="bd-opt"><label>${e(op.label)}</label>${v ? `<div style="margin-bottom:4px"><img src="${e(v)}" style="max-height:40px;max-width:100%"></div>` : ""}
					<button class="ub-btn bd-upload" data-k="${op.key}" ${ro}>${v ? __("Change") : __("Upload")}</button> ${v ? `<button class="ub-btn bd-clear" data-k="${op.key}" ${ro}>${__("Remove")}</button>` : ""}</div>`;
				if (op.type === "fields") return `<div class="bd-opt"><label>${e(op.label)}</label>
					${(v || []).map((f, i) => `<div class="bd-field"><span title="${e(fieldLabel(f.key))}">${e(fieldLabel(f.key))}</span>
						<input class="form-control bd-flabel" data-i="${i}" value="${e(f.label)}" placeholder="${__("own label")}" ${ro}>
						${this.ro ? "" : `<button class="bd-x bd-fup" data-i="${i}">↑</button><button class="bd-x bd-fdel" data-i="${i}">✕</button>`}</div>`).join("")}
					${this.ro ? "" : `<select class="form-control bd-fadd"><option value="">+ ${__("field")}</option>${this.meta.fields.map((f) => `<option value="${f.key}">${e(f.label)}</option>`).join("")}</select>`}</div>`;
				return "";
			});
			$o.html(`<h4>${e(meta.label)}</h4><div class="bd-help">${e(meta.description)}</div>${parts.join("")}
				${this.ro ? "" : `<div style="display:flex;gap:6px;margin-top:6px"><button class="ub-btn bd-bup">↑ ${__("Up")}</button><button class="ub-btn bd-bdown">↓ ${__("Down")}</button>
				<button class="ub-btn bd-bcopy">${__("Duplicate")}</button><button class="ub-btn bd-bdel">${__("Delete")}</button></div>`}`);
			const set = (k, val) => this.change(() => (o[k] = val));
			$o.find("[data-k]").not(".bd-upload, .bd-clear").on("change", (ev) => {
				const k = $(ev.target).attr("data-k");
				const op = meta.options.find((x) => x.key === k);
				set(k, op.type === "check" ? (ev.target.checked ? 1 : 0) : op.type === "int" ? Math.max(op.min, Math.min(op.max, parseInt(ev.target.value, 10) || op.min)) : ev.target.value);
			});
			$o.find("[data-m]").on("change", (ev) => {
				const k = $(ev.target).attr("data-m");
				set(k, $o.find(`[data-m="${k}"]:checked`).map((i, x) => x.value).get());
			});
			$o.find(".bd-flabel").on("change", (ev) => this.change(() => (o.fields[+$(ev.target).attr("data-i")].label = ev.target.value)));
			$o.find(".bd-fup").on("click", (ev) => this.change(() => { const i = +$(ev.currentTarget).attr("data-i"); if (i > 0) [o.fields[i - 1], o.fields[i]] = [o.fields[i], o.fields[i - 1]]; }));
			$o.find(".bd-fdel").on("click", (ev) => this.change(() => o.fields.splice(+$(ev.currentTarget).attr("data-i"), 1)));
			$o.find(".bd-fadd").on("change", (ev) => ev.target.value && this.change(() => o.fields.push({ key: ev.target.value, label: "" })));
			$o.find(".bd-upload").on("click", (ev) => {
				const k = $(ev.currentTarget).attr("data-k");
				new frappe.ui.FileUploader({ allow_multiple: false, restrictions: { allowed_file_types: ["image/*"] }, on_success: (file) => set(k, file.file_url) });
			});
			$o.find(".bd-clear").on("click", (ev) => set($(ev.currentTarget).attr("data-k"), ""));
			const blocks = L.sections[si].rows[ri].cells[ci].blocks;
			$o.find(".bd-bup, .bd-bdown").on("click", (ev) => this.change(() => {
				const d = $(ev.currentTarget).hasClass("bd-bup") ? -1 : 1, j = bi + d;
				if (j < 0 || j >= blocks.length) return;
				[blocks[bi], blocks[j]] = [blocks[j], blocks[bi]];
				this.sel = { kind: "block", path: [si, ri, ci, j] };
			}));
			$o.find(".bd-bcopy").on("click", () => this.change(() => { blocks.splice(bi + 1, 0, clone(block)); this.sel = { kind: "block", path: [si, ri, ci, bi + 1] }; }));
			$o.find(".bd-bdel").on("click", () => this.change(() => { blocks.splice(bi, 1); this.sel = null; }));
		}

		// ------------------------------------------------------------ page settings and where the template is used
		page_dialog() {
			const t = this.t;
			const d = new frappe.ui.Dialog({
				title: __("Page and use"),
				fields: [
					{ fieldname: "society", label: __("Society"), fieldtype: "Select", options: this.meta.societies.map((s) => ({ value: s.name, label: s.society_name })), default: t.society, reqd: 1, read_only: t.name ? 1 : 0 },
					{ fieldname: "utility", label: __("Utility"), fieldtype: "Link", options: "Utility Type", default: t.utility, description: __("Empty: every utility.") },
					{ fieldname: "consumer_category", label: __("Consumer Category"), fieldtype: "Link", options: "Consumer Category", default: t.consumer_category, description: __("Empty: every category.") },
					{ fieldname: "effective_from", label: __("Use for bills issued from"), fieldtype: "Date", default: t.effective_from, reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldname: "paper_size", label: __("Paper size"), fieldtype: "Select", options: this.meta.paper_sizes.join("\n"), default: t.paper_size },
					{ fieldname: "pages_per_bill", label: __("Pages per bill"), fieldtype: "Select", options: "1\n2", default: String(t.pages_per_bill || 1),
						description: __("1: every bill must fit on one page. Choose 2 only if the society wants more printed.") },
					{ fieldname: "font_family", label: __("Font"), fieldtype: "Select", options: this.meta.fonts.join("\n"), default: t.font_family },
					{ fieldname: "font_size", label: __("Text size (pt)"), fieldtype: "Float", default: t.font_size },
					{ fieldname: "margin_top", label: __("Top and bottom margin (mm)"), fieldtype: "Float", default: t.margin_top },
					{ fieldname: "margin_side", label: __("Left and right margin (mm)"), fieldtype: "Float", default: t.margin_side },
					{ fieldname: "accent_colour", label: __("Accent colour"), fieldtype: "Color", default: t.accent_colour, description: __("Empty: the society's brand colour.") },
				],
				primary_action_label: __("Apply"),
				primary_action: (v) => {
					Object.assign(t, v);
					this.society = t.society;
					this.dirty = true;
					d.hide();
					this.refresh();
				},
			});
			d.show();
		}

		page() {
			const t = this.t;
			return { paper_size: t.paper_size, font_family: t.font_family, font_size: t.font_size, margin_top: t.margin_top, margin_side: t.margin_side, accent_colour: t.accent_colour,
				pages_per_bill: String(t.pages_per_bill || 1) };
		}

		// ------------------------------------------------------------ preview
		refresh() {
			clearTimeout(this.timer);
			this.timer = setTimeout(() => this.render_preview(), 350);
		}

		async render_preview() {
			const seq = (this.seq = (this.seq || 0) + 1);
			let html;
			try {
				html = await frappe.xcall(M + "preview", { society: this.society, layout: JSON.stringify(this.layout), page: JSON.stringify(this.page()), bill: this.bill || null, designer: this.ro ? 0 : 1 });
			} catch (err) {
				return;
			}
			if (seq !== this.seq) return;
			const $f = this.$root.find(".bd-frame");
			const frame = $f[0];
			frame.onload = () => {
				this.mark_pages(frame);
				this.zoom();
				if (!this.ro) this.attach(frame);
			};
			frame.srcdoc = html;
		}

		// dashed lines where each printed page ends, and a warning when the bill runs past one page
		mark_pages(frame) {
			const doc = frame.contentDocument;
			if (!doc) return;
			const sheet = doc.querySelector(".sheet");
			const paper = this.meta.paper_mm[this.t.paper_size] || [210, 297];
			const content = (paper[1] - 2 * (this.t.margin_top || 0)) * MM;
			const body = sheet.querySelector(".ubd");
			const h = body ? body.getBoundingClientRect().height : 0;
			const pages = Math.max(1, Math.ceil((h - 2) / content));
			$(sheet).css({ position: "relative" });
			for (let i = 1; i < pages; i++) {
				$(`<div style="position:absolute;left:0;right:0;top:${(this.t.margin_top || 0) * MM + i * content}px;border-top:2px dashed #b42318;"><span style="position:absolute;right:4px;top:-16px;font:11px sans-serif;color:#b42318;background:#fff;padding:0 4px">${__("page {0} ends", [i])}</span></div>`).appendTo(sheet);
			}
			const fill = h / content, limit = Number(this.t.pages_per_bill || 1);
			this.$root.find(".bd-fit").removeClass("ok over").addClass(pages > limit ? "over" : "ok")
				.text(pages > limit
					? __("Runs to {0} pages; this template allows {1}. Shorten it or use a smaller text size.", [pages, limit])
					: limit === 1 ? (fill > 0.93 ? __("Fits on one page, only just: use Check fit") : __("Fits on one page"))
					: __("{0} of {1} pages", [pages, limit]));
			frame.style.height = `${doc.documentElement.scrollHeight + 10}px`;
		}

		zoom() {
			const frame = this.$root.find(".bd-frame")[0];
			if (!frame || !frame.contentDocument) return;
			const wrapW = this.$root.find(".bd-frame-wrap").width() || 800;
			const sheetW = ((this.meta.paper_mm[this.t.paper_size] || [210])[0] + 12) * MM;
			const z = this.$root.find(".bd-zoom").val();
			const scale = z === "fit" ? Math.min(1, wrapW / sheetW) : parseFloat(z);
			frame.style.width = `${Math.max(wrapW / scale, sheetW)}px`;
			frame.style.transform = `scale(${scale})`;
			this.scale = scale;
			const h = frame.contentDocument.documentElement.scrollHeight + 10;
			frame.style.height = `${h}px`;
			frame.parentElement.style.minHeight = "";
			$(frame).css("margin-bottom", `${-(h * (1 - scale))}px`);
		}

		async fit_check() {
			frappe.show_alert({ message: __("Printing the bill and a long bill…"), indicator: "blue" });
			const r = await frappe.xcall(M + "fit_check", { society: this.society, layout: JSON.stringify(this.layout), page: JSON.stringify(this.page()), bill: this.bill || null });
			const ok = r.long <= r.limit && r.pages <= r.limit;
			frappe.msgprint({
				title: ok ? __("Fits") : __("Doesn't fit"),
				indicator: ok ? "green" : "red",
				message: `<p>${__("Pages allowed per bill: {0}", [r.limit])}</p>
					<p>${__("This bill: {0} page(s)", [r.pages])}<br>${__("A long bill (8 charges, an installment, a full history, a long name): {0} page(s)", [r.long])}</p>
					${ok ? "" : `<p>${__("A template must fit the long bill before it can be submitted for approval. Shorten it, use a smaller text size, or allow 2 pages under Page and use.")}</p>`}`,
			});
		}

		async pdf() {
			const args = { society: this.society, layout: JSON.stringify(this.layout), page: JSON.stringify(this.page()), bill: this.bill || "" };
			const form = $(`<form method="POST" target="_blank" action="/api/method/${M}preview_pdf"></form>`);
			Object.entries(Object.assign(args, { csrf_token: frappe.csrf_token })).forEach(([k, v]) => $('<input type="hidden">').attr({ name: k, value: v }).appendTo(form));
			form.appendTo("body").submit().remove();
		}

		// ------------------------------------------------------------ editing on the bill itself
		// The preview is tagged by the server (designer mode): sections [data-s], rows [data-r], columns [data-c], blocks [data-p].
		// Boxes keep their flow: a drop puts a block into a column, a new column or a new row, never at a fixed position, so a
		// bill with more charge lines or history months still prints without overlap.
		attach(frame) {
			const doc = frame.contentDocument;
			if (!doc || !doc.body) return;
			this.frame = frame;
			this.idoc = doc;
			$(`<style>
				.bd-sel { outline: 2px solid #1f5f8b !important; outline-offset: 1px; }
				td.bd-sel { outline-offset: -2px; }
				.bd-ghost { min-height: 6mm; border: 1px dashed #c9d1d8; color: #8a959e; font: 10px sans-serif; padding: 1mm 2mm; }
				.bd-ind { position: absolute; background: #1f5f8b; z-index: 50; pointer-events: none; border-radius: 2px; }
				.bd-ind.box { background: rgba(31,95,139,.12); border: 2px solid #1f5f8b; }
				.bd-h { position: absolute; z-index: 40; }
				.bd-h.col { cursor: col-resize; }
				.bd-h.col:hover, .bd-h.col.on { background: rgba(31,95,139,.35); }
				.bd-h.size { width: 9px; height: 9px; background: #1f5f8b; border: 1.5px solid #fff; border-radius: 2px; cursor: nwse-resize; }
				.bd-h.col.grip { width: 10px; height: 18px; background: #fff; border: 2px solid #1f5f8b; border-radius: 3px; }
				.bd-h.col.grip:hover, .bd-h.col.grip.on { background: #1f5f8b; }
				.bd-h.minh { width: 18px; height: 10px; background: #fff; border: 2px solid #1f5f8b; border-radius: 3px; cursor: ns-resize; }
				.bd-h.minh:hover { background: #1f5f8b; }
				[data-edit] { cursor: text; }
				[data-edit]:hover { background: rgba(31,95,139,.08); box-shadow: inset 0 -1px 0 #1f5f8b; }
				.bd-edit { position: absolute; z-index: 70; font: inherit; border: 2px solid #1f5f8b; border-radius: 3px; padding: 1px 4px; background: #fff; color: #1d2329; box-shadow: 0 2px 8px rgba(0,0,0,.2); }
				.bd-tip { position: absolute; z-index: 60; background: #1d2329; color: #fff; font: 11px sans-serif; padding: 2px 6px; border-radius: 4px; pointer-events: none; }
			</style>`).appendTo(doc.head);
			// blocks with nothing to show on this bill (no photo, no notices…) still get a box to click and drag
			doc.querySelectorAll("[data-p]").forEach((el) => {
				if (el.getBoundingClientRect().height < 4) {
					el.classList.add("bd-ghost");
					el.textContent = __("{0} (nothing to show on this bill)", [(this.block_meta(el.getAttribute("data-t")) || {}).label || ""]);
				}
			});
			doc.addEventListener("mousedown", (ev) => this.on_frame_down(ev));
			doc.addEventListener("dblclick", (ev) => this.edit_text(ev));
			doc.addEventListener("keydown", (ev) => this.on_key(ev));
			this.paint_selection();
		}

		// double-click a title, heading, label, copy label, caption or free text and type over it; the layout keeps the words as
		// written, placeholders such as {due_date} included (they are shown unfilled while editing)
		edit_text(ev) {
			const el = ev.target.closest("[data-edit]");
			if (!el || this.ro) return;
			ev.preventDefault();
			const doc = this.idoc, win = doc.defaultView;
			const key = el.getAttribute("data-edit");
			const blockEl = el.closest("[data-p]");
			const path = blockEl ? blockEl.getAttribute("data-p").split(",").map(Number) : null;
			const sec = this.layout.sections[path ? path[0] : 0];
			const block = path ? sec.rows[path[1]].cells[path[2]].blocks[path[3]] : null;
			let get, set;
			if (key === "@copy_label") {
				get = () => sec.copy_label || "";
				set = (v) => (sec.copy_label = v);
			} else if (key.startsWith("field:")) {
				const f = block.options.fields[Number(key.slice(6))];
				const fallback = (this.meta.fields.find((x) => x.key === f.key) || {}).label || "";
				get = () => f.label || fallback;
				set = (v) => (f.label = v.trim() === fallback ? "" : v);
			} else {
				get = () => block.options[key] || "";
				set = (v) => (block.options[key] = v);
			}
			const multi = key === "text";
			const r = el.getBoundingClientRect();
			const $ed = $(multi ? "<textarea class='bd-edit'></textarea>" : "<input class='bd-edit'>").val(get())
				.css({ left: r.left + win.scrollX - 3, top: r.top + win.scrollY - 3, width: Math.max(r.width + 6, 120), height: multi ? Math.max(r.height + 30, 60) : r.height + 8,
					fontSize: win.getComputedStyle(el).fontSize, fontWeight: win.getComputedStyle(el).fontWeight })
				.appendTo(doc.body);
			$ed.trigger("focus").trigger("select");
			let done = false;
			const finish = (save) => {
				if (done) return;
				done = true;
				const v = $ed.val();
				$ed.remove();
				if (save && v !== get()) this.change(() => set(v));
			};
			$ed.on("keydown", (e) => {
				if (e.key === "Escape") finish(false);
				else if (e.key === "Enter" && (!multi || e.ctrlKey)) { e.preventDefault(); finish(true); }
				e.stopPropagation();
			});
			$ed.on("blur", () => finish(true));
			$ed.on("mousedown dblclick", (e) => e.stopPropagation());
		}

		on_key(ev) {
			if (this.ro || !this.sel || this.sel.kind !== "block" || !["Delete", "Backspace"].includes(ev.key)) return;
			if ($(ev.target).is("input, textarea, select, [contenteditable]")) return;
			ev.preventDefault();
			const [si, ri, ci, bi] = this.sel.path;
			this.change(() => {
				this.layout.sections[si].rows[ri].cells[ci].blocks.splice(bi, 1);
				this.sel = null;
			});
		}

		to_parent(x, y) {
			const r = this.frame.getBoundingClientRect(), s = this.scale || 1;
			return [r.left + x * s, r.top + y * s];
		}

		to_frame(cx, cy) {
			const r = this.frame.getBoundingClientRect(), s = this.scale || 1;
			return [(cx - r.left) / s, (cy - r.top) / s];
		}

		on_frame_down(ev) {
			if (ev.button !== 0) return;
			const doc = this.idoc;
			const h = ev.target.closest(".bd-h");
			if (h) {
				ev.preventDefault();
				this.start_resize(h, ev);
				return;
			}
			const b = ev.target.closest("[data-p]");
			const cell = ev.target.closest("td[data-c]");
			const zone = ev.target.closest(".dz-empty[data-z]");
			if (!b) {
				if (cell) this.select("cell", cell.getAttribute("data-c").split(",").map(Number));
				else if (zone) this.select("section", [Number(zone.getAttribute("data-z"))]);
				return;
			}
			ev.preventDefault();
			const path = b.getAttribute("data-p").split(",").map(Number);
			const sx = ev.clientX, sy = ev.clientY;
			const move = (e) => {
				if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) < 5) return;
				cleanup();
				const [px, py] = this.to_parent(e.clientX, e.clientY);
				this.start_drag({ kind: "move", path, label: (this.block_meta(b.getAttribute("data-t")) || {}).label }, px, py);
			};
			const up = () => {
				cleanup();
				this.select("block", path);
			};
			const cleanup = () => {
				doc.removeEventListener("mousemove", move);
				doc.removeEventListener("mouseup", up);
			};
			doc.addEventListener("mousemove", move);
			doc.addEventListener("mouseup", up);
		}

		// a transparent cover over the whole screen takes the mouse while dragging, so moves over the preview and the panels
		// are seen in one place; positions are turned into the preview's own coordinates
		start_drag(info, cx, cy) {
			const $cover = $('<div class="bd-drag-cover"></div>').appendTo("body");
			const $ghost = $(`<div class="bd-drag-ghost">${e(info.label || "")}</div>`).appendTo("body");
			$(this.idoc).find(".bd-h").hide();
			let target = null;
			const place = (x, y) => {
				$ghost.css({ left: x + 12, top: y + 10 });
				const [fx, fy] = this.to_frame(x, y);
				target = this.idoc ? this.target_at(fx, fy) : null;
				this.show_indicator(target);
			};
			place(cx, cy);
			$cover.on("mousemove", (ev) => place(ev.clientX, ev.clientY));
			$cover.on("mouseup", () => {
				$cover.remove();
				$ghost.remove();
				this.show_indicator(null);
				$(this.idoc).find(".bd-h").show();
				if (target) this.drop(info, target);
			});
		}

		target_at(x, y) {
			const doc = this.idoc;
			const fr = this.frame.getBoundingClientRect(), s = this.scale || 1;
			if (x < 0 || y < 0 || x > fr.width / s || y > fr.height / s) return null;
			const el = doc.elementFromPoint(x, y);
			if (!el) return null;
			const R = (n) => n.getBoundingClientRect();
			const zone = el.closest(".dz-empty[data-z]");
			if (zone) return { zone: "newrow", si: +zone.getAttribute("data-z"), index: 0, box: R(zone) };
			const row = el.closest("table[data-r]");
			if (row) {
				const [si, ri] = row.getAttribute("data-r").split(",").map(Number);
				const rr = R(row);
				if (y < rr.top + 5) return { zone: "newrow", si, index: ri, line: [rr.left, rr.top - 2, rr.width, 3] };
				if (y > rr.bottom - 5) return { zone: "newrow", si, index: ri + 1, line: [rr.left, rr.bottom - 1, rr.width, 3] };
				const free = el.closest("td.dz-free");
				const cells = [...row.querySelectorAll(":scope > tbody > tr > td[data-c], :scope > tr > td[data-c]")];
				if (free) return { zone: "free", si, ri, box: R(free) };
				const cell = el.closest("td[data-c]");
				if (cell) {
					const ci = +cell.getAttribute("data-c").split(",")[2];
					const cr = R(cell);
					if (x < cr.left + 10) return { zone: "col", si, ri, ci, after: false, line: [cr.left - 2, cr.top, 3, cr.height] };
					if (x > cr.right - 12) return { zone: "col", si, ri, ci, after: true, line: [cr.right - 3, cr.top, 3, cr.height] };
					const blocks = [...cell.querySelectorAll(":scope > [data-p]")];
					let index = blocks.length, line = null;
					for (let i = 0; i < blocks.length; i++) {
						const br = R(blocks[i]);
						if (y < br.top + br.height / 2) {
							index = i;
							line = [br.left, br.top - 2, br.width, 3];
							break;
						}
					}
					if (!line) {
						const last = blocks.length ? R(blocks[blocks.length - 1]) : null;
						line = last ? [last.left, last.bottom, last.width, 3] : [cr.left, cr.top, cr.width, 3];
					}
					return { zone: "cell", si, ri, ci, index, line, cells: cells.length };
				}
			}
			// between rows, or below the last one: a new row at that place in the section under the mouse (or the last section)
			const secs = [...doc.querySelectorAll(".sec[data-s]")];
			if (!secs.length) return null;
			let sec = secs.find((sEl) => { const r = R(sEl); return y >= r.top && y <= r.bottom; }) || secs[secs.length - 1];
			const si = +sec.getAttribute("data-s");
			const rows = [...sec.querySelectorAll("table[data-r]")];
			let index = rows.length, at = rows.length ? R(rows[rows.length - 1]).bottom : R(sec).top;
			for (let i = 0; i < rows.length; i++) {
				const r = R(rows[i]);
				if (y < r.top + r.height / 2) {
					index = i;
					at = r.top - 2;
					break;
				}
			}
			const sr = R(sec);
			return { zone: "newrow", si, index, line: [sr.left, at, sr.width, 3] };
		}

		show_indicator(t) {
			if (!this.idoc) return;
			$(this.idoc).find(".bd-ind").remove();
			if (!t) return;
			const win = this.idoc.defaultView, sx = win.scrollX, sy = win.scrollY;
			if (t.box) {
				$('<div class="bd-ind box"></div>').css({ left: t.box.left + sx, top: t.box.top + sy, width: t.box.width, height: t.box.height }).appendTo(this.idoc.body);
			} else if (t.line) {
				const [l, tp, w, h] = t.line;
				$('<div class="bd-ind"></div>').css({ left: l + sx, top: tp + sy, width: w, height: h }).appendTo(this.idoc.body);
			}
		}

		path_of(block) {
			const L = this.layout;
			for (let si = 0; si < L.sections.length; si++)
				for (let ri = 0; ri < L.sections[si].rows.length; ri++)
					for (let ci = 0; ci < L.sections[si].rows[ri].cells.length; ci++) {
						const bi = L.sections[si].rows[ri].cells[ci].blocks.indexOf(block);
						if (bi >= 0) return [si, ri, ci, bi];
					}
			return null;
		}

		drop(info, t) {
			const L = this.layout;
			const sec = L.sections[t.si];
			const row = t.ri != null ? sec.rows[t.ri] : null;
			const cell = row && t.ci != null ? row.cells[t.ci] : null;
			if (t.zone === "col" && row.cells.length >= 6) {
				frappe.show_alert({ message: __("A row can have at most 6 columns."), indicator: "orange" });
				return;
			}
			this.change(() => {
				let block, src = null;
				if (info.kind === "new") {
					block = { type: info.type, options: this.default_options(info.type) };
				} else {
					const [a, b, c, d] = info.path;
					src = { sec: L.sections[a], row: L.sections[a].rows[b], cell: L.sections[a].rows[b].cells[c] };
					block = src.cell.blocks[d];
				}
				// the place is held by reference, so taking the block out first can't shift it
				const before = t.zone === "cell" && t.index < cell.blocks.length ? cell.blocks[t.index] : null;
				const rowAt = t.zone === "newrow" ? sec.rows[t.index] || null : null;
				if (before === block) return;
				if (src) src.cell.blocks.splice(src.cell.blocks.indexOf(block), 1);
				if (t.zone === "cell") {
					cell.blocks.splice(before ? cell.blocks.indexOf(before) : cell.blocks.length, 0, block);
				} else if (t.zone === "col") {
					const half = Math.max(5, Math.floor(cell.width / 2));
					cell.width = Math.max(5, cell.width - half);
					row.cells.splice(row.cells.indexOf(cell) + (t.after ? 1 : 0), 0, { width: half, blocks: [block] });
				} else if (t.zone === "free") {
					const used = row.cells.reduce((n, c) => n + c.width, 0);
					row.cells.push({ width: Math.max(5, 100 - used), blocks: [block] });
				} else {
					sec.rows.splice(rowAt ? sec.rows.indexOf(rowAt) : sec.rows.length, 0, { cells: [{ width: 100, blocks: [block] }] });
				}
				// a column the block left empty goes, its width to its neighbour; an empty row goes too
				if (src && !src.cell.blocks.length) {
					const i = src.row.cells.indexOf(src.cell);
					if (i >= 0) {
						src.row.cells.splice(i, 1);
						if (src.row.cells.length) src.row.cells[Math.max(0, i - 1)].width += src.cell.width;
						else src.sec.rows.splice(src.sec.rows.indexOf(src.row), 1);
					}
				}
				const path = this.path_of(block);
				this.sel = path ? { kind: "block", path } : null;
			});
		}

		paint_selection() {
			const doc = this.idoc;
			if (!doc) return;
			$(doc).find(".bd-sel").removeClass("bd-sel");
			const s = this.sel;
			if (s && s.kind === "block") $(doc).find(`[data-p="${s.path.join(",")}"]`).addClass("bd-sel");
			if (s && s.kind === "cell") $(doc).find(`td[data-c="${s.path.join(",")}"]`).addClass("bd-sel");
			if (s && s.kind === "section") $(doc).find(`.sec[data-s="${s.path[0]}"]`).addClass("bd-sel");
			this.place_handles();
		}

		// handles: the edge between two columns (and the right edge of the last one) resizes them; a selected stamp, spacer,
		// image, photo or QR code gets a corner handle for its size
		place_handles() {
			const doc = this.idoc;
			$(doc).find(".bd-h").remove();
			const win = doc.defaultView, sx = win.scrollX, sy = win.scrollY;
			doc.querySelectorAll("table[data-r]").forEach((row) => {
				const cells = [...row.querySelectorAll(":scope > tbody > tr > td[data-c], :scope > tr > td[data-c]")];
				const rr = row.getBoundingClientRect();
				cells.forEach((c, i) => {
					const cr = c.getBoundingClientRect();
					const last = i === cells.length - 1;
					$('<div class="bd-h col"></div>').attr({ "data-r": row.getAttribute("data-r"), "data-i": i, "data-end": last ? 1 : 0, title: __("Drag to resize") })
						.css({ left: cr.right - 3 + sx, top: rr.top + sy, width: 6, height: rr.height }).appendTo(doc.body);
				});
			});
			const s = this.sel;
			if (s && s.kind === "block") {
				const el = doc.querySelector(`[data-p="${s.path.join(",")}"]`);
				const SIZE = { spacer: "height", stamp: "height", image: "width", meter_photo: "width", qr: "size" };
				const type = el && el.getAttribute("data-t");
				if (!el) return;
				const br = el.getBoundingClientRect();
				// side grips: the box's width is its column's, so they move the column's edges (the neighbour takes up the rest)
				const cell = el.closest("td[data-c]");
				const [si, ri, ci] = cell.getAttribute("data-c").split(",").map(Number);
				const n = this.layout.sections[si].rows[ri].cells.length;
				const grip = (side, i, end) => $('<div class="bd-h col grip"></div>')
					.attr({ "data-r": `${si},${ri}`, "data-i": i, "data-end": end, title: __("Drag to change the width") })
					.css({ left: (side === "left" ? br.left : br.right) - 5 + sx, top: br.top + br.height / 2 - 9 + sy }).appendTo(doc.body);
				grip("right", ci, ci === n - 1 ? 1 : 0);
				if (ci > 0) grip("left", ci - 1, 0);
				// bottom grip: a minimum height for the box
				$('<div class="bd-h minh"></div>').attr({ "data-key": "min_height", title: __("Drag to change the height") })
					.css({ left: br.left + br.width / 2 - 9 + sx, top: br.bottom - 5 + sy }).appendTo(doc.body);
				if (SIZE[type]) {
					const r = (el.querySelector("img, .stamp") || el).getBoundingClientRect();
					$('<div class="bd-h size"></div>').attr({ "data-key": SIZE[type], title: __("Drag to resize") })
						.css({ left: r.right - 5 + sx, top: r.bottom - 5 + sy }).appendTo(doc.body);
				}
			}
		}

		start_resize(h, ev) {
			const doc = this.idoc;
			const startX = ev.clientX, startY = ev.clientY;
			const $tip = $('<div class="bd-tip"></div>').appendTo(doc.body);
			const win = doc.defaultView;
			let commit = null;
			if (h.classList.contains("col")) {
				const [si, ri] = h.getAttribute("data-r").split(",").map(Number);
				const i = +h.getAttribute("data-i"), end = h.getAttribute("data-end") === "1";
				const rowEl = doc.querySelector(`table[data-r="${si},${ri}"]`);
				const tds = [...rowEl.querySelectorAll(":scope > tbody > tr > td, :scope > tr > td")];
				const cells = this.layout.sections[si].rows[ri].cells;
				const W = rowEl.getBoundingClientRect().width;
				const w0 = cells.map((c) => c.width);
				const sum = w0.reduce((a, b) => a + b, 0);
				h.classList.add("on");
				const move = (e) => {
					const d = Math.round(((e.clientX - startX) / W) * 100);
					const w = w0.slice();
					if (end) w[i] = Math.max(5, Math.min(100 - (sum - w0[i]), w0[i] + d));
					else {
						w[i] = Math.max(5, Math.min(w0[i] + w0[i + 1] - 5, w0[i] + d));
						w[i + 1] = w0[i] + w0[i + 1] - w[i];
					}
					w.forEach((x, k) => (tds[k].style.width = `${x}%`));
					const used = w.reduce((a, b) => a + b, 0);
					if (tds.length > w.length) tds[tds.length - 1].style.width = `${100 - used}%`;
					$tip.text(w.map((x) => `${x}%`).join(" | ")).css({ left: e.clientX + 12 + win.scrollX, top: e.clientY + 12 + win.scrollY });
					commit = () => w.forEach((x, k) => (cells[k].width = x));
				};
				this.track(doc, move, () => { $tip.remove(); if (commit) this.change(commit); else h.classList.remove("on"); });
			} else {
				const key = h.getAttribute("data-key");
				const [si, ri, ci, bi] = this.sel.path;
				const block = this.layout.sections[si].rows[ri].cells[ci].blocks[bi];
				const op = this.block_meta(block.type).options.find((o) => o.key === key);
				const el = doc.querySelector(`[data-p="${this.sel.path.join(",")}"]`);
				// a minimum height starts from the box's height now, so the first pixel of dragging already shows
				const v0 = key === "min_height" ? Number(block.options[key]) || Math.round(el.getBoundingClientRect().height / MM) : Number(block.options[key]) || op.default;
				const move = (e) => {
					const px = key === "height" || key === "min_height" ? e.clientY - startY : e.clientX - startX;
					const v = Math.max(op.min, Math.min(op.max, Math.round(v0 + px / MM)));
					$tip.text(`${v} mm`).css({ left: e.clientX + 12 + win.scrollX, top: e.clientY + 12 + win.scrollY });
					commit = () => (block.options[key] = v);
				};
				this.track(doc, move, () => { $tip.remove(); if (commit) this.change(commit); });
			}
		}

		track(doc, move, done) {
			const up = () => {
				doc.removeEventListener("mousemove", move);
				doc.removeEventListener("mouseup", up);
				done();
			};
			doc.addEventListener("mousemove", move);
			doc.addEventListener("mouseup", up);
		}

		// ------------------------------------------------------------ save, import
		async save() {
			const t = this.t;
			t.template_name = this.$root.find(".bd-name").val().trim();
			if (!t.template_name) {
				frappe.msgprint(__("Give the template a name."));
				return;
			}
			const values = Object.assign({ name: t.name, template_name: t.template_name, society: t.society, utility: t.utility, consumer_category: t.consumer_category,
				effective_from: t.effective_from, description: t.description, layout: this.layout }, this.page());
			const name = await frappe.xcall(M + "save_template", { values: JSON.stringify(values) });
			const fresh = !t.name;
			t.name = name;
			this.dirty = false;
			frappe.show_alert({ message: __("Saved {0}. Submit it for approval from Approval when it is ready.", [name]), indicator: "green" });
			if (fresh) this.load(name);
			else this.render_state();
		}

		import_file() {
			const input = $('<input type="file" accept=".json,application/json" style="display:none">').appendTo("body");
			input.on("change", () => {
				const file = input[0].files[0];
				if (!file) return;
				const reader = new FileReader();
				reader.onload = async () => {
					const name = await frappe.xcall(M + "import_template", { data: reader.result, society: this.society });
					frappe.show_alert({ message: __("Imported as draft {0}.", [name]), indicator: "green" });
					this.load(name);
				};
				reader.readAsText(file);
				input.remove();
			});
			input.trigger("click");
		}
	}

	window.UtilityBillingBillDesigner = BillDesigner;
})();
