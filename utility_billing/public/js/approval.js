// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Shared approval UI for documents using utility_billing.utility_billing.approval:
// renders the stored Approval Log as a readable, wrapping history (newest first).

frappe.provide("utility_billing");

// Impact preview (FS-CFG-07): what a tariff, tax or adjustment would have done to last cycle's bills, in a dialog.
utility_billing.add_impact_button = function (frm, method) {
	if (frm.is_new()) return;
	frm.add_custom_button(__("Impact Preview"), () => {
		frappe.dom.freeze(__("Applying it to last cycle's bills…"));
		frappe
			.xcall(`utility_billing.utility_billing.preview.${method}`, { name: frm.doc.name })
			.then((r) => {
				frappe.dom.unfreeze();
				utility_billing.show_impact(r);
			})
			.catch(() => frappe.dom.unfreeze());
	});
};

utility_billing.show_impact = function (r) {
	const e = frappe.utils.escape_html;
	const money = (v) => format_currency(v || 0);
	const sign = (v) => (v > 0 ? "+" : "") + money(v);
	const tone = (v) => (v > 0 ? "var(--red-600, #c0392b)" : v < 0 ? "var(--green-600, #2e7d32)" : "inherit");
	const rows = (list, key) =>
		list.length
			? list.map((x) => `<tr><td>${e(x.consumer_name || x.connection)}<div class="text-muted small">${e(x.connection)} · ${e(x.category || "")}</div></td>
				<td class="text-right">${money(x.old)}</td><td class="text-right">${money(x.new)}</td>
				<td class="text-right" style="color:${tone(x.difference)}">${sign(x.difference)}</td></tr>`).join("")
			: `<tr><td colspan="4" class="text-muted">${__("None")}</td></tr>`;
	const head = `<tr><th></th><th class="text-right">${__("Last cycle")}</th><th class="text-right">${__("With this")}</th><th class="text-right">${__("Difference")}</th></tr>`;
	let html = `<p><b>${e(r.what)}</b> ${r.month ? "· " + __("applied to the {0} bills", [e(r.month)]) : ""}</p>`;
	if (r.message) html += `<p class="text-muted">${e(r.message)}</p>`;
	else {
		html += `<p>${__("{0} bills: {1} now, {2} with this: ", [r.bills, money(r.old_total), money(r.new_total)])}
			<b style="color:${tone(r.difference)}">${sign(r.difference)}${r.percent == null ? "" : " (" + r.percent + "%)"}</b>.
			${__("{0} bills change.", [r.changed_bills])} ${r.unavailable ? __("{0} bills could not be worked out.", [r.unavailable]) : ""}</p>
			<h6>${__("By category")}</h6><table class="table table-sm">${head}${r.by_category
				.map((c) => `<tr><td>${e(c.category)} <span class="text-muted small">${c.bills} ${__("bills")}</span></td><td class="text-right">${money(c.old)}</td>
				<td class="text-right">${money(c.new)}</td><td class="text-right" style="color:${tone(c.difference)}">${sign(c.difference)}${c.percent == null ? "" : " (" + c.percent + "%)"}</td></tr>`)
				.join("")}</table>
			<h6>${__("Biggest rises")}</h6><table class="table table-sm">${head}${rows(r.biggest_rises)}</table>
			<h6>${__("Biggest falls")}</h6><table class="table table-sm">${head}${rows(r.biggest_falls)}</table>`;
	}
	new frappe.ui.Dialog({ title: __("Impact Preview"), size: "large", fields: [{ fieldtype: "HTML", fieldname: "body", options: html }] }).show();
};

// Work running in the background (increments 52 and 54): asks `method` every 2 seconds how far it is. show(p) while it runs,
// ended(p) once it is Done or Failed; it stops asking when still_here() says the user went elsewhere.
// p = {state: Queued | Running | Done | Failed, done, total, text, error}.
utility_billing.watch_progress = function (method, args, show, ended, still_here) {
	const ask = () =>
		frappe.xcall(method, args).then(
			(p) => {
				if (!still_here()) return;
				if (p && (p.state === "Queued" || p.state === "Running")) {
					show(p);
					setTimeout(ask, 2000);
				} else ended(p || { state: "Done" });
			},
			() => still_here() && setTimeout(ask, 5000)
		);
	ask();
};

utility_billing.watch_calculation = (run, show, ended, still_here) =>
	utility_billing.watch_progress("utility_billing.utility_billing.billing.calculation_progress", { run }, show, ended, still_here);

utility_billing.progress_percent = (p) =>
	p.total ? Math.max(3, Math.round((Math.min(p.done, p.total) / p.total) * 100)) : p.state === "Queued" ? 2 : 5;

// "Posting the bills: 1,500 of 21,446"
utility_billing.progress_text = function (p) {
	if (!p || p.state === "Queued") return __("Waiting to start…");
	const count = p.total ? __("{0} of {1}", [format_number(Math.min(p.done, p.total), null, 0), format_number(p.total, null, 0)]) : "";
	return [p.text ? __(p.text) : __("Working…"), count].filter(Boolean).join(": ");
};

// A progress bar on a form while its background work runs, then the outcome and the form reloaded (increment 54). Call it from
// refresh while the document says the work is running; o = {method, args, title, failed_title}.
utility_billing.form_progress = function (frm, o) {
	const name = frm.doc.name;
	const paint = (p) => frm.dashboard.show_progress(o.title, utility_billing.progress_percent(p), utility_billing.progress_text(p));
	paint(frm._ub_progress || { state: "Queued" });
	if (frm._ub_watching === name) return;
	frm._ub_watching = name;
	utility_billing.watch_progress(
		o.method,
		o.args,
		(p) => {
			frm._ub_progress = p;
			paint(p);
		},
		(p) => {
			frm._ub_watching = frm._ub_progress = null;
			frm.dashboard.hide_progress(o.title);
			if (p.state === "Failed") frappe.msgprint({ title: o.failed_title, indicator: "red", message: frappe.utils.escape_html(p.error || "") });
			else frappe.show_alert({ message: p.text ? __(p.text) : __("Done."), indicator: "green" }, 10);
			frm.reload_doc();
		},
		() => {
			const here = cur_frm === frm && frm.doc.name === name;
			if (!here) frm._ub_watching = frm._ub_progress = null;
			return here;
		}
	);
};

// ---- Buttons whose work runs in the background (increment 55) ----
// utility_billing.run_job(job, args, title) starts one of the server's progress.JOBS and resolves with its result when it is done. While
// it runs, a panel at the bottom right shows a progress bar; the screen stays usable, the user can go elsewhere, and after a page
// reload the panel picks the job up again. A failure is shown there and in a message, and the promise is then left unresolved.
const UB_JOBS = "ub_background_jobs";
const ub_saved_jobs = () => {
	try {
		return JSON.parse(localStorage.getItem(UB_JOBS) || "[]");
	} catch (e) {
		return [];
	}
};
const ub_save_jobs = (list) => {
	try {
		localStorage.setItem(UB_JOBS, JSON.stringify(list));
	} catch (e) {
		/* private window or blocked storage: the panel just won't come back after a reload */
	}
};

utility_billing.run_job = function (job, args, title) {
	return frappe
		.xcall("utility_billing.utility_billing.progress.start", { job, args: JSON.stringify(args), title })
		.then((r) => new Promise((resolve) => utility_billing.track_job({ job, record: r.record, title, at: Date.now() }, resolve)));
};

utility_billing.track_job = function (j, resolve) {
	const id = `${j.job}:${j.record}`;
	ub_save_jobs([...ub_saved_jobs().filter((x) => `${x.job}:${x.record}` !== id), j]);
	let $tray = $(".ub-jobs");
	if (!$tray.length) $tray = $('<div class="ub-jobs" aria-live="polite"></div>').appendTo(document.body);
	$tray.find(`[data-job="${CSS.escape(id)}"]`).remove();
	const e = frappe.utils.escape_html;
	const $card = $(`<div class="ub-job" data-job="${e(id)}"><div class="ub-job-head"><span>${e(j.title || __("Working"))}</span>
		<button class="ub-job-close" title="${__("Hide")}" aria-label="${__("Hide")}">×</button></div>
		<div class="progress"><div class="progress-bar progress-bar-striped progress-bar-animated" style="width:100%"></div></div>
		<div class="ub-job-text">${__("Waiting to start…")}</div></div>`).appendTo($tray);
	$card.find(".ub-job-close").on("click", () => $card.hide()); // hides the panel only; the work and its follow-up go on
	const paint = (p) => {
		const $bar = $card.find(".progress-bar");
		$bar.toggleClass("progress-bar-striped progress-bar-animated", !p.total).css("width", p.total ? `${utility_billing.progress_percent(p)}%` : "100%");
		$card.find(".ub-job-text").text(utility_billing.progress_text(p));
	};
	utility_billing.watch_progress(
		"utility_billing.utility_billing.progress.job_progress",
		{ job: j.job, record: j.record },
		paint,
		(p) => {
			ub_save_jobs(ub_saved_jobs().filter((x) => `${x.job}:${x.record}` !== id));
			if (!p.by) { // watch_progress's stand-in when the server has no news: every job's real news names who started it
				// no news kept about it (kept 6 hours; or the server was restarted): it has ended, but nothing says how
				$card.find(".progress-bar").removeClass("progress-bar-striped progress-bar-animated").css("width", "100%");
				$card.find(".ub-job-text").text(__("It has ended. Open the record to see the outcome."));
				return;
			}
			if (p.state === "Failed") {
				$card.addClass("failed").find(".progress-bar").removeClass("progress-bar-striped progress-bar-animated").css("width", "100%");
				$card.find(".ub-job-text").text(p.error || __("It didn't complete."));
				frappe.msgprint({ title: __("{0}: didn't complete", [j.title || __("Background work")]), indicator: "red", message: e(p.error || "") });
				return;
			}
			$card.addClass("done").find(".progress-bar").removeClass("progress-bar-striped progress-bar-animated").css("width", "100%");
			$card.find(".ub-job-text").text(__("Done."));
			setTimeout(() => $card.fadeOut(400, () => $card.remove()), 6000);
			if (resolve) resolve(p.result || {});
		},
		() => document.body.contains($card[0])
	);
};

// Print Bills in the background (increments 55, 56): the PDFs are drawn 50 bills at a time with the count shown, then offered to open.
// More bills than one PDF holds come as parts (`part`: one part's number, or empty for all). The server records the printed bills as
// Printed. A button inside the message opens each PDF, since a browser blocks a window opened without a click.
utility_billing.print_run_bills = function (run, block, route, part) {
	const title = part ? __("Printing part {0} of the bills of {1}", [part, run]) : __("Printing the bills of {0}", [run]);
	return utility_billing.run_job("print_bills", { run, block: block || "", route: route || "", part: part || "" }, title).then((r) => {
		const n = (v) => format_number(v, null, 0);
		const files = r.files || [{ file_url: r.file_url, bills: r.bills }];
		const link = (f, label, kind = "btn-primary") =>
			`<a class="btn ${kind} btn-sm" style="margin:0 6px 6px 0" href="${encodeURI(f.file_url)}" target="_blank" rel="noopener">${label}</a>`;
		const body = files.length === 1 && !(r.parts > 1)
			? `<p>${r.bills === 1 ? __("1 bill.") : __("{0} bills, in delivery order.", [n(r.bills)])}</p><p>${link(files[0], __("Open the PDF"))}</p>`
			: `<p>${__("{0} bills in delivery order, in {1} PDFs of up to 500 bills. Open each to print it.", [n(r.bills), files.length])}</p>
				<div>${files.map((f) => link(f, __("Part {0}: bills {1} to {2}", [f.part, n(f.first), n(f.last)]), "btn-default")).join("")}</div>
				<p class="text-muted small">${__("They are kept on the bill run, under Attachments.")}</p>`;
		frappe.msgprint({ title: __("Bills ready to print"), indicator: "green", message: body, wide: files.length > 1 });
	});
};

// after a reload: the jobs still running come back to the panel (their outcome is shown, without the screen's own follow-up)
$(document).on("app_ready", () => {
	const fresh = ub_saved_jobs().filter((x) => Date.now() - (x.at || 0) < 6 * 3600 * 1000);
	ub_save_jobs(fresh);
	fresh.forEach((x) => utility_billing.track_job(x, null));
});

// The same bar as a card on a page: <section class="ub-card ub-progress-card"> filled with this.
utility_billing.progress_card = (title, p) => `<div class="ub-cardhead"><h2>${frappe.utils.escape_html(title)}</h2></div>
	<div class="progress" style="height:10px"><div class="progress-bar" style="width:${utility_billing.progress_percent(p)}%"></div></div>
	<p class="ub-kpi-note" style="margin-top:8px">${frappe.utils.escape_html(utility_billing.progress_text(p))}</p>`;

utility_billing.calculation_text = function (p) {
	if (p.state === "Queued") return __("Waiting to start…");
	return p.total ? __("Calculating bills: {0} of {1} accounts", [format_number(Math.min(p.done, p.total), null, 0), format_number(p.total, null, 0)])
		: __("Calculating bills…");
};

utility_billing.calculation_percent = (p) => (p.total ? Math.max(3, Math.round((Math.min(p.done, p.total) / p.total) * 100)) : 2);

// For any document using the shared approval module: call from refresh, and return
// utility_billing.before_workflow_action(frm) from the form's before_workflow_action.
utility_billing.setup_approval_form = function (frm) {
	utility_billing.render_approval_history(frm);
	if (frm.doc.workflow_state === "Rejected" && frm.doc.rejection_reason) {
		frm.dashboard.set_headline_alert(
			`${__("Rejected")}: ${frappe.utils.escape_html(frm.doc.rejection_reason)}`,
			"red"
		);
	}
	if (frm.doc.workflow_state === "Pending Approval" && frm.doc.society) {
		// approvals in turn (FS-ACC-04): show who has approved and whose level is next
		frappe.call({ method: "utility_billing.utility_billing.approval.level_status", args: { doctype: frm.doctype, name: frm.doc.name } }).then((r) => {
			const s = r.message || {};
			if (!s.in_turn) return;
			const e = frappe.utils.escape_html;
			const steps = s.chain.map((role, i) => {
				const d = s.done[i];
				if (d) return `${i + 1}. ${e(role)} ✓ ${e(frappe.user.full_name(d.user) || d.user)}`;
				return i === s.done.length ? `<b>${i + 1}. ${e(role)} — ${__("next")}</b>` : `${i + 1}. ${e(role)}`;
			});
			frm.dashboard.set_headline_alert(`${__("Approval in turn")}: ${steps.join(" → ")}`, "blue");
		});
	}
};

utility_billing.before_workflow_action = function (frm) {
	const action = frm.selected_workflow_action;
	if (action === "Approve" && frm.doc.society) {
		// approvals in turn: below the final level, Approve records this level and the document waits for the next
		return frappe
			.call({ method: "utility_billing.utility_billing.approval.level_status", args: { doctype: frm.doctype, name: frm.doc.name } })
			.then((r) => {
				const s = r.message || {};
				if (!s.in_turn || s.final) return;
				return frappe
					.call({ method: "utility_billing.utility_billing.approval.approve_level", args: { doctype: frm.doctype, name: frm.doc.name } })
					.then((res) => {
						frappe.dom.unfreeze();
						frappe.show_alert({ message: __("Approved at level {0}. It now waits for: {1}", [res.message.level, res.message.next_role]), indicator: "green" });
						frm.reload_doc();
						return Promise.reject(); // the workflow's own Approve doesn't run
					});
			});
	}
	if (action === "Cancel" && frm.fields_dict.cancellation_reason) {
		return utility_billing.ask_reason(frm, {
			title: __("Reverse {0}", [__(frm.doctype)]),
			label: __("Reason for reversing"),
			description: __("The reason is kept with the document and in the audit log."),
			button: __("Reverse"),
			method: "utility_billing.utility_billing.approval.set_cancellation_reason",
		});
	}
	if (action !== "Reject") return;
	return utility_billing.ask_reason(frm, {
		title: __("Reject {0}", [__(frm.doctype)]),
		label: __("Reason for rejecting"),
		description: __("The submitter sees this reason and can correct and resubmit."),
		button: __("Reject"),
		method: "utility_billing.utility_billing.approval.set_rejection_reason",
	});
};

// Ask for a reason in a dialog and store it on the server before the workflow action runs.
utility_billing.ask_reason = function (frm, o) {
	// Frappe freezes the screen before this runs; lift it so the dialog can be used (D6).
	frappe.dom.unfreeze();
	return new Promise((resolve, reject) => {
		let saved = false;
		const d = new frappe.ui.Dialog({
			title: o.title,
			fields: [
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: o.label,
					description: o.description,
					reqd: 1,
				},
			],
			primary_action_label: o.button,
			primary_action({ reason }) {
				frappe
					.call({
						method: o.method,
						args: { doctype: frm.doctype, name: frm.doc.name, reason },
					})
					.then(() => {
						saved = true;
						d.hide();
						frappe.dom.freeze();
						resolve();
					});
			},
		});
		d.onhide = () => {
			if (!saved) {
				frm.selected_workflow_action = null;
				reject();
			}
		};
		d.show();
	});
};

utility_billing.render_approval_history = function (frm, html_field = "approval_history_html") {
	const field = frm.fields_dict[html_field];
	if (!field) return;
	const rows = (frm.doc.approval_log || []).slice().reverse();
	if (!rows.length) {
		field.$wrapper.html(
			`<p class="text-muted small">${__("No approval steps yet.")}</p>`
		);
		return;
	}
	const esc = frappe.utils.escape_html;
	const badge = {
		Approve: "green",
		Reject: "red",
		Withdraw: "orange",
		Cancel: "gray",
	};
	const items = rows
		.map((r) => {
			const who = frappe.user.full_name(r.user) || r.user;
			const remarks = r.remarks
				? `<div style="white-space: pre-wrap; margin-top: 4px;">${esc(r.remarks)}</div>`
				: "";
			return `<div style="padding: 10px 0; border-bottom: 1px solid var(--border-color);">
				<span class="indicator-pill ${badge[r.action] || "blue"}">${esc(__(r.action))}</span>
				<b style="margin-left: 6px;">${esc(who)}</b>
				<span class="text-muted small" style="margin-left: 6px;">${esc(frappe.datetime.str_to_user(r.on))}</span>
				${remarks}
			</div>`;
		})
		.join("");
	field.$wrapper.html(`<div>${items}</div>`);
};

// ---- Notifications: read when opened, cleared when acted on (user request, 2026-11-14) ----
// Frappe only offers "Mark all as read"; opening one notification left it unread and the counter unchanged.
// Clicking a notification now marks that one read, and the server clears the notifications about a document
// when someone approves, rejects, withdraws or resubmits it (it sends "ub_notifications_changed" to the
// affected users). Either way the counter and the list are refreshed from the server.
utility_billing.notifications_ui = function () {
	// The sidebar's notification panel keeps the bell's list and counter in its "notifications" tab.
	const panel = frappe.app && frappe.app.sidebar && frappe.app.sidebar.notifications;
	return (panel && panel.tabs && panel.tabs.notifications) || null;
};

utility_billing.refresh_notifications = function () {
	if (!frappe.session || frappe.session.user === "Guest") return;
	frappe
		.xcall("frappe.client.get_list", {
			doctype: "Notification Log",
			filters: { for_user: frappe.session.user, read: 0 },
			fields: ["name"],
			limit_page_length: 500,
		})
		.then((rows) => {
			const unread = new Set(rows.map((r) => r.name));
			const ui = utility_billing.notifications_ui();
			if (ui) {
				(ui.dropdown_items || []).forEach((n) => (n.read = unread.has(n.name) ? 0 : 1));
				ui.update_count_badge(unread.size);
			} else {
				const $count = $(".notification-count");
				unread.size
					? $count.text(unread.size > 99 ? "99+" : unread.size).removeClass("hidden")
					: $count.addClass("hidden");
			}
			$(".notification-item[data-name]").each(function () {
				$(this).toggleClass("unread", unread.has($(this).attr("data-name")));
			});
		});
};

// Listen in the capture phase: Frappe's panel handles the click itself and the event never bubbles up to the document.
document.addEventListener(
	"click",
	(e) => {
		const item = e.target.closest && e.target.closest(".notification-item[data-name]");
		if (!item || !item.classList.contains("unread")) return;
		item.classList.remove("unread");
		frappe
			.xcall("frappe.desk.doctype.notification_log.notification_log.mark_as_read", {
				docname: item.getAttribute("data-name"),
			})
			.then(() => utility_billing.refresh_notifications());
	},
	true
);

$(document).on("startup", () => {
	frappe.realtime.on("ub_notifications_changed", () => utility_billing.refresh_notifications());
});
