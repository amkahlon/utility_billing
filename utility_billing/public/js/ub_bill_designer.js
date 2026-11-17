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
				effective_from: frappe.datetime.get_today(), paper_size: "A4", font_family: "Helvetica / Arial", font_size: 9, margin_top: 6, margin_side: 8,
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
			$(window).off("resize.ubbd").on("resize.ubbd", () => this.zoom());
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
			const $t = this.$root.find(".bd-tree").html(`<h4>${__("Layout")}</h4>
				<div class="bd-help">${ro ? "" : __("Click a block to set its options. Drag blocks between columns.")}</div>${html}
				${ro ? "" : `<button class="ub-btn bd-sec-add">+ ${__("section (e.g. a tear-off copy)")}</button>`}`);
			const P = (el, a = "data-p") => $(el).attr(a).split(",").map(Number);
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
					<div class="bd-opt"><label class="bd-check"><input type="checkbox" data-k="new_page" ${sec.new_page ? "checked" : ""} ${ro}> ${__("Start on a new page")}</label></div>`);
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
			return { paper_size: t.paper_size, font_family: t.font_family, font_size: t.font_size, margin_top: t.margin_top, margin_side: t.margin_side, accent_colour: t.accent_colour };
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
				html = await frappe.xcall(M + "preview", { society: this.society, layout: JSON.stringify(this.layout), page: JSON.stringify(this.page()), bill: this.bill || null });
			} catch (err) {
				return;
			}
			if (seq !== this.seq) return;
			const $f = this.$root.find(".bd-frame");
			const frame = $f[0];
			frame.onload = () => {
				this.mark_pages(frame);
				this.zoom();
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
			const fill = h / content;
			this.$root.find(".bd-fit").removeClass("ok over").addClass(pages > 1 ? "over" : "ok")
				.text(pages > 1 ? __("Runs to {0} pages: shorten it or use a smaller text size", [pages]) : fill > 0.93 ? __("Fits on one page, only just: check the PDF") : __("Fits on one page"));
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
			const h = frame.contentDocument.documentElement.scrollHeight + 10;
			frame.style.height = `${h}px`;
			frame.parentElement.style.minHeight = "";
			$(frame).css("margin-bottom", `${-(h * (1 - scale))}px`);
		}

		async pdf() {
			const args = { society: this.society, layout: JSON.stringify(this.layout), page: JSON.stringify(this.page()), bill: this.bill || "" };
			const form = $(`<form method="POST" target="_blank" action="/api/method/${M}preview_pdf"></form>`);
			Object.entries(Object.assign(args, { csrf_token: frappe.csrf_token })).forEach(([k, v]) => $('<input type="hidden">').attr({ name: k, value: v }).appendTo(form));
			form.appendTo("body").submit().remove();
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
