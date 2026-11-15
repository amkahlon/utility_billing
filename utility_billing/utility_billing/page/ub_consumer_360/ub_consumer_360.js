// Stable loader: the page itself lives in public/js/ub_consumer_360.js, fetched with a version number the server works out
// from the file's modification time, so browsers never run an old copy (Frappe caches page scripts in localStorage).
// Opened from a link with ?consumer=... or ?account=... (an account number or reference number).
function ub_c360_wanted() {
	const o = frappe.route_options || {};
	const w = { consumer: o.consumer || frappe.utils.get_url_arg("consumer"), account: o.account || frappe.utils.get_url_arg("account") };
	frappe.route_options = null;
	return w;
}

frappe.pages["ub-consumer-360"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Consumer 360"), single_column: true });
	$(wrapper).addClass("ub-page");
	const wanted = ub_c360_wanted();
	$.ajax({ url: `/assets/utility_billing/js/ub_consumer_360.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_c360 = new window.UtilityBillingConsumer360(wrapper);
		wrapper.ub_c360.go(wanted);
	});
};

frappe.pages["ub-consumer-360"].on_page_show = function (wrapper) {
	const w = ub_c360_wanted();
	if (wrapper.ub_c360 && (w.consumer || w.account)) wrapper.ub_c360.go(w);
};
