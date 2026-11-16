// Stable loader: the page itself lives in public/js/ub_rights.js, fetched with a version number worked out from the files' dates.
frappe.pages["ub-rights"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Role Rights"), single_column: true });
	$(wrapper).addClass("ub-page");
	const arg = (frappe.route_options && frappe.route_options.role) || frappe.utils.get_url_arg("role");
	frappe.route_options = null;
	$.ajax({ url: `/assets/utility_billing/js/ub_rights.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_page = new window.UtilityBillingRoleRights(wrapper);
		wrapper.ub_page.load(arg);
	});
};

frappe.pages["ub-rights"].on_page_show = function (wrapper) {
	const arg = (frappe.route_options && frappe.route_options.role) || null;
	frappe.route_options = null;
	if (wrapper.ub_page) wrapper.ub_page.load(arg);
};
