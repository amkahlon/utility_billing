// Stable loader: the page itself lives in public/js/ub_cash.js, fetched with a version number the server works out from the file's
// modification time, so browsers never run an old copy.
frappe.pages["ub-cash"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Cash Counter"), single_column: true });
	$(wrapper).addClass("ub-page");
	$.ajax({ url: `/assets/utility_billing/js/ub_cash.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_cash = new window.UtilityBillingCashCounter(wrapper);
		wrapper.ub_cash.load();
	});
};

frappe.pages["ub-cash"].on_page_show = function (wrapper) {
	wrapper.ub_cash && wrapper.ub_cash.load();
};
