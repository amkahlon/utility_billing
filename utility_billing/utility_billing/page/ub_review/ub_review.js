// Stable loader: the page itself lives in public/js/ub_review.js, fetched with a version number the server works out from the file's
// modification time, so browsers never run an old copy. Opened with ?cycle=<reading cycle> or for the latest open cycle.
frappe.pages["ub-review"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Reading Review"), single_column: true });
	$(wrapper).addClass("ub-page");
	const cycle = (frappe.route_options && frappe.route_options.cycle) || frappe.utils.get_url_arg("cycle");
	frappe.route_options = null;
	$.ajax({ url: `/assets/utility_billing/js/ub_review.js?v=${frappe.boot.ub_ui_version || ""}`, dataType: "script", cache: true }).done(() => {
		wrapper.ub_review = new window.UtilityBillingReadingReview(wrapper);
		wrapper.ub_review.load(cycle);
	});
};

frappe.pages["ub-review"].on_page_show = function (wrapper) {
	const cycle = (frappe.route_options && frappe.route_options.cycle) || null;
	frappe.route_options = null;
	if (wrapper.ub_review && cycle) wrapper.ub_review.load(cycle);
};
