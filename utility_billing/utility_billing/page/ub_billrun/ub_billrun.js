// Stable loader: the page itself lives in public/js/ub_billrun.js, fetched with a version number the server works out from the file's
// modification time, so browsers never run an old copy. Opened with ?run=<Bill Run>.
frappe.pages["ub-billrun"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Bill Run"), single_column: true });
	$(wrapper).addClass("ub-page");
	const run = (frappe.route_options && frappe.route_options.run) || frappe.utils.get_url_arg("run");
	frappe.route_options = null;
	$.ajax({ url: `/assets/utility_billing/js/ub_billrun.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_billrun = new window.UtilityBillingRunWizard(wrapper);
		wrapper.ub_billrun.load(run);
	});
};

frappe.pages["ub-billrun"].on_page_show = function (wrapper) {
	const run = (frappe.route_options && frappe.route_options.run) || null;
	frappe.route_options = null;
	if (wrapper.ub_billrun && run) wrapper.ub_billrun.load(run);
};
