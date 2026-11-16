// Stable loader: the page itself lives in public/js/ub_users.js, fetched with a version number worked out from the files' dates.
frappe.pages["ub-users"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Society Users"), single_column: true });
	$(wrapper).addClass("ub-page");
	const arg = (frappe.route_options && frappe.route_options.user) || frappe.utils.get_url_arg("user");
	frappe.route_options = null;
	$.ajax({ url: `/assets/utility_billing/js/ub_users.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_page = new window.UtilityBillingSocietyUsers(wrapper);
		wrapper.ub_page.load(arg);
	});
};

frappe.pages["ub-users"].on_page_show = function (wrapper) {
	const arg = (frappe.route_options && frappe.route_options.user) || null;
	frappe.route_options = null;
	if (wrapper.ub_page) wrapper.ub_page.load(arg);
};
