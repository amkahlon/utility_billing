# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Migration Batch: one society's data from its previous system, checked, loaded and approved (increment 29). See migration.py.

A Billing Manager, Accounts Officer or System Manager attaches the workbook, checks it, loads it, and compares the loaded figures
with the previous system's control figures. Until approval the load can be undone. A General Manager (not the maker) approves it;
the opening balances it prepared are then approved and posted through their own workflow."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import approval, migration

DEFAULT_APPROVER = "General Manager"
EDITABLE = (None, "", approval.DRAFT, approval.REJECTED)


class MigrationBatch(Document):
	def validate(self):
		if frappe.db.get_value("Migration Profile", self.profile, "society") != self.society:
			frappe.throw(_("Profile {0} belongs to another society.").format(self.profile))
		if self.load_status == "Loaded" and self.has_value_changed("workbook") and not self.is_new():
			frappe.throw(_("Undo the load before attaching another workbook."))
		self.batch_title = f"{self.society} · {_('as at')} {frappe.utils.formatdate(self.as_of_date)}"
		approval.validate_transition(self)
		if self.workflow_state == approval.PENDING and self.load_status != "Loaded":
			frappe.throw(_("Load the workbook before submitting the batch for approval."))

	def on_update(self):
		approval.notify(self, DEFAULT_APPROVER)

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A migration batch is approved through its workflow, not submitted directly."))
		if self.load_status != "Loaded":
			frappe.throw(_("Only a loaded batch is approved."))

	def on_submit(self):
		approval.notify(self, DEFAULT_APPROVER)

	def before_cancel(self):
		frappe.throw(_("An approved migration isn't cancelled. Correct accounts one by one."))

	def on_trash(self):
		if self.load_status == "Loaded":
			frappe.throw(_("Undo the load before deleting the batch."))


def _editable(name):
	doc = frappe.get_doc("Migration Batch", name)
	doc.check_permission("write")
	if doc.docstatus != 0 or doc.workflow_state not in EDITABLE:
		frappe.throw(_("Only a draft or rejected batch can be checked, loaded or undone."))
	if not doc.workbook:
		frappe.throw(_("Attach the workbook first."))
	return doc


@frappe.whitelist(methods=["POST"])
def check(name: str) -> dict:
	doc = _editable(name)
	if doc.load_status == "Loaded":
		frappe.throw(_("The batch is loaded. Undo it to check another workbook."))
	migration.check(doc)
	doc.save()
	return {"errors": doc.error_count, "warnings": doc.warning_count, "accounts": doc.account_count}


@frappe.whitelist(methods=["POST"])
def load(name: str) -> dict:
	doc = _editable(name)
	ok = migration.load(doc)
	doc.save()
	return {"loaded": ok, "errors": doc.error_count, "warnings": doc.warning_count, "accounts": doc.account_count,
	        "records": len(doc.records)}


@frappe.whitelist(methods=["POST"])
def undo(name: str) -> dict:
	doc = _editable(name)
	if doc.load_status != "Loaded":
		frappe.throw(_("The batch isn't loaded."))
	n = len(doc.records)
	migration.undo(doc)
	doc.save()
	return {"removed": n}


@frappe.whitelist()
def template():
	"""The empty workbook with our sheets and columns, and one example row each."""
	frappe.local.response.filename = "migration workbook template.xlsx"
	frappe.local.response.filecontent = migration.template_workbook()
	frappe.local.response.type = "binary"
