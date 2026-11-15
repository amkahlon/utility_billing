// Stable loader: the page itself lives in public/js/ub_readings.js, fetched with a version number the server works out
// from the file's modification time, so browsers never run an old copy (Frappe caches page scripts in localStorage).
frappe.pages["ub-readings"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Reading Dashboard"), single_column: true });
	$(wrapper).addClass("ub-page");
	$.ajax({ url: `/assets/utility_billing/js/ub_readings.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_readings = new window.UtilityBillingReadings(wrapper);
		wrapper.ub_readings.load();
	});
};

frappe.pages["ub-readings"].on_page_show = function (wrapper) {
	wrapper.ub_readings && wrapper.ub_readings.load();
};
