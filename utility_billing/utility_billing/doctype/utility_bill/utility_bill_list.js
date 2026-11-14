// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

frappe.listview_settings["Utility Bill"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const color = { Draft: "orange", Posted: "blue", Cancelled: "red" }[doc.status] || "gray";
		return [__(doc.status), color, `status,=,${doc.status}`];
	},
};
