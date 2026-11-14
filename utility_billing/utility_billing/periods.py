# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Month-end close and period lock (FS-RUN-11, FS-ACC-10).

A Billing Period is one calendar month of one society's transactions. Closing it shows a checklist (runs approved,
readings resolved, counters closed, bank statements matched, ledger reconciled); once closed, no ledger entry can be
dated in it: a late payment, fee or bill is refused, and a correction goes through an adjustment dated in the open
period. Reopening is for a General Manager, with a reason, and only the latest closed month can be reopened.
"""

import calendar

import frappe
from frappe import _
from frappe.utils import add_days, add_months, getdate, nowdate

CLOSERS = {"System Manager", "Billing Manager", "General Manager", "Accounts Officer"}
REOPENERS = {"System Manager", "General Manager"}
PENDING_DOCTYPES = ("Account Adjustment", "Refund", "Bulk Adjustment", "Write Off", "Installment Plan", "Bill Revision",
                    "Due Date Extension", "Bill Run", "Tariff", "Tariff Adjustment")
CACHE = "ub_closed_periods"


def bounds(date):
	"""First and last day of the calendar month of `date`."""
	d = getdate(date)
	return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _cache() -> dict:
	if not hasattr(frappe.local, CACHE):
		setattr(frappe.local, CACHE, {})
		frappe.db.after_rollback.add(clear_cache)  # a rolled-back close or reopen must not linger
	return getattr(frappe.local, CACHE)


def clear_cache():
	if hasattr(frappe.local, CACHE):
		delattr(frappe.local, CACHE)


def is_closed(society, date) -> bool:
	start = bounds(date)[0]
	cache = _cache()
	key = (society, str(start))
	if key not in cache:
		cache[key] = bool(frappe.db.exists("Billing Period", {"society": society, "period_start": start, "status": "Closed"}))
	return cache[key]


def assert_open(society, date, what=None):
	"""Refuse to change a closed period: the entry has to be dated in the open period instead."""
	if society and date and is_closed(society, date):
		start = bounds(date)[0]
		frappe.throw(_("{0} is closed for {1}: nothing can be dated in {2}. Post it in the open period (for example as an adjustment).").format(
			_(what) if what else _("The period"), society, start.strftime("%B %Y")), title=_("Period closed"))


def next_open_date(society, date):
	"""`date`, or the first day of the next open month when its month is closed (for postings the system dates itself)."""
	d = getdate(date)
	while is_closed(society, d):
		d = add_months(bounds(d)[0], 1)
	return d


# ---------------------------------------------------------------- the checklist
def _names(rows, limit=5):
	rows = list(rows)
	more = f" (+{len(rows) - limit} more)" if len(rows) > limit else ""
	return ", ".join(rows[:limit]) + more


def checks(society, start, end) -> list[dict]:
	"""The month-end checklist: [{check, result, detail}] with result Passed, Warning or Failed. Only a Failed one stops the close."""
	out = []

	def add(check, result, detail):
		out.append({"check": check, "result": result, "detail": detail})

	runs = frappe.get_all("Bill Run", filters={"society": society, "issue_date": ["between", [start, end]], "docstatus": ["<", 2]},
	                      fields=["name", "docstatus", "reading_cycle"])
	open_runs = [r.name for r in runs if r.docstatus == 0]
	posted = [r for r in runs if r.docstatus == 1]
	if open_runs:
		add(_("Bill runs approved"), "Failed", _("Not yet approved: {0}.").format(_names(open_runs)))
	elif not posted:
		add(_("Bill runs approved"), "Warning", _("No bill run was issued in this month."))
	else:
		add(_("Bill runs approved"), "Passed", _("{0} bill run(s) approved.").format(len(posted)))

	cycles = [r.reading_cycle for r in runs if r.reading_cycle]
	open_cycles = frappe.get_all("Reading Cycle", filters={"name": ["in", cycles or [""]], "status": "Open"}, pluck="name")
	unresolved = frappe.get_all("Meter Reading", filters={"reading_cycle": ["in", cycles or [""]],
	                                                      "status": ["in", ["Pending", "Flagged", "Re-read Requested"]]}, pluck="name")
	if open_cycles or unresolved:
		bits = []
		if open_cycles:
			bits.append(_("reading cycle(s) not closed: {0}").format(_names(open_cycles)))
		if unresolved:
			bits.append(_("{0} reading(s) pending, flagged or waiting for a re-read").format(len(unresolved)))
		add(_("Readings resolved"), "Failed", "; ".join(bits).capitalize() + ".")
	else:
		add(_("Readings resolved"), "Passed", _("Every reading of this month's runs is resolved."))

	days = frappe.get_all("Cashier Day", filters={"society": society, "business_date": ["between", [start, end]]}, fields=["name", "status"])
	still_open = [d.name for d in days if d.status == "Open"]
	unapproved = [d.name for d in days if d.status == "Closed"]
	if still_open:
		add(_("Counters closed"), "Failed", _("Still open: {0}.").format(_names(still_open)))
	elif unapproved:
		add(_("Counters closed"), "Warning", _("Closed but not yet approved: {0}.").format(_names(unapproved)))
	else:
		add(_("Counters closed"), "Passed", _("{0} cashier day(s), all closed and approved.").format(len(days)))

	files = frappe.get_all("Settlement File", filters={"society": society, "file_date": ["between", [start, end]]}, pluck="name")
	loose = []
	if files:
		for row in frappe.get_all("Settlement Row", filters={"parent": ["in", files], "result": ["!=", "Matched"]},
		                          fields=["parent", "action_taken"]):
			if not row.action_taken:
				loose.append(row.parent)
	if loose:
		add(_("Bank statements matched"), "Failed", _("{0} row(s) of {1} not matched or dealt with.").format(len(loose), _names(sorted(set(loose)))))
	else:
		add(_("Bank statements matched"), "Passed", _("{0} settlement file(s) checked.").format(len(files)))

	pending = []
	for dt in PENDING_DOCTYPES:
		meta = frappe.get_meta(dt)
		if not (meta.has_field("society") and meta.has_field("workflow_state")):
			continue
		filters = {"society": society, "workflow_state": "Pending Approval", "creation": ["<=", f"{end} 23:59:59"]}
		pending += [f"{dt} {n}" for n in frappe.get_all(dt, filters=filters, pluck="name")]
	if pending:
		add(_("Approvals waiting"), "Warning", _("{0} document(s) still pending approval: {1}.").format(len(pending), _names(pending)))
	else:
		add(_("Approvals waiting"), "Passed", _("Nothing is waiting for approval."))

	from utility_billing.utility_billing import gl  # noqa: PLC0415

	rec = gl.reconcile(society, save=False)
	if rec.status == "Difference":
		add(_("Ledger reconciled"), "Failed", _("Customer accounts and journals differ by Rs {0} that no unjournaled entry explains.").format(f"{rec.unexplained:,.2f}"))
	elif rec.status == "Timing differences":
		add(_("Ledger reconciled"), "Warning", _("{0} ledger entries (Rs {1}) are not yet in a journal.").format(rec.not_journaled_count, f"{rec.not_journaled_amount:,.2f}"))
	else:
		add(_("Ledger reconciled"), "Passed", _("Customer accounts agree with the receivables in the journals."))

	earlier = frappe.get_all("Billing Period", filters={"society": society, "status": "Open", "period_start": ["<", start]}, pluck="period_start")
	if earlier:
		add(_("Earlier months closed"), "Failed", _("Close {0} first.").format(_names(sorted(getdate(e).strftime("%B %Y") for e in earlier))))
	else:
		add(_("Earlier months closed"), "Passed", _("No earlier month is open."))
	return out


# ---------------------------------------------------------------- close and reopen
def _doc(name):
	return frappe.get_doc("Billing Period", name)


@frappe.whitelist()
def preview(period: str):
	"""The checklist as it stands now, without closing."""
	doc = _doc(period)
	doc.check_permission("read")
	return checks(doc.society, doc.period_start, doc.period_end)


@frappe.whitelist(methods=["POST"])
def close(period: str):
	"""Run the checklist and close the month when nothing failed."""
	if not set(frappe.get_roles()) & CLOSERS:
		raise frappe.PermissionError(_("Only a Billing Manager, Accounts Officer or General Manager can close a month."))
	doc = _doc(period)
	if doc.status == "Closed":
		frappe.throw(_("{0} is already closed.").format(doc.period_title))
	if getdate(doc.period_end) >= getdate(nowdate()):
		frappe.throw(_("{0} isn't over yet; close it after its last day.").format(doc.period_title))
	rows = checks(doc.society, doc.period_start, doc.period_end)
	doc.set("checklist", rows)
	failed = [r for r in rows if r["result"] == "Failed"]
	if failed:  # nothing is saved: the refusal lists what to put right
		frappe.throw(_("{0} can't be closed yet:<br>{1}").format(doc.period_title, "<br>".join(f"{r['check']}: {r['detail']}" for r in failed)),
		             title=_("Checklist not clear"))
	doc.status = "Closed"
	doc.closed_by, doc.closed_on = frappe.session.user, frappe.utils.now_datetime()
	doc.append("history", {"action": "Closed", "by_user": frappe.session.user, "on": frappe.utils.now_datetime()})
	doc.flags.ignore_permissions = True
	doc.save()
	clear_cache()
	return {"status": doc.status, "checklist": rows}


@frappe.whitelist(methods=["POST"])
def reopen(period: str, reason: str):
	if not set(frappe.get_roles()) & REOPENERS:
		raise frappe.PermissionError(_("Only a General Manager can reopen a closed month."))
	if len((reason or "").strip()) < 10:
		frappe.throw(_("Give the reason for reopening (at least 10 characters)."))
	doc = _doc(period)
	if doc.status != "Closed":
		frappe.throw(_("{0} is not closed.").format(doc.period_title))
	later = frappe.get_all("Billing Period", filters={"society": doc.society, "status": "Closed", "period_start": [">", doc.period_start]},
	                       pluck="period_start", order_by="period_start")
	if later:
		frappe.throw(_("{0} is closed too. Reopen the later months first, the latest one first.").format(getdate(later[-1]).strftime("%B %Y")))
	year_end = frappe.db.exists("Year End Close", {"society": doc.society, "status": "Closed", "year_start": ["<=", doc.period_start], "year_end": [">=", doc.period_end]})
	if year_end:
		frappe.throw(_("The year was closed in {0}; it can't be reopened month by month.").format(year_end))
	doc.status = "Open"
	doc.closed_by = doc.closed_on = None
	doc.append("history", {"action": "Reopened", "by_user": frappe.session.user, "on": frappe.utils.now_datetime(), "remarks": reason.strip()})
	doc.flags.ignore_permissions = True
	doc.save()
	clear_cache()
	return {"status": doc.status}


def last_day_before_today():
	return add_days(nowdate(), -1)
