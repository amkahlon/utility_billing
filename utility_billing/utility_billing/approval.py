# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Maker-checker approval shared by every document that goes through an approval workflow
(FS-ACC-03..06, 08). Tariff uses it now; adjustments, revised bills and bill runs will too.

A document using it needs these fields: workflow_state, requested_by, requested_on, approved_by,
approved_on, rejection_reason and an `approval_log` table of "Approval Log" rows.

States: Draft -> Pending Approval -> Approved | Rejected; Pending Approval -> Draft (Withdraw);
Rejected -> Pending Approval (resubmit); Approved -> Cancelled.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

DRAFT = "Draft"
PENDING = "Pending Approval"
APPROVED = "Approved"
REJECTED = "Rejected"
CANCELLED = "Cancelled"

ACTION_FOR = {
	(DRAFT, PENDING): "Submit for Approval",
	(REJECTED, PENDING): "Resubmit",
	(PENDING, APPROVED): "Approve",
	(PENDING, REJECTED): "Reject",
	(PENDING, DRAFT): "Withdraw",
	(APPROVED, CANCELLED): "Cancel",
}


def workflow_active(doctype: str) -> bool:
	return bool(frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "name"))


def assert_not_maker(doc, user: str):
	"""FS-ACC-05: whoever submitted (or created) a document can't approve or reject it, at any level."""
	if user in {doc.get("requested_by"), doc.owner}:
		frappe.throw(
			_("You submitted or created this {0}, so another user must approve or reject it (maker-checker).").format(
				_(doc.doctype).lower()
			),
			frappe.PermissionError,
			title=_("Not allowed"),
		)


def validate_transition(doc):
	"""Call from validate(). Enforces the rules and records the step in the approval history."""
	if not workflow_active(doc.doctype) or doc.is_new():
		return
	before = doc.get_doc_before_save()
	old = before.get("workflow_state") if before else None
	new = doc.workflow_state
	user = frappe.session.user
	label = _(doc.doctype).lower()

	if old == PENDING and new == PENDING:
		frappe.throw(
			_("This {0} is waiting for approval and can't be changed. The submitter can withdraw it, or the approver can reject it, so it can be edited.").format(label),
			title=_("Pending approval"),
		)
	if old == new:
		return

	if new == PENDING:
		doc.requested_by = user
		doc.requested_on = now_datetime()
		doc.rejection_reason = None
	elif old == PENDING and new in (APPROVED, REJECTED):
		assert_not_maker(doc, user)
		if new == APPROVED:
			doc.approved_by = user
			doc.approved_on = now_datetime()
		elif not (doc.rejection_reason or "").strip():
			frappe.throw(_("Give a reason for rejecting this {0}.").format(label))
	elif old == PENDING and new == DRAFT:
		if user != doc.get("requested_by"):
			frappe.throw(
				_("Only {0}, who submitted it, can withdraw it.").format(doc.get("requested_by")),
				frappe.PermissionError,
			)

	log(doc, old, new, remarks=doc.rejection_reason if new == REJECTED else None)
	doc.flags.approval_change = (old, new)


def log(doc, old, new, remarks=None):
	doc.append(
		"approval_log",
		{
			"action": ACTION_FOR.get((old, new), f"{old} → {new}"),
			"from_state": old,
			"to_state": new,
			"user": frappe.session.user,
			"on": now_datetime(),
			"remarks": remarks,
		},
	)


def users_with_role(role: str) -> list[str]:
	return frappe.get_all(
		"Has Role",
		filters={"role": role, "parenttype": "User", "parent": ["not in", ["Administrator", "Guest"]]},
		pluck="parent",
		distinct=True,
	)


def notify(doc, approver_role: str):
	"""Call from on_update / on_submit. In-app notifications: approvers when something waits for them,
	the submitter when it's approved, rejected or cancelled."""
	change = doc.flags.pop("approval_change", None) if doc.flags.get("approval_change") else None
	if not change:
		return
	old, new = change
	actor = frappe.session.user
	title = doc.get_title() or doc.name
	label = _(doc.doctype)
	if new == PENDING:
		enabled = set(frappe.get_all("User", filters={"enabled": 1}, pluck="name"))
		users = [u for u in users_with_role(approver_role) if u in enabled and u != actor]
		subject = _("{0} {1} is waiting for your approval").format(label, frappe.bold(title))
	elif new in (APPROVED, REJECTED, CANCELLED) and doc.get("requested_by"):
		users = [doc.requested_by] if doc.requested_by != actor else []
		subject = {
			APPROVED: _("{0} {1} was approved"),
			REJECTED: _("{0} {1} was rejected: {2}"),
			CANCELLED: _("{0} {1} was cancelled"),
		}[new].format(label, frappe.bold(title), doc.rejection_reason or "")
	else:
		return
	if not users:
		return
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	enqueue_create_notification(
		users,
		{
			"type": "Alert",
			"document_type": doc.doctype,
			"document_name": doc.name,
			"subject": subject,
			"from_user": actor,
		},
	)


@frappe.whitelist()
def set_rejection_reason(doctype: str, name: str, reason: str):
	"""Store the approver's reason just before the Reject action is applied."""
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	if doc.get("workflow_state") != PENDING:
		frappe.throw(_("Only a document that is pending approval can be rejected."))
	from frappe.model.workflow import get_transitions

	if not any(t.action == "Reject" for t in get_transitions(doc)):
		frappe.throw(_("You can't reject this document."), frappe.PermissionError)
	assert_not_maker(doc, frappe.session.user)
	reason = (reason or "").strip()
	if len(reason) < 5:
		frappe.throw(_("Write a reason of at least 5 characters."))
	doc.db_set("rejection_reason", reason, update_modified=False)
