// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// RA Finding list: run the revenue assurance checks for a society and month (FS-RA); they also run by themselves on the 1st.
frappe.listview_settings["RA Finding"] = {
	get_indicator(doc) {
		const colour = { Open: "red", Assigned: "orange", Investigating: "blue", Closed: "green" }[doc.status] || "gray";
		return [__(doc.status), colour, "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Run Checks"), () => {
			frappe.prompt([
				{ fieldname: "society", fieldtype: "Link", label: __("Society"), options: "Society", reqd: 1,
				  default: frappe.defaults.get_user_default("Society") },
				{ fieldname: "month", fieldtype: "Date", label: __("Month"), description: __("Leave empty for the latest month with posted bills.") },
			], (v) => {
				frappe.call({
					method: "utility_billing.utility_billing.revenue_assurance.run_now",
					args: v,
					freeze: true,
					freeze_message: __("Running the checks…"),
				}).then((r) => {
					const out = r.message;
					frappe.show_alert({ message: __("{0} new findings, {1} updated", [out.new, out.updated]), indicator: out.new ? "orange" : "green" });
					listview.refresh();
				});
			}, __("Revenue assurance checks"), __("Run"));
		});
	},
};
