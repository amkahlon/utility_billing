// Stable loader: the page itself lives in public/js/ub_reading_entry.js, fetched with a version number the server works out
// from the file's modification time, so browsers never run an old copy (Frappe caches page scripts in localStorage).
frappe.pages["ub-reading-entry"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Reading Entry"), single_column: true });
	$(wrapper).addClass("ub-page");
	$.ajax({ url: `/assets/utility_billing/js/ub_reading_entry.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_reading_entry = new window.UtilityBillingReadingEntry(wrapper);
		// opened from a reading cycle (its Reading Entry button) the cycle is given in the route options
		Object.assign(wrapper.ub_reading_entry.q, frappe.route_options || {});
		frappe.route_options = null;
		wrapper.ub_reading_entry.load();
	});
};

frappe.pages["ub-reading-entry"].on_page_show = function (wrapper) {
	const page = wrapper.ub_reading_entry;
	if (!page) return;
	if (frappe.route_options && frappe.route_options.cycle) {
		Object.assign(page.q, frappe.route_options, { start: 0 });
		frappe.route_options = null;
		page.load();
	}
};
