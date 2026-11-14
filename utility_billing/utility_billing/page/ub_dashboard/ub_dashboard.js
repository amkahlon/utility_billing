// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Stable loader. Frappe keeps a page's script in the browser for good (outside developer mode), so the real code lives in
// public/js/ub_dashboard.js and is fetched with a version the server works out from the file dates (frappe.boot.ub_ui_version):
// an update reaches every browser by itself, without clearing anything.

frappe.pages["ub-dashboard"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Dashboard"), single_column: true });
	$(wrapper).addClass("ub-page");
	$.ajax({ url: `/assets/utility_billing/js/ub_dashboard.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_dashboard = new window.UtilityBillingDashboard(wrapper);
		wrapper.ub_dashboard.load();
	});
};

frappe.pages["ub-dashboard"].on_page_show = function (wrapper) {
	wrapper.ub_dashboard && wrapper.ub_dashboard.load();
};
