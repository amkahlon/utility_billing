// Stable loader: the page itself lives in public/js/ub_consumer_360.js, fetched with a version number the server works out
// from the file's modification time, so browsers never run an old copy (Frappe caches page scripts in localStorage).
function ub_c360_wanted() {
	const c = (frappe.route_options && frappe.route_options.consumer) || frappe.utils.get_url_arg("consumer");
	frappe.route_options = null;
	return c;
}

frappe.pages["ub-consumer-360"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Consumer 360"), single_column: true });
	$(wrapper).addClass("ub-page");
	const wanted = ub_c360_wanted();
	$.ajax({ url: `/assets/utility_billing/js/ub_consumer_360.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_c360 = new window.UtilityBillingConsumer360(wrapper);
		wrapper.ub_c360.open(wanted);
	});
};

frappe.pages["ub-consumer-360"].on_page_show = function (wrapper) {
	const c = ub_c360_wanted();
	if (wrapper.ub_c360 && c && c !== wrapper.ub_c360.consumer) wrapper.ub_c360.open(c);
};
