// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// New society from a template (FS-CFG-27).
frappe.listview_settings["Society"] = {
	onload(list) {
		if (!frappe.model.can_create("Society")) return;
		list.page.add_inner_button(__("New Society from Template"), () => {
			frappe.prompt(
				[
					{ fieldname: "society_name", fieldtype: "Data", label: __("Legal Name"), reqd: 1 },
					{ fieldname: "short_name", fieldtype: "Data", label: __("Short Name"), reqd: 1 },
					{ fieldname: "template", fieldtype: "Select", label: __("Template"), options: ["Pakistan housing society"], default: "Pakistan housing society" },
				],
				(v) =>
					frappe.call({ method: "utility_billing.utility_billing.templates.new_society", args: v, freeze: true }).then(({ message: r }) => {
						frappe.msgprint({
							title: __("Society {0} is set up", [r.society]),
							message: `<p>${__("Still to do:")}</p><ul>${r.to_do.map((t) => `<li>${frappe.utils.escape_html(t)}</li>`).join("")}</ul>`,
							indicator: "green",
						});
						list.refresh();
					}),
				__("New Society from Template"),
				__("Create")
			);
		});
	},
};
