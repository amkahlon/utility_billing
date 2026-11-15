// Stable loader: the page itself lives in public/js/ub_tariff.js, fetched with a version number the server works out from the file's
// modification time, so browsers never run an old copy. Opened with ?name=<tariff> (the Tariff form's "Open in Tariff Editor").
function ub_tariff_wanted() {
	const n = (frappe.route_options && frappe.route_options.name) || frappe.utils.get_url_arg("name");
	frappe.route_options = null;
	return n;
}

frappe.pages["ub-tariff"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Tariff Editor"), single_column: true });
	$(wrapper).addClass("ub-page");
	const wanted = ub_tariff_wanted();
	$.ajax({ url: `/assets/utility_billing/js/ub_tariff.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_tariff = new window.UtilityBillingTariffEditor(wrapper);
		wrapper.ub_tariff.open(wanted);
	});
};

frappe.pages["ub-tariff"].on_page_show = function (wrapper) {
	const n = ub_tariff_wanted();
	if (wrapper.ub_tariff && n && n !== wrapper.ub_tariff.name) wrapper.ub_tariff.open(n);
};
