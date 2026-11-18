# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Access (FS-ACC-01, 06, 07, 11, 12): where each user works, the approval inbox with bulk actions, reminders and escalation of waiting
approvals, delegation during leave, and hiding CNIC and phone numbers from roles that don't need them.

- User Access saves the user's societies and cash counters as Frappe User Permissions, so every list, report and link only shows those
  societies' records (a head-office user can be given all societies).
- Approval Delegation lends the approver roles of someone away to another user for the dates; maker-checker still applies.
- Personal data: CNIC and phone fields are on permission level 1; roles without it see them masked in pages and reports.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate, nowdate

APPROVER_ROLES = ("Billing Manager", "General Manager", "Management")
ADMINS = {"System Manager", "Society Admin"}
APPROVAL_DOCTYPES = ("Tariff", "Tariff Adjustment", "Bill Run", "Account Adjustment", "Refund", "Bill Revision", "Bulk Adjustment",
                     "Installment Plan", "Write Off", "Security Deposit", "Bill Template", "Solar Rebate Pool")


# ---------------------------------------------------------------- where users work (FS-ACC-01)
def sync_user_permissions(ua):
	"""Make the user's User Permissions for Society and Cash Counter match their User Access."""
	wanted = set()
	if not cint(ua.all_societies):
		wanted |= {("Society", r.society) for r in ua.societies}
	wanted |= {("Cash Counter", r.cash_counter) for r in ua.counters}
	have = {(p.allow, p.for_value): p.name for p in frappe.get_all("User Permission", filters={"user": ua.user, "allow": ["in", ["Society", "Cash Counter"]]},
	                                                                fields=["name", "allow", "for_value"])}
	for key, name in have.items():
		if key not in wanted:
			frappe.delete_doc("User Permission", name, ignore_permissions=True)
	for allow, value in wanted - set(have):
		frappe.get_doc({"doctype": "User Permission", "user": ua.user, "allow": allow, "for_value": value, "apply_to_all_doctypes": 1}).insert(
			ignore_permissions=True)
	frappe.clear_cache(user=ua.user)


def societies_of(user=None) -> list[str] | None:
	"""The societies a user is limited to, or None when not limited."""
	user = user or frappe.session.user
	rows = frappe.get_all("User Permission", filters={"user": user, "allow": "Society"}, pluck="for_value")
	return rows or None


def check_society(society, user=None):
	"""A user limited to some societies (User Access) can't read or act on another society's data, whatever a screen or a
	request sends (review 2026-11-16: reports and pages trusted the society in the request)."""
	if not society:
		return
	mine = societies_of(user)
	wanted = list(society) if isinstance(society, (list, tuple, set)) else [society]
	refused = [s for s in wanted if s and mine is not None and s not in mine]
	if refused:
		raise frappe.PermissionError(_("You don't have access to society {0}.").format(", ".join(refused)))


def visible_societies(fields=("name", "society_name"), order_by="society_name", limit=200):
	"""The societies this user may see, for a page's society picker."""
	mine = societies_of()
	filters = {"name": ["in", mine]} if mine is not None else {}
	return frappe.get_all("Society", filters=filters, fields=list(fields), order_by=order_by, limit=limit)


def check_record_society(doctype, name):
	"""The society of a record looked up by name, checked as above."""
	if name and frappe.get_meta(doctype).has_field("society"):
		check_society(frappe.db.get_value(doctype, name, "society"))


def blocks_of(user=None, society=None) -> list[str] | None:
	user = user or frappe.session.user
	if not frappe.db.exists("User Access", user):
		return None
	filters = {"parent": user, "parenttype": "User Access"}
	if society:
		filters["society"] = society
	rows = frappe.get_all("Access Block", filters=filters, pluck="block")
	return rows or None


# ---------------------------------------------------------------- personal data (FS-ACC-11)
PERSONAL_FIELDS = ("mobile_no", "phone", "id_number", "cnic")


def sees_personal(user=None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return 1 in frappe.get_meta("Consumer").get_permlevel_access("read", user=user)


def mask(value) -> str | None:
	"""35202-1234567-1 → 35202-*******-1; 0300-1234567 → 0300-***4567."""
	if not value:
		return value
	s = str(value)
	digits = [i for i, ch in enumerate(s) if ch.isdigit()]
	if len(digits) <= 4:
		return "*" * len(s)
	keep = set(digits[:4] + digits[-3:]) if len(digits) > 10 else set(digits[-4:])
	return "".join(ch if (not ch.isdigit() or i in keep) else "*" for i, ch in enumerate(s))


def mask_rows(rows, user=None):
	"""Mask personal fields in report rows (dicts) for users who don't see them."""
	if sees_personal(user):
		return rows
	for r in rows or []:
		if isinstance(r, dict):
			for k in PERSONAL_FIELDS:
				if r.get(k):
					r[k] = mask(r[k])
	return rows


# ---------------------------------------------------------------- approval inbox (FS-ACC-06)
def _pending(doctype):
	if not frappe.db.table_exists(doctype) or not frappe.get_meta(doctype).has_field("workflow_state"):
		return []
	fields = ["name", "modified", "owner"] + [f for f in ("society", "requested_by", "requested_on") if frappe.get_meta(doctype).has_field(f)]
	title = frappe.get_meta(doctype).title_field
	if title:
		fields.append(title)
	return [(doctype, r, title) for r in frappe.get_list(doctype, filters={"workflow_state": "Pending Approval", "docstatus": 0}, fields=fields,
	                                                      order_by="modified asc", limit=500)]


@frappe.whitelist()
def inbox():
	"""What the user can approve now: pending documents of the societies they work in where Approve is open to them."""
	from frappe.model.workflow import get_transitions  # noqa: PLC0415

	out = []
	for dt in APPROVAL_DOCTYPES:
		for doctype, r, title in _pending(dt):
			doc = frappe.get_doc(doctype, r.name)
			actions = sorted({t.action for t in get_transitions(doc)} & {"Approve", "Reject"})
			if "Approve" not in actions or (r.get("requested_by") or r.owner) == frappe.session.user:
				continue
			from utility_billing.utility_billing import approval  # noqa: PLC0415

			turn = approval.level_state(doc)
			if turn["in_turn"] and (turn["next_role"] not in frappe.get_roles() or frappe.session.user in {d["user"] for d in turn["done"]}):
				continue  # approvals in turn: not this user's level yet (or already approved by them)
			since = r.get("requested_on") or r.modified
			out.append({"doctype": doctype, "name": r.name, "title": r.get(title) if title else r.name, "society": r.get("society"),
			            "requested_by": r.get("requested_by") or r.owner, "since": str(since), "days": date_diff(nowdate(), getdate(since)), "actions": actions})
	return sorted(out, key=lambda x: -x["days"])


@frappe.whitelist(methods=["POST"])
def bulk(items: str, action: str, reason: str | None = None):
	"""Approve or reject (return to the maker) several documents at once; each is checked on its own and a refusal doesn't stop the rest."""
	from frappe.model.workflow import apply_workflow  # noqa: PLC0415

	from utility_billing.utility_billing import approval  # noqa: PLC0415

	if action not in ("Approve", "Reject"):
		frappe.throw(_("Choose Approve or Reject."))
	if action == "Reject" and len((reason or "").strip()) < 5:
		frappe.throw(_("Give the reason for rejecting (it goes back to whoever made it)."))
	done, failed = [], []
	for it in frappe.parse_json(items) or []:
		frappe.db.savepoint("bulk_item")
		try:
			doc = frappe.get_doc(it["doctype"], it["name"])
			if action == "Reject":
				approval.set_rejection_reason(doc.doctype, doc.name, reason)
				doc.reload()
			if action == "Approve":
				approval.approve(doc)  # at the next level when the society approves in turn
			else:
				apply_workflow(doc, action)
			done.append(it["name"])
		except Exception as e:  # each document stands alone
			frappe.db.rollback(save_point="bulk_item")
			failed.append({"name": it.get("name"), "error": frappe.utils.strip_html(str(e))[:200]})
			frappe.clear_messages()
	return {"done": done, "failed": failed}


# ---------------------------------------------------------------- reminders and escalation (FS-ACC-07)
def _notify(users, doc, subject):
	for u in users:
		if frappe.db.exists("Notification Log", {"for_user": u, "document_type": doc.doctype, "document_name": doc.name, "subject": subject}):
			continue
		frappe.get_doc({"doctype": "Notification Log", "for_user": u, "type": "Alert", "document_type": doc.doctype, "document_name": doc.name,
		                "subject": subject, "from_user": "Administrator"}).insert(ignore_permissions=True)


def remind_and_escalate(as_of=None):
	"""Daily: remind the approvers of items waiting longer than the society's reminder days, and tell the escalation role after more days."""
	from utility_billing.utility_billing import approval  # noqa: PLC0415

	as_of = getdate(as_of or nowdate())
	counts = {"reminded": 0, "escalated": 0}
	for dt in APPROVAL_DOCTYPES:
		if not frappe.db.table_exists(dt) or not frappe.get_meta(dt).has_field("workflow_state"):
			continue
		for name in frappe.get_all(dt, filters={"workflow_state": "Pending Approval", "docstatus": 0}, pluck="name"):
			doc = frappe.get_doc(dt, name)
			society = doc.get("society")
			soc = frappe.get_cached_doc("Society", society) if society else None
			since = getdate(doc.get("requested_on") or doc.modified)
			days = date_diff(as_of, since)
			remind, escalate = cint(soc.remind_after_days if soc else 2) or 2, cint(soc.escalate_after_days if soc else 5) or 5
			title = doc.get(frappe.get_meta(dt).title_field or "name") or doc.name
			if days >= escalate:
				role = (soc.escalate_to_role if soc else None) or "Management"
				_notify(approval.users_with_role(role), doc, _("Escalated: {0} {1} has waited {2} days for approval").format(_(dt), title, days))
				counts["escalated"] += 1
			elif days >= remind:
				roles = approval.required_roles(doc) or [approval.DEFAULT_APPROVER.get(dt, "General Manager")]
				_notify(approval.users_with_role(roles[0]), doc, _("Reminder: {0} {1} is waiting for your approval ({2} days)").format(_(dt), title, days))
				counts["reminded"] += 1
	return counts


# ---------------------------------------------------------------- delegation (FS-ACC-07)
def approver_roles() -> list[str]:
	"""The roles that approve something on any active workflow (lent during a delegation), as the Workflow screens say."""
	rows = frappe.db.sql("""select distinct t.allowed from `tabWorkflow Transition` t join `tabWorkflow` w on w.name = t.parent
	                       where w.is_active = 1 and t.action = 'Approve'""")
	return [r[0] for r in rows] or list(APPROVER_ROLES)


def roles_of(user):
	return set(frappe.get_roles(user))


def start_delegation(d):
	lend = [r for r in approver_roles() if r in roles_of(d.delegator) and r not in roles_of(d.delegate)]
	user = frappe.get_doc("User", d.delegate)
	for r in lend:
		user.append("roles", {"role": r})
	user.flags.ignore_permissions = True
	user.save()
	d.db_set({"status": "Active", "roles_lent": json.dumps(lend)})


def end_delegation(d, status="Ended"):
	lent = json.loads(d.roles_lent or "[]")
	if lent:
		user = frappe.get_doc("User", d.delegate)
		keep = [r for r in user.roles if r.role not in lent or other_delegation_lends(d, r.role)]
		user.set("roles", keep)
		user.flags.ignore_permissions = True
		user.save()
	d.db_set({"status": status})


def other_delegation_lends(d, role):
	for o in frappe.get_all("Approval Delegation", filters={"delegate": d.delegate, "status": "Active", "name": ["!=", d.name]}, fields=["roles_lent"]):
		if role in json.loads(o.roles_lent or "[]"):
			return True
	return False


def run_delegations(as_of=None):
	"""Daily: start the delegations whose first day has come and end those past their last day."""
	as_of = getdate(as_of or nowdate())
	for name in frappe.get_all("Approval Delegation", filters={"status": "Scheduled", "from_date": ["<=", as_of], "to_date": [">=", as_of]}, pluck="name"):
		start_delegation(frappe.get_doc("Approval Delegation", name))
	for name in frappe.get_all("Approval Delegation", filters={"status": "Active", "to_date": ["<", as_of]}, pluck="name"):
		end_delegation(frappe.get_doc("Approval Delegation", name))


@frappe.whitelist(methods=["POST"])
def cancel_delegation(name: str):
	d = frappe.get_doc("Approval Delegation", name)
	d.check_permission("write")
	if d.status == "Active":
		end_delegation(d, "Cancelled")
	elif d.status == "Scheduled":
		d.db_set("status", "Cancelled")
	return d.status


def daily():
	remind_and_escalate()
	run_delegations()
