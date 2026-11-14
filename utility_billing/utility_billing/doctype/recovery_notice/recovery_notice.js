// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

const RN = "utility_billing.utility_billing.recovery.";

frappe.ui.form.on("Recovery Notice", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Print"), () => window.open(`/api/method/${RN}download_notices?notice=${encodeURIComponent(frm.doc.name)}`));
		const s = frm.doc.status;
		if (s === "Awaiting Approval") {
			frm.add_custom_button(__("Approve Order"), () =>
				frappe.confirm(__("Approve this disconnection order? You must not be the person who issued it."), () =>
					frappe.xcall(RN + "approve_order", { name: frm.doc.name }).then(() => frm.reload_doc())
				)
			).addClass("btn-primary");
		}
		if (s === "Queued" || s === "Failed") {
			frm.add_custom_button(__("Mark Sent"), () => frappe.xcall(RN + "mark_notice_api", { name: frm.doc.name, status: "Sent" }).then(() => frm.reload_doc()), __("Delivery"));
			if (s === "Queued")
				frm.add_custom_button(__("Mark Failed"), () => frappe.xcall(RN + "mark_notice_api", { name: frm.doc.name, status: "Failed" }).then(() => frm.reload_doc()), __("Delivery"));
		}
		if (s !== "Sent" && s !== "Cancelled") {
			frm.add_custom_button(__("Cancel Notice"), () =>
				frappe.prompt({ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }, (v) =>
					frappe.xcall(RN + "cancel_notice_api", { name: frm.doc.name, reason: v.reason }).then(() => frm.reload_doc())
				, __("Cancel notice"))
			);
		}
	},
});
