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
