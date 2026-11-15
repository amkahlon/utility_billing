# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Defaulters and recovery (increment 11; FS-REC-02, 04, 05, 06, 07, 08, 15 and the recovery part of FS-RPT-24).

How it works
- A **defaulter** owes at least the society's `defaulter_min_amount` and has a charge overdue by at least
  `defaulter_min_days` (FS-REC-04). Recovery stages (reminder, notice, final notice, disconnection order) are
  issued to accounts owing at least that amount once their oldest overdue charge reaches the stage's days (FS-REC-05).
- A **Recovery Run** works out which notice each account is due, shows the list for review, and issues it. Issuing
  records each stage and date as a **Recovery Notice**. A disconnection order waits for a second person's approval.
- A **Recovery Case** assigns an account to a recovery officer, records follow-ups and promises to pay, and an
  open legal case or an unexpired promise stops the automatic steps (FS-REC-08, 15).
- A **Recovery Target** per month is compared with what was actually recovered (FS-REC-07).
Notices go out by print now. SMS and WhatsApp notices are queued with their text, and are sent when the
notifications increment adds a gateway (FS-REC-06, NTF)."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, formatdate, getdate, now_datetime, nowdate

from utility_billing.utility_billing import access, dashboard, flags, holders, mis, notify
from utility_billing.utility_billing import recovery_engine as engine

RUNNERS = ["Recovery Officer", "Billing Manager", "General Manager", "Society Admin"]
APPROVERS = ["Billing Manager", "General Manager"]
VIEWERS = set(mis.RECOVERY + RUNNERS + ["Billing Officer"])
ACTIVE_NOTICE = ("Awaiting Approval", "Issued", "Queued", "Sent", "Failed")

DEFAULT_STAGES = [
	("Reminder", 15, "Print", "Dear {consumer}, your electricity account {reference} has {owed} overdue. Please pay at your earliest."),
	("Notice", 30, "Print", "Dear {consumer}, account {reference} owes {owed}, overdue for {days} days. Please pay within 7 days."),
	("Final Notice", 45, "Print", "FINAL NOTICE: account {reference} owes {owed}, overdue for {days} days. Supply will be disconnected if not paid."),
	("Disconnection Order", 60, "Print", "Supply to account {reference} is ordered to be disconnected for non-payment of {owed}."),
]


def can(roles) -> bool:
	return bool(set(frappe.get_roles()) & set(roles)) or frappe.session.user == "Administrator"


def require(roles, what):
	if not can(roles):
		frappe.throw(_("You don't have permission to {0}.").format(what), frappe.PermissionError)


# ---------------------------------------------------------------- rules and state
def rules_of(society) -> frappe._dict:
	s = frappe.get_cached_doc("Society", society)
	stages = [(r.stage, cint(r.days_overdue), r.channel or "Print") for r in s.recovery_stages]
	messages = {r.stage: r.message for r in s.recovery_stages}
	return frappe._dict(min_amount=flt(s.defaulter_min_amount), min_days=cint(s.defaulter_min_days), stages=engine.ordered_rules(stages),
	                    messages=messages, society=s)


def open_cases(society) -> dict:
	return {c.connection: c for c in frappe.get_all(
		"Recovery Case", filters={"society": society, "status": ["!=", engine.CASE_CLOSED]},
		fields=["name", "connection", "status", "assigned_to", "promise_date", "promise_amount", "legal_case_no"])}


def notice_history(society) -> dict:
	"""{connection: [(stage, issue_date)]} of notices that count (not cancelled)."""
	hist = defaultdict(list)
	for n in frappe.get_all("Recovery Notice", filters={"society": society, "status": ["in", ACTIVE_NOTICE]},
	                        fields=["connection", "stage", "issue_date", "name"], order_by="issue_date, creation"):
		hist[n.connection].append((n.stage, getdate(n.issue_date)))
	return hist


def overdue_view(parts):
	overdue = [(a, amt) for a, amt in parts if a > 0]
	return round(sum(amt for _a, amt in overdue), 2), max((a for a, _amt in overdue), default=0)


def accounts(society, as_of=None, block=None, category=None, include_all=False) -> list[dict]:
	"""Accounts owing at least the defaulter amount with something overdue, with their stage position, hold and case.
	`include_all` also returns accounts below the amount (for the case screens)."""
	as_of = getdate(as_of or nowdate())
	rules = rules_of(society)
	cases, hist = open_cases(society), notice_history(society)
	plan_accounts = set(frappe.get_all("Installment Plan", filters={"society": society, "docstatus": 1, "plan_status": "Active"}, pluck="connection"))
	disputed = frappe.db.sql("""select connection, sum(disputed_amount) a from `tabBill Dispute` where society = %s and status = 'Open' group by connection""",
	                         society, as_dict=True)
	disputed = {d.connection: flt(d.a) for d in disputed}  # money in dispute is left out of recovery (FS-WF-10)
	former = holders.former_by_connection(society)
	flagged = flags.held(society, flags.HOLD_RECOVERY, as_of)
	see = access.sees_personal()
	blocks = access.blocks_of(society=society)  # a recovery officer limited to some blocks sees only those (FS-ACC-01)
	rows = []
	for r, parts in mis.aging_for(society, as_of, category, block):
		overdue, oldest = overdue_view(parts)
		r.balance = flt(r.balance) - disputed.get(r.connection, 0)
		r.balance -= max(former.get(r.connection, 0), 0)  # a former holder's debt isn't the current holder's (increment 22)
		if overdue <= 0 or r.balance <= 0:
			continue
		if blocks and (r.block or "") not in blocks:
			continue
		if not include_all and flt(r.balance) < rules.min_amount:
			continue
		# stages issued since the oldest overdue charge fell due belong to this stretch of arrears
		since = add_days(as_of, -oldest)
		done = [s for s, d in hist.get(r.connection, []) if d >= since]
		case = cases.get(r.connection)
		hold = engine.on_hold(case.status, case.promise_date, as_of) if case else None
		if not hold and r.connection in plan_accounts:
			hold = "Installment plan active"
		if not hold and r.connection in flagged:
			hold = _("Flag: {0}").format(flagged[r.connection])  # a flag that holds recovery (FS-MST-14)
		nxt = None if hold else engine.next_stage(rules.stages, oldest, done)
		rows.append({
			"connection": r.connection, "consumer_name": r.consumer_name, "property_no": r.property_no, "block": r.block,
			"category": r.category, "mobile_no": r.mobile_no if see else access.mask(r.mobile_no), "owed": flt(r.balance), "overdue": overdue, "oldest": oldest,
			"defaulter": engine.is_defaulter(r.balance, oldest, rules.min_amount, rules.min_days),
			"last_stage": next((s for s in reversed(engine.STAGES) if s in done), None), "stages_done": done,
			"next_stage": nxt[0] if nxt else None, "next_channel": nxt[1] if nxt else None, "hold": hold,
			"case": case.name if case else None, "case_status": case.status if case else None,
			"assigned_to": case.assigned_to if case else None,
			"promise": engine.promise_state(case.status, case.promise_date, as_of) if case else None,
			"promise_date": case.promise_date if case else None,
		})
	rows.sort(key=lambda x: (-x["owed"], -x["oldest"]))
	return rows


def recovered_in_month(society, month) -> float:
	"""What payments took off arrears, surcharge and fees in a month (not what was paid on the current bill).
	Reversed payments are left out."""
	first = mis.month_start(month)
	last = frappe.utils.get_last_day(first)
	row = mis.sql("""select -sum(d_arrears + d_surcharge + d_fees) v from `tabCustomer Ledger Entry`
	                 where society = %(s)s and entry_type = 'Payment' and posting_date between %(a)s and %(b)s
	                   and ifnull(reversed_by, '') = ''""", {"s": society, "a": first, "b": last})
	return round(max(flt(row[0].v) if row else 0, 0), 2)


def target_of(society, month):
	return frappe.db.get_value("Recovery Target", {"society": society, "month": mis.month_start(month)}, ["name", "target_amount"], as_dict=True)


def target_progress(society, month) -> dict:
	t = target_of(society, month)
	out = engine.progress(t.target_amount if t else 0, recovered_in_month(society, month))
	out["name"] = t.name if t else None
	return out


# ---------------------------------------------------------------- recovery run
def prepare_run(run):
	"""Fill a draft run with the notice each account is due."""
	if run.status not in (None, "", "Draft"):
		frappe.throw(_("Only a draft run can be prepared again."))
	rules = rules_of(run.society)
	if not rules.stages:
		frappe.throw(_("Set the recovery stages on the Society first (Defaulters and Recovery)."))
	rows = accounts(run.society, run.as_of, run.block or None, run.consumer_category or None)
	run.items = []
	held = 0
	for r in rows:
		if r["hold"]:
			held += 1
		if not r["next_stage"]:
			continue
		run.append("items", {"include": 1, "connection": r["connection"], "consumer_name": r["consumer_name"], "block": r["block"],
		                     "category": r["category"], "stage": r["next_stage"], "channel": r["next_channel"], "owed": r["owed"],
		                     "overdue": r["overdue"], "oldest_days": r["oldest"]})
	run.accounts_checked, run.accounts_due, run.accounts_on_hold = len(rows), len(run.items), held
	run.notices_issued = 0


def notice_message(rules, society_doc, stage, row_values) -> str:
	text = rules.messages.get(stage) or next((m for s, _d, _c, m in DEFAULT_STAGES if s == stage), "")
	return engine.render_message(text, **row_values)


def issue_run(run):
	"""Create the notices of the ticked rows. Stage dates are recorded; a disconnection order waits for approval."""
	require(RUNNERS, _("issue recovery notices"))
	if run.status not in (None, "", "Draft"):
		frappe.throw(_("This run is already {0}.").format(run.status.lower()))
	rows = [i for i in run.items if cint(i.include)]
	if not rows:
		frappe.throw(_("Tick at least one account, or prepare the run first."))
	rules = rules_of(run.society)
	as_of = getdate(run.as_of)
	issued = 0
	positions = {a["connection"]: a for a in accounts(run.society, as_of)}
	cases = open_cases(run.society)
	for item in rows:
		current = positions.get(item.connection)
		if not current or current["next_stage"] != item.stage:
			item.include, item.notice = 0, None  # the position changed since the list was prepared: skip, don't duplicate
			continue
		case = cases.get(item.connection)
		ref = frappe.db.get_value("Connection", item.connection, "reference_no") or item.connection
		message = notice_message(rules, rules.society, item.stage, {
			"consumer": item.consumer_name, "owed": f"Rs {flt(item.owed):,.0f}", "days": item.oldest_days,
			"society": rules.society.society_name, "reference": ref})
		if item.stage == engine.DISCONNECTION:
			status = "Awaiting Approval"
		else:
			status = "Queued" if item.channel in ("SMS", "WhatsApp") else "Issued"
		n = frappe.get_doc({
			"doctype": "Recovery Notice", "society": run.society, "connection": item.connection, "consumer_name": item.consumer_name,
			"block": item.block, "consumer_category": item.category, "stage": item.stage, "status": status, "issue_date": as_of,
			"channel": item.channel, "owed": item.owed, "overdue": item.overdue, "oldest_days": item.oldest_days, "message": message,
			"recovery_run": run.name, "recovery_case": case.name if case else None})
		n.insert(ignore_permissions=True)
		item.notice = n.name
		issued += 1
		if status == "Queued":
			notify.send_recovery_notice(n)  # SMS and WhatsApp go through the message queue (FS-REC-06, NTF)
		if case:
			frappe.db.set_value("Recovery Case", case.name, {"last_stage": item.stage, "last_stage_date": as_of}, update_modified=False)
	run.notices_issued = issued
	run.status, run.issued_by, run.issued_on = "Issued", frappe.session.user, now_datetime()
	return issued


def approve_disconnection(name):
	"""A disconnection order needs a second person: not whoever ran or created the run (maker-checker)."""
	require(APPROVERS, _("approve a disconnection order"))
	n = frappe.get_doc("Recovery Notice", name)
	if n.stage != engine.DISCONNECTION or n.status != "Awaiting Approval":
		frappe.throw(_("Only a disconnection order that is awaiting approval can be approved."))
	run_by = frappe.db.get_value("Recovery Run", n.recovery_run, "issued_by") if n.recovery_run else None
	if frappe.session.user in {n.owner, run_by}:
		frappe.throw(_("You issued this order, so another user must approve it (maker-checker)."), frappe.PermissionError)
	n.status, n.approved_by, n.approved_on = ("Queued" if n.channel in ("SMS", "WhatsApp") else "Issued"), frappe.session.user, now_datetime()
	n.save(ignore_permissions=True)
	if n.status == "Queued":
		notify.send_recovery_notice(n)
	return n


def cancel_notice(name, reason):
	require(RUNNERS, _("cancel a notice"))
	if not (reason or "").strip():
		frappe.throw(_("Give a reason for cancelling the notice."))
	n = frappe.get_doc("Recovery Notice", name)
	if n.status in ("Sent", "Cancelled"):
		frappe.throw(_("A notice that is {0} can't be cancelled.").format(n.status.lower()))
	n.status, n.cancel_reason = "Cancelled", reason.strip()
	n.save(ignore_permissions=True)
	return n


def mark_notice(name, status):
	"""Staff record the result of a queued SMS or WhatsApp notice until a gateway does it (notifications increment)."""
	require(RUNNERS, _("update a notice"))
	allowed = {"Queued": ("Sent", "Failed"), "Failed": ("Queued", "Sent")}
	n = frappe.get_doc("Recovery Notice", name)
	if status not in allowed.get(n.status, ()):
		frappe.throw(_("A {0} notice can't be changed to {1}.").format(n.status.lower(), status.lower()))
	n.status = status
	n.save(ignore_permissions=True)
	return n


# ---------------------------------------------------------------- cases
def assign_cases(society, connections, user):
	"""Open a case for each account that has none, and assign it. Accounts that already have an open case are reassigned."""
	require(RUNNERS, _("assign defaulters"))
	if not user or "Recovery Officer" not in frappe.get_roles(user):
		frappe.throw(_("{0} is not a Recovery Officer.").format(user or _("Choose a user")))
	cases = open_cases(society)
	made = changed = 0
	for c in connections:
		if frappe.db.get_value("Connection", c, "society") != society:
			frappe.throw(_("{0} doesn't belong to {1}.").format(c, society))
		if c in cases:
			frappe.db.set_value("Recovery Case", cases[c].name, "assigned_to", user)
			changed += 1
			continue
		frappe.get_doc({"doctype": "Recovery Case", "society": society, "connection": c, "assigned_to": user}).insert(ignore_permissions=True)
		made += 1
	return {"opened": made, "reassigned": changed}


# ---------------------------------------------------------------- the page
@frappe.whitelist()
def get_recovery(society: str | None = None, as_of: str | None = None, block: str | None = None, category: str | None = None,
                 show: str = "defaulters"):
	"""Everything the Defaulters and Recovery page shows, in one call."""
	require(VIEWERS, _("see recovery"))
	societies = frappe.get_all("Society", fields=["name", "society_name"], order_by="society_name")
	if not societies:
		return {"societies": [], "empty": True}
	society = society or dashboard.default_society(societies)
	as_of = getdate(as_of or nowdate())
	rules = rules_of(society)
	rows = accounts(society, as_of, block or None, category or None, include_all=False)
	defaulters = [r for r in rows if r["defaulter"]]
	shown = defaulters if show == "defaulters" else rows
	due = defaultdict(int)
	for r in rows:
		if r["next_stage"]:
			due[r["next_stage"]] += 1
	open_n = frappe.db.count("Recovery Case", {"society": society, "status": ["!=", engine.CASE_CLOSED]})
	month = mis.month_start(as_of)
	issued_month = frappe.db.sql("""select stage, count(*) n from `tabRecovery Notice` where society = %s and status in %s
	                                and issue_date between %s and %s group by stage""",
	                             (society, ACTIVE_NOTICE, month, frappe.utils.get_last_day(month)), as_dict=True)
	progress = target_progress(society, month)
	# the mock-up's figures and columns (2026-11-16): last payment, a stage label per account, plans and missed installments
	names = [r["connection"] for r in shown[:500]] or [""]
	last_paid = {x.connection: x for x in frappe.db.sql(
		"""select connection, max(payment_date) payment_date from `tabPayment Receipt` where docstatus = 1 and connection in %s group by connection""",
		(names,), as_dict=True)}
	for r in shown[:500]:
		r["last_payment"] = str(last_paid[r["connection"]].payment_date) if r["connection"] in last_paid else None
		r["stage_label"], r["stage_tone"] = stage_label(r)
	plans = frappe.db.sql("""select count(distinct p.name) plans, count(distinct case when s.status = 'Missed' then p.name end) missed
	                         from `tabInstallment Plan` p left join `tabInstallment Row` s on s.parent = p.name
	                         where p.society = %s and p.docstatus = 1 and p.plan_status = 'Active'""", society, as_dict=True)[0]
	return {
		"society": society, "societies": societies, "as_of": str(as_of), "show": show, "block": block or "", "category": category or "",
		"data_label": dashboard.data_label(society),
		"rules": {"min_amount": rules.min_amount, "min_days": rules.min_days, "stages": [{"stage": s, "days": d, "channel": c} for s, d, c in rules.stages]},
		"cards": {
			"defaulters": len(defaulters), "defaulter_amount": round(sum(r["owed"] for r in defaulters), 2),
			"overdue_accounts": len(rows), "notices_due": sum(due.values()), "due_by_stage": dict(due),
			"on_hold": sum(1 for r in rows if r["hold"]), "broken_promises": sum(1 for r in rows if r["promise"] == "Broken"),
			"open_cases": open_n, "issued_this_month": {x.stage: cint(x.n) for x in issued_month},
			"outstanding": round(sum(r["owed"] for r in rows), 2), "plans": cint(plans.plans), "missed_installments": cint(plans.missed)},
		"progress": progress, "month_label": month.strftime("%B %Y"),
		"blocks": sorted({r["block"] for r in mis.account_rows(society, as_of) if r.block}),
		"categories": frappe.get_all("Consumer Category", pluck="name", order_by="name"),
		"officers": frappe.get_all("Has Role", filters={"role": "Recovery Officer", "parenttype": "User"}, pluck="parent"),
		"rows": shown[:500], "truncated": len(shown) > 500, "total_rows": len(shown),
		"can_run": can(RUNNERS), "can_target": can(["Billing Manager", "General Manager", "Society Admin"]),
	}


def stage_label(r) -> tuple[str, str]:
	"""(label, tone) of where an account stands, as the mock-up's Stage column shows it."""
	if r.get("promise") == "Broken":
		return _("Broken promise"), "red"
	if r.get("hold") == "Installment plan active":
		return _("Installment plan"), "blue"
	if r.get("hold"):
		return _("On hold"), "blue"
	if r.get("next_stage") == "Disconnection Order":
		return _("For disconnection"), "red"
	if r.get("last_stage"):
		return _("{0} sent").format(_(r["last_stage"])), "amber"
	if r.get("next_stage"):
		return _("{0} due").format(_(r["next_stage"])), "amber"
	return _("No action yet"), ""


@frappe.whitelist(methods=["POST"])
def new_run(society: str, as_of: str | None = None, block: str | None = None, consumer_category: str | None = None, connections=None):
	"""Create a draft recovery run and fill it, for the page's "Run recovery stages" button; with `connections`, for those accounts only
	(the selection bar's "Send notice"). The run is reviewed before anything is issued."""
	require(RUNNERS, _("run recovery stages"))
	run = frappe.get_doc({"doctype": "Recovery Run", "society": society, "as_of": as_of or nowdate(), "block": block,
	                      "consumer_category": consumer_category})
	prepare_run(run)
	wanted = set(frappe.parse_json(connections) if isinstance(connections, str) else (connections or []))
	if wanted:
		run.set("items", [i for i in run.items if i.connection in wanted])
		run.accounts_due = len(run.items)
		if not run.items:
			frappe.throw(_("None of the selected accounts is due a notice yet (each stage waits its number of days)."))
	run.insert()
	return run.name


@frappe.whitelist(methods=["POST"])
def disconnection_requests(society: str, connections):
	"""The selection bar's "Create disconnection orders": a Disconnection service request per account (reason Defaulter), so the notice
	period and the approval step still apply. Accounts with one already open are skipped."""
	require(RUNNERS, _("create disconnection requests"))
	from utility_billing.utility_billing import servicereq  # noqa: PLC0415

	servicereq.ensure_types(society)
	made, skipped = [], []
	for c in frappe.parse_json(connections) if isinstance(connections, str) else connections:
		if frappe.db.get_value("Connection", c, "society") != society or frappe.db.exists(
				"Service Request", {"connection": c, "kind": servicereq.DISC, "status": ["in", ["Open", "In Progress"]]}):
			skipped.append(c)
			continue
		sr = frappe.get_doc({"doctype": "Service Request", "request_type": f"{society}-DISC", "connection": c, "disconnection_reason": "Defaulter",
		                     "description": _("Raised from Defaulters and Recovery")})
		sr.insert()
		made.append(sr.name)
	return {"made": made, "skipped": skipped}


@frappe.whitelist(methods=["POST"])
def assign(society: str, connections, user: str):
	return assign_cases(society, frappe.parse_json(connections), user)


@frappe.whitelist()
def case_of(connection: str):
	"""Open the account's recovery case, creating an unassigned one if needed."""
	require(RUNNERS, _("open a recovery case"))
	society = frappe.db.get_value("Connection", connection, "society")
	c = open_cases(society).get(connection)
	if c:
		return c.name
	return frappe.get_doc({"doctype": "Recovery Case", "society": society, "connection": connection}).insert(ignore_permissions=True).name


@frappe.whitelist(methods=["POST"])
def approve_order(name: str):
	return approve_disconnection(name).name


@frappe.whitelist(methods=["POST"])
def cancel_notice_api(name: str, reason: str):
	return cancel_notice(name, reason).name


@frappe.whitelist(methods=["POST"])
def mark_notice_api(name: str, status: str):
	return mark_notice(name, status).name


# ---------------------------------------------------------------- notice print (FS-REC-06)
def notice_context(n):
	from utility_billing.utility_billing.account_print import society_header
	from utility_billing.utility_billing.bill_print import date, money

	conn = frappe.db.get_value("Connection", n.connection, ["reference_no", "property"], as_dict=True) or {}
	prop = frappe.db.get_value("Property", conn.get("property"), ["property_no", "block"], as_dict=True) if conn.get("property") else {}
	titles = {"Reminder": _("Payment Reminder"), "Notice": _("Notice of Arrears"), "Final Notice": _("Final Notice"),
	          "Disconnection Order": _("Disconnection Order")}
	return {"n": n, "soc": society_header(n.society, n.issue_date or n.creation, "Notice"), "money": money, "date": date, "title": titles.get(n.stage, n.stage),
	        "reference": conn.get("reference_no") or n.connection, "property": prop or {},
	        "watermark": _("CANCELLED") if n.status == "Cancelled" else (_("AWAITING APPROVAL") if n.status == "Awaiting Approval" else None)}


def notice_html(names):
	pages = []
	for name in names:
		n = frappe.get_doc("Recovery Notice", name)
		pages.append(frappe.render_template("utility_billing/templates/recovery/notice.html", notice_context(n)))
	return '<div style="page-break-after: always;"></div>'.join(pages)


NOTICE_PDF = {"page-size": "A4", "margin-top": "12mm", "margin-bottom": "12mm", "margin-left": "14mm", "margin-right": "14mm", "encoding": "UTF-8"}
MAX_NOTICES_PER_PDF = 500


@frappe.whitelist()
def download_notices(run: str | None = None, notice: str | None = None):
	"""PDF of one notice, or of every printable notice of a run (cancelled ones and SMS-only ones are left out)."""
	from frappe.utils.pdf import get_pdf

	from utility_billing.utility_billing.account_print import wrap
	from utility_billing.utility_billing.bill_print import send_pdf

	if notice:
		frappe.get_doc("Recovery Notice", notice).check_permission("print")
		names = [notice]
	else:
		frappe.get_doc("Recovery Run", run).check_permission("print")
		names = frappe.get_all("Recovery Notice", filters={"recovery_run": run, "channel": "Print", "status": ["in", ("Issued", "Awaiting Approval")]},
		                       pluck="name", order_by="block, connection")
	if not names:
		frappe.throw(_("There is nothing to print."))
	if len(names) > MAX_NOTICES_PER_PDF:
		frappe.throw(_("{0} notices is too many for one file (limit {1}). Filter the run by block or category.").format(len(names), MAX_NOTICES_PER_PDF))
	send_pdf(get_pdf(wrap(notice_html(names), run or notice), options=dict(NOTICE_PDF)), f"{run or notice}-notices.pdf")

