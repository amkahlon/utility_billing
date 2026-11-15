// Ask Your Data (AI analytics 2, increment 24): a question in plain language, answered from the society's own reports.
// Also explains a theft-suspicion score (AI analytics 3). Everything is logged in AI Log; nothing runs unless AI Settings allow it.
frappe.pages["ub-ask"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Ask Your Data"), single_column: true });
	$(wrapper).addClass("ub-page");
	const $root = $('<div class="ub-root"></div>').appendTo($(wrapper).find(".layout-main-section"));
	const e = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	let society = null;
	frappe.db.get_list("Society", { fields: ["name", "society_name"], limit: 200, order_by: "society_name" }).then((socs) => {
		society = socs.length ? socs[0].name : null;
		$root.html(`
			<div class="ub-head"><div><div class="ub-hello">${__("AI analytics")}</div><h1>${__("Ask Your Data")}</h1>
				<p>${__("Ask in plain words. The answer comes only from your society's reports, run with your own rights, and names the reports it used.")}</p></div>
				<div class="ub-tools"><select class="ub-select ub-soc">${socs.map((s) => `<option value="${e(s.name)}">${e(s.society_name)}</option>`).join("")}</select></div></div>
			<section class="ub-card"><div class="ub-ask-box">
				<input class="form-control ub-q" placeholder="${__("For example: which blocks collected least last month?")}">
				<button class="ub-btn ub-btn-primary ub-go">${__("Ask")}</button></div>
				<div class="ub-out" style="margin-top:14px"></div></section>
			<section class="ub-card"><div class="ub-cardhead"><h2>${__("Explain a theft-suspicion score")}</h2></div>
				<div class="ub-ask-box"><input class="form-control ub-acc" placeholder="${__("Account number")}">
				<button class="ub-btn ub-explain">${__("Explain")}</button></div><div class="ub-exp" style="margin-top:12px"></div>
				<div class="ub-kpi-note">${__("Scores are in the Theft Suspicion report. A score is a reason to inspect, not proof.")}</div></section>`);
		$root.on("change", ".ub-soc", (ev) => (society = ev.target.value));
		const ask = () => {
			const q = $root.find(".ub-q").val();
			const out = $root.find(".ub-out").html(`<div class="ub-kpi-note">${__("Working it out…")}</div>`);
			frappe.xcall("utility_billing.utility_billing.ai.ask", { society, question: q })
				.then((r) => {
					const used = r.reports.length
						? `<div class="ub-kpi-note" style="margin-top:10px">${__("From")}: ${r.reports.map((u) => {
							const f = Object.entries(u.filters).filter(([k]) => k !== "society").map(([k, v]) => `${k}=${v}`).join(", ");
							return `<a href="/app/query-report/${encodeURIComponent(u.report)}">${e(u.report)}</a>${f ? " (" + e(f) + ")" : ""}`;
						}).join("; ")}</div>`
						: "";
					out.html(`<div class="ub-answer">${e(r.answer)}</div>${used}`);
				})
				.catch(() => out.html(""));
		};
		$root.on("click", ".ub-go", ask);
		$root.on("keydown", ".ub-q", (ev) => ev.key === "Enter" && ask());
		$root.on("click", ".ub-explain", () => {
			const out = $root.find(".ub-exp").html(`<div class="ub-kpi-note">${__("Working it out…")}</div>`);
			frappe.xcall("utility_billing.utility_billing.ai.explain_account", { society, connection: $root.find(".ub-acc").val().trim() })
				.then((r) => out.html(`<div class="ub-answer">${e(r.explanation)}</div>`))
				.catch(() => out.html(""));
		});
	});
};
