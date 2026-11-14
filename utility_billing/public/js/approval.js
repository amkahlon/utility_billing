// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Shared approval UI for documents using utility_billing.utility_billing.approval:
// renders the stored Approval Log as a readable, wrapping history (newest first).

frappe.provide("utility_billing");

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
};

utility_billing.before_workflow_action = function (frm) {
	if (frm.selected_workflow_action !== "Reject") return;
	// Frappe freezes the screen before this runs; lift it so the dialog can be used (D6).
	frappe.dom.unfreeze();
	return new Promise((resolve, reject) => {
		let saved = false;
		const d = new frappe.ui.Dialog({
			title: __("Reject {0}", [__(frm.doctype)]),
			fields: [
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason for rejecting"),
					description: __("The submitter sees this reason and can correct and resubmit."),
					reqd: 1,
				},
			],
			primary_action_label: __("Reject"),
			primary_action({ reason }) {
				frappe
					.call({
						method: "utility_billing.utility_billing.approval.set_rejection_reason",
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
