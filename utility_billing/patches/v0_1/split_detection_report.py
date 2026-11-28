"""Detection Bills and Abnormal Consumption became two reports (user, 2026-11-28): Detection Bills and Abnormal Consumption.
The sync brings the new ones; the old record goes, with any background runs kept for it. Reports also stop being turned into
background reports by themselves after one slow run (Consumption Analytics, 2026-11-26)."""
import frappe

OLD = "Detection Bills and Abnormal Consumption"


def execute():
	if frappe.db.exists("Report", OLD):
		frappe.db.delete("Prepared Report", {"report_name": OLD})
		frappe.delete_doc("Report", OLD, force=True, ignore_permissions=True)
	for name in frappe.get_all("Report", filters={"module": "Utility Billing", "is_standard": "Yes"}, pluck="name"):
		frappe.db.set_value("Report", name, {"prepared_report": 0, "disable_prepared_report_automation": 1}, update_modified=False)
