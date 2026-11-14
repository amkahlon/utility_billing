// Stable loader: the page lives in public/js/ub_approvals.js, fetched with a version number from the server.
frappe.pages["ub-approvals"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Approval Inbox"), single_column: true });
	$(wrapper).addClass("ub-page");
	$.ajax({ url: `/assets/utility_billing/js/ub_approvals.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_inbox = new window.UtilityBillingApprovals(wrapper);
		wrapper.ub_inbox.load();
	});
};

frappe.pages["ub-approvals"].on_page_show = function (wrapper) {
	wrapper.ub_inbox && wrapper.ub_inbox.load();
};
