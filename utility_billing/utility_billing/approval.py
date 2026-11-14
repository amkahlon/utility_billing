# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Maker-checker approval shared by every document that goes through an approval workflow
(FS-ACC-03..06, 08). Tariff uses it now; adjustments, revised bills and bill runs will too.

A document using it needs these fields: workflow_state, requested_by, requested_on, approved_by,
approved_on, rejection_reason and an `approval_log` table of "Approval Log" rows.

States: Draft -> Pending Approval -> Approved | Rejected; Pending Approval -> Draft (Withdraw);
Rejected -> Pending Approval (resubmit); Approved -> Cancelled.
"""

import json

import frappe
from frappe import _
from frappe.utils import cstr, flt, now_datetime

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


# ----- approval levels by amount (FS-ACC-04, FS-WF-04) -----
# For these documents the Society's "Approval Levels by Amount" table says who approves up to which amount. With no rows for a
# document type the default approver applies, so nothing changes until a society sets levels.
AMOUNT_FIELD = {"Account Adjustment": "amount", "Refund": "amount", "Bulk Adjustment": "total_amount", "Write Off": "amount", "Installment Plan": "amount_to_split",
                "Security Deposit": "amount"}
DEFAULT_APPROVER = {"Account Adjustment": "General Manager", "Refund": "General Manager", "Bulk Adjustment": "General Manager",
                    "Write Off": "General Manager", "Installment Plan": "Billing Manager", "Security Deposit": "General Manager"}


def required_roles(doc) -> list[str] | None:
	"""Roles that may approve this document, the lowest level that covers its amount first, then every higher level. None for
	documents that aren't amount-led."""
	field = AMOUNT_FIELD.get(doc.doctype)
	if not field:
		return None
	amount = flt(doc.get(field))
	levels = []
	if doc.get("society"):
		levels = [r for r in frappe.get_cached_doc("Society", doc.society).get("approval_levels") or [] if r.transaction == doc.doctype]
	if not levels:
		return [DEFAULT_APPROVER[doc.doctype]]
	levels.sort(key=lambda r: (flt(r.up_to_amount) == 0, flt(r.up_to_amount)))  # a level with no limit is the top
	for i, r in enumerate(levels):
		if flt(r.up_to_amount) == 0 or amount <= flt(r.up_to_amount):
			return [x.approver_role for x in levels[i:]]
	return [levels[-1].approver_role]


def assert_level(doc, user: str):
	"""The approver must hold the role the amount needs, or a higher level's (Administrator always may)."""
	roles = required_roles(doc)
	if roles is None or user == "Administrator":
		return
	if not set(frappe.get_roles(user)) & set(roles):
		frappe.throw(
			_("A {0} of Rs {1} needs approval by {2}{3}.").format(
				_(doc.doctype).lower(), f"{flt(doc.get(AMOUNT_FIELD[doc.doctype])):,.0f}", roles[0],
				_(" or a higher level") if len(roles) > 1 else ""),
			frappe.PermissionError, title=_("Not allowed"))


def reset_for_new_document(doc):
	"""A new or amended document starts its own approval round: the original keeps its history
	(found 2026-11-13: an amended tariff inherited the original's "Cancel" row)."""
	doc.approval_log = []
	for field in ("requested_by", "requested_on", "approved_by", "approved_on", "rejection_reason",
	              "changes_since_last_submission", "submitted_snapshot"):
		doc.set(field, None)
	if doc.meta.get_field("workflow_state"):
		doc.workflow_state = DRAFT


def validate_transition(doc):
	"""Call from validate(). Enforces the rules and records the step in the approval history."""
	if not workflow_active(doc.doctype):
		return
	if doc.is_new():
		reset_for_new_document(doc)
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

	had_previous_submission = False
	if new == PENDING:
		lines = changes_since_last_submission(doc)
		had_previous_submission = lines is not None
		doc.changes_since_last_submission = ("\n".join(lines) or NO_CHANGES) if had_previous_submission else None
		doc.submitted_snapshot = json.dumps(snapshot(doc))
		doc.requested_by = user
		doc.requested_on = now_datetime()
		doc.rejection_reason = None
	elif old == PENDING and new in (APPROVED, REJECTED):
		assert_not_maker(doc, user)
		assert_level(doc, user)
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

	remarks = None
	if new == REJECTED:
		remarks = doc.rejection_reason
	elif new == PENDING and had_previous_submission:
		remarks = doc.changes_since_last_submission
	log(doc, old, new, remarks=remarks)
	doc.flags.approval_change = (old, new)


# ----- changes since the last submission -----
# Each submission stores a snapshot of the editable content (fields and table rows) in the hidden
# `submitted_snapshot` field; the next submission compares against it. Documents submitted before
# snapshots existed fall back to the version history. (Version history alone isn't enough: it can be
# switched off per DocType, and Frappe skips it in tests.)
NO_CHANGES = "No changes since the last submission."
APPROVAL_FIELDS = {
	"workflow_state", "requested_by", "requested_on", "approved_by", "approved_on", "rejection_reason",
	"approval_log", "changes_since_last_submission", "submitted_snapshot", "modified", "modified_by",
	"docstatus", "amended_from",
}


def changes_since_last_submission(doc) -> list[str] | None:
	"""Readable change lines since the previous submission; None if this is the first submission."""
	before = doc.get("submitted_snapshot")
	if before:
		return diff_snapshot(doc, frappe.parse_json(before))
	previous = last_submission_time(doc)
	return change_summary(doc, since=previous) if previous else None


def _content_fields(meta):
	from frappe.model import no_value_fields, table_fields

	for df in meta.fields:
		if df.fieldname in APPROVAL_FIELDS or df.hidden or df.read_only:
			continue
		if df.fieldtype in table_fields:
			yield df, True
		elif df.fieldtype not in no_value_fields:
			yield df, False


def snapshot(doc) -> dict:
	out = {"fields": {}, "tables": {}}
	for df, is_table in _content_fields(doc.meta):
		if is_table:
			child = frappe.get_meta(df.options)
			cols = [c.fieldname for c, t in _content_fields(child) if not t]
			out["tables"][df.fieldname] = [{"name": r.name, **{c: r.get(c) for c in cols}} for r in doc.get(df.fieldname)]
		else:
			out["fields"][df.fieldname] = doc.get(df.fieldname)
	return frappe.parse_json(frappe.as_json(out))


def _same(a, b) -> bool:
	if isinstance(a, (int, float)) or isinstance(b, (int, float)):
		return flt(a) == flt(b)
	return cstr(a) == cstr(b)


def diff_snapshot(doc, before: dict) -> list[str]:
	meta = doc.meta
	after = snapshot(doc)
	lines = []
	for fieldname, new in after["fields"].items():
		old = before.get("fields", {}).get(fieldname)
		if not _same(old, new):
			lines.append(f"~ {_label(meta, fieldname)}: {_fmt(old)} → {_fmt(new)}")
	for table, rows in after["tables"].items():
		child = frappe.get_meta(meta.get_field(table).options)
		old_rows = {r["name"]: r for r in before.get("tables", {}).get(table, [])}
		new_rows = {r["name"]: r for r in rows}
		for name, row in new_rows.items():
			if name not in old_rows:
				lines.append(f"+ {_label(meta, table)}: {_row_text(meta, table, row)}")
		for name, row in old_rows.items():
			if name not in new_rows:
				lines.append(f"- {_label(meta, table)}: {_row_text(meta, table, row)}")
		for name in new_rows.keys() & old_rows.keys():
			old, new = old_rows[name], new_rows[name]
			changed = [(f, old.get(f), new.get(f)) for f in new if f != "name" and not _same(old.get(f), new.get(f))]
			if changed:
				details = "; ".join(f"{_label(child, f)} {_fmt(o)} → {_fmt(n)}" for f, o, n in changed)
				lines.append(f"~ {_label(meta, table)}: {_row_name(child, new) or name}, {details}")
	return lines


def last_submission_time(doc):
	"""When this document last went to Pending Approval (from its version history), or None."""
	for v in frappe.get_all(
		"Version",
		filters={"ref_doctype": doc.doctype, "docname": doc.name},
		fields=["data", "creation"],
		order_by="creation desc",
	):
		for change in json.loads(v.data).get("changed", []):
			if change[0] == "workflow_state" and change[2] == PENDING:
				return v.creation
	return None


def change_summary(doc, since, until=None) -> list[str]:
	"""Net content changes saved between `since` and `until`, as readable lines:
	"+ Charges: Service Charges, Rate / Amount 1,700" / "- ..." / "~ Rate per Unit: 80 → 75"."""
	filters = [["ref_doctype", "=", doc.doctype], ["docname", "=", doc.name], ["creation", ">", since]]
	if until:
		filters.append(["creation", "<=", until])
	versions = frappe.get_all("Version", filters=filters, fields=["data"], order_by="creation asc")
	meta = frappe.get_meta(doc.doctype)
	fields, added, removed, row_changed = {}, {}, {}, {}
	for v in versions:
		data = json.loads(v.data)
		for fieldname, old, new in data.get("changed", []):
			if fieldname in APPROVAL_FIELDS or meta.get_field(fieldname) is None or meta.get_field(fieldname).hidden:
				continue
			if fieldname in fields:
				fields[fieldname][1] = new
			else:
				fields[fieldname] = [old, new]
		for table, row in data.get("added", []):
			if table not in APPROVAL_FIELDS:
				added[(table, row.get("name"))] = dict(row)
		for table, row in data.get("removed", []):
			key = (table, row.get("name"))
			if table in APPROVAL_FIELDS:
				continue
			if key in added:
				added.pop(key)  # added and removed again: no net change
			else:
				removed[key] = row
		for table, _idx, rowname, changes in data.get("row_changed", []):
			if table in APPROVAL_FIELDS:
				continue
			key = (table, rowname)
			if key in added:
				for fieldname, _old, new in changes:
					added[key][fieldname] = new
				continue
			per_row = row_changed.setdefault(key, {})
			for fieldname, old, new in changes:
				if fieldname in per_row:
					per_row[fieldname][1] = new
				else:
					per_row[fieldname] = [old, new]

	lines = []
	for fieldname, (old, new) in fields.items():
		if cstr(old) != cstr(new):
			lines.append(f"~ {_label(meta, fieldname)}: {_fmt(old)} → {_fmt(new)}")
	for (table, _name), row in added.items():
		lines.append(f"+ {_label(meta, table)}: {_row_text(meta, table, row)}")
	for (table, _name), row in removed.items():
		lines.append(f"- {_label(meta, table)}: {_row_text(meta, table, row)}")
	for (table, rowname), changes in row_changed.items():
		real = [(f, o, n) for f, (o, n) in changes.items() if cstr(o) != cstr(n)]
		if not real:
			continue
		child = frappe.get_meta(meta.get_field(table).options)
		current = next((r for r in doc.get(table) or [] if r.name == rowname), None)
		row_label = _row_name(child, current.as_dict() if current else {}) or rowname
		details = "; ".join(f"{_label(child, f)} {_fmt(o)} → {_fmt(n)}" for f, o, n in real)
		lines.append(f"~ {_label(meta, table)}: {row_label}, {details}")
	return lines


def _label(meta, fieldname):
	df = meta.get_field(fieldname)
	return _(df.label) if df and df.label else fieldname


def _fmt(value):
	if value in (None, ""):
		return "(blank)"
	if isinstance(value, (int, float)):
		return f"{value:,.2f}".rstrip("0").rstrip(".")
	return cstr(value)


def _row_name(child_meta, row):
	for df in child_meta.fields:
		if df.in_list_view and df.fieldtype in ("Link", "Data", "Select") and row.get(df.fieldname):
			return cstr(row.get(df.fieldname))
	return ""


def _row_text(meta, table, row):
	child = frappe.get_meta(meta.get_field(table).options)
	parts = [_row_name(child, row) or "row"]
	for df in child.fields:
		if df.in_list_view and df.fieldtype in ("Currency", "Float", "Int", "Percent") and row.get(df.fieldname) is not None:
			parts.append(f"{_(df.label)} {_fmt(flt(row.get(df.fieldname)))}")
	return ", ".join(parts)


def clear_notifications(doc):
	"""Once someone acts on a document (approve, reject, withdraw, resubmit, cancel), the notifications about it
	are out of date: mark them read for everyone, and tell the affected users' open browsers to refresh their
	counter (user request, 2026-11-14). The notification about the new state is created after this, so it stays
	unread until it is opened."""
	filters = {"document_type": doc.doctype, "document_name": doc.name, "read": 0}
	users = frappe.get_all("Notification Log", filters=filters, pluck="for_user", distinct=True)
	if not users:
		return
	frappe.db.set_value("Notification Log", filters, "read", 1, update_modified=False)
	for user in users:
		frappe.publish_realtime("ub_notifications_changed", user=user, after_commit=True)


def log(doc, old, new, remarks=None):
	if not doc.is_new():
		clear_notifications(doc)
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
		role = (required_roles(doc) or [approver_role])[0]  # the level the amount needs
		users = [u for u in users_with_role(role) if u in enabled and u != actor]
		subject = _("{0} {1} is waiting for your approval").format(label, frappe.bold(title))
		if doc.get("changes_since_last_submission"):  # set only when there was a previous submission
			changes = doc.changes_since_last_submission.splitlines()
			shown = "; ".join(changes[:3]) + (f" (+{len(changes) - 3} more)" if len(changes) > 3 else "")
			subject += ". " + _("Changes since last submission: {0}").format(shown)
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


@frappe.whitelist()
def set_cancellation_reason(doctype: str, name: str, reason: str):
	"""Store the reason for reversing an approved document just before the Cancel action is applied (FS-RUN-06)."""
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	if not doc.meta.get_field("cancellation_reason"):
		frappe.throw(_("This document doesn't keep a reversal reason."))
	if doc.get("workflow_state") != APPROVED:
		frappe.throw(_("Only an approved document can be reversed."))
	from frappe.model.workflow import get_transitions

	if not any(t.action == "Cancel" for t in get_transitions(doc)):
		frappe.throw(_("You can't reverse this document."), frappe.PermissionError)
	reason = (reason or "").strip()
	if len(reason) < 5:
		frappe.throw(_("Write a reason of at least 5 characters."))
	doc.db_set("cancellation_reason", reason, update_modified=False)
