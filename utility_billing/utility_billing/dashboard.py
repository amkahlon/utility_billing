# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Data for the home dashboard page (modern UI; FS-RPT-01). One call returns everything the page shows, with the
numbers already formatted, so the page only draws and the figures can be tested here.

Figures the system can't produce yet (line losses, service requests) are returned as "not recorded yet" cards, never
as zeros: they need the DISCO bill and the service request workflow."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, cstr, flt, format_time, formatdate, get_last_day, getdate, now_datetime, nowdate

from utility_billing.utility_billing import access, losses, mis
from utility_billing.utility_billing import rights

ALLOWED = set(mis.FINANCE + mis.BILLING + mis.RECOVERY + ["Society Admin"])
MODES = ("Cash", "Cheque", "Pay Order", "Card", "Bank Transfer", "1Link", "Kuickpay")
GOOD, WARN, BAD, MUTED = "green", "amber", "red", "slate"


def rs(value) -> str:
	v = flt(value)
	return f"Rs {v / 1_000_000:,.1f}M" if abs(v) >= 1_000_000 else f"Rs {v:,.0f}"


def signed_rs(value) -> str:
	v = flt(value)
	return ("+" if v >= 0 else "−") + rs(abs(v))


def signed_pct(value) -> str:
	return ("+" if flt(value) >= 0 else "−") + f"{abs(flt(value)):.1f}%"


def latest_billing_month(society):
	m = frappe.db.get_value("Utility Bill", {"society": society, "docstatus": 1}, "billing_month", order_by="billing_month desc")
	return mis.month_start(m or nowdate())


def default_society(societies) -> str:
	"""The society that has the latest posted bill among those the user may see, so the dashboard opens on live figures;
	else the newest society. Judged against the user's societies, not the picker's list, which holds at most 200."""
	filters = {"docstatus": 1}
	mine = access.societies_of()
	if mine is not None:
		filters["society"] = ["in", mine]
	name = frappe.db.get_value("Utility Bill", filters, "society", order_by="creation desc")
	return name or societies[0].name


def picker(societies, society) -> list[dict]:
	"""The society picker's entries; the society on screen is always one of them, even past the list's 200."""
	rows = [{"name": s.name, "label": s.society_name or s.name} for s in societies]
	if not any(r["name"] == society for r in rows):
		rows.insert(0, {"name": society, "label": frappe.db.get_value("Society", society, "society_name") or society})
	return rows


def data_label(society) -> str | None:
	"""The amber pill beside the society: it warns that the figures are not a real society's."""
	name = (society or "").upper()
	if name == "DEMO":
		return _("Demo data")
	if name.startswith(("QA", "QO", "TST")):
		return _("Test data")
	return None


# ---------------------------------------------------------------- pieces
def kpi(label, value, note, tone=MUTED, raw=None):
	return {"label": label, "value": value, "note": note, "tone": tone, "raw": raw}


LOSS_LABEL, ATC_LABEL = "Line losses", "Line losses (AT&C)"


def collection_due(society, month, today):
	"""The due date of the month's posted bill run while it hasn't passed yet; None once collections can be counted
	(or there is no posted run)."""
	due = frappe.db.get_value("Bill Run", {"society": society, "billing_month": mis.month_start(month), "docstatus": 1}, "due_date")
	return getdate(due) if due and getdate(today) <= getdate(due) else None


def line_loss_card(society, month, series, today=None):
	"""AT&C for the month when the DISCO bill (or feeder input units) is recorded; otherwise an honest placeholder.
	Until the bills fall due, AT&C would read close to 100% only because nothing has been paid yet, so the card shows the
	line loss instead and says when AT&C follows."""
	cur = losses.at_c_for(society, month, series[-1])
	if not cur:
		return kpi(_(ATC_LABEL), "—", _("Needs the DISCO bill, not recorded yet"), MUTED)
	watch, high = losses.levels(society)
	tone = losses.TONE[losses.status_of(cur["input_kwh"], cur["billed_kwh"], watch, high)]
	due = collection_due(society, month, today or nowdate())
	if due and cur["loss_pct"] is not None:
		return {**kpi(_(LOSS_LABEL), f"{cur['loss_pct']:.1f}%", _("AT&C after the due date, {0}").format(formatdate(due)), tone, cur["loss_pct"]),
		        "drill": "Line Losses by Feeder and Transformer"}
	if cur["atc"] is None:
		return kpi(_(ATC_LABEL), "—", _("Can't be worked out: nothing was billed"), MUTED)
	prev = losses.at_c_for(society, add_months(month, -1), series[-2]) if len(series) > 1 else None
	if prev and prev["atc"] is not None:
		delta = round(cur["atc"] - prev["atc"], 1)
		note = _("{0} pts vs. last month").format(("+" if delta >= 0 else "−") + f"{abs(delta):.1f}")
	else:
		note = _("Line loss {0}%").format(f"{cur['loss_pct']:.1f}")
	return kpi(_(ATC_LABEL), f"{cur['atc']:.1f}%", note, tone, cur["atc"])


def defaulter_stats(society, as_of, aged=None):
	"""(accounts with something 60+ days overdue, the amount that is 60+ days overdue). `aged`: mis.aging_for(..., detail=True)
	when the caller has it (the same figures as the Defaulters report: the age floored at 0)."""
	count, amount = 0, 0.0
	for _acc, parts in (mis.aging_for(society, as_of, detail=True) if aged is None else aged):
		old = [p[1] for p in parts if max(p[0], 0) >= 60]
		if old:
			count += 1
			amount += sum(old)
	return count, amount


class Figures:
	"""What several cards share, worked out once per call (increment 61: the dashboard read every account's balances four times
	and aged them twice, 18.8 s on 21,446 accounts)."""

	def __init__(self, society, month, as_of):
		self.society, self.month, self.as_of = society, month, getdate(as_of)
		self.rows = mis.account_rows(society, as_of)
		self.aged = mis.aging_for(society, as_of, rows=self.rows, detail=True)
		self.defaulters = defaulter_stats(society, as_of, self.aged)
		self.before = mis.balance_totals(society, add_days(month, -1))

	@property
	def arrears(self):
		return sum(flt(a.arrears_balance) + flt(a.surcharge_balance) + flt(a.fees_balance) for a in self.rows)

	@property
	def owed(self):
		return sum(flt(a.balance) for a in self.rows if flt(a.balance) > 0)

	@property
	def credit(self):
		return -sum(flt(a.balance) for a in self.rows if flt(a.balance) < 0)


AGE_BANDS = ("Not yet due", "0-30 days", "31-60 days", "61-90 days", "91-180 days", "Over 180 days", "Before go-live")


def ageing_of(fig) -> dict:
	"""What consumers owe by how long it has been due: a band for bills not due yet, the report's five bands, and the part brought
	over at go-live whose age the old system didn't give (the Arrears Ageing report counts both of these as 0-30 days)."""
	amounts, accounts = dict.fromkeys(AGE_BANDS, 0.0), {b: set() for b in AGE_BANDS}
	for acc, parts in fig.aged:
		for age, amount, opening in parts:
			band = AGE_BANDS[6] if opening else AGE_BANDS[0] if age < 0 else (
				AGE_BANDS[1 + mis.BANDS.index(mis.band_of(age))])
			amounts[band] += flt(amount)
			accounts[band].add(acc.connection)
	total = sum(amounts.values())
	bands = [{"label": _(b), "key": b, "amount": round(amounts[b], 2), "value": rs(amounts[b]), "accounts": len(accounts[b]),
	          "pct": round(mis.pct(amounts[b], total), 1)} for b in AGE_BANDS]
	return {"bands": bands, "total": rs(total), "total_raw": round(total, 2), "credit": rs(fig.credit), "credit_raw": round(fig.credit, 2),
	        "owing": sum(1 for a in fig.rows if flt(a.balance) > 0), "in_credit": sum(1 for a in fig.rows if flt(a.balance) < 0)}


def mix_of(society, month) -> list[dict]:
	"""The month's bills by charge line, largest first (taxes are their own lines)."""
	rows = mis.sql("""select l.label, sum(l.amount) amount from `tabBill Line` l join `tabUtility Bill` b on b.name = l.parent
		where b.society = %(s)s and b.docstatus = 1 and b.billing_month = %(m)s and l.parenttype = 'Utility Bill'
		group by l.label having sum(l.amount) != 0 order by sum(l.amount) desc""", {"s": society, "m": month})
	total = sum(flt(r.amount) for r in rows)
	return [{"name": r.label, "value": rs(r.amount), "raw": flt(r.amount), "pct": round(mis.pct(r.amount, total), 1)} for r in rows]


def categories_of(society, month) -> list[dict]:
	"""The month's bills by consumer category: bills, units and current billing, in category name order (so each keeps its colour)."""
	rows = mis.sql("""select ifnull(cc.category_name, b.consumer_category) name, count(*) bills, sum(b.units) units, sum(b.current_amount) amount
		from `tabUtility Bill` b left join `tabConsumer Category` cc on cc.name = b.consumer_category
		where b.society = %(s)s and b.docstatus = 1 and b.billing_month = %(m)s group by 1 order by 1""", {"s": society, "m": month})
	total = sum(flt(r.amount) for r in rows)
	return [{"name": r.name or _("(none)"), "bills": cint(r.bills), "units": flt(r.units), "value": rs(r.amount), "raw": flt(r.amount),
	         "pct": round(mis.pct(r.amount, total), 1)} for r in rows]


def delta(now, before, up_is_good=True, points=False):
	"""A change against last month for a headline card: {text, dir: up | down | flat, good: True | False | None}. A percentage
	change, or with `points` the difference in percentage points (for a rate)."""
	now, before = flt(now), flt(before)
	if not before and not points:
		return None
	change = now - before if points else (now - before) / abs(before) * 100
	if abs(change) < 0.05:
		return {"text": _("No change"), "dir": "flat", "good": None}
	up = change > 0
	text = _("{0} pts").format(("+" if up else "−") + f"{abs(change):.1f}") if points else signed_pct(change)
	return {"text": text, "dir": "up" if up else "down", "good": up == up_is_good}


def headline_of(society, month, series, fig, cards) -> list[dict]:
	"""The four figures the dashboard leads with: billed, collected, collection efficiency and what consumers owe, each with its
	change against last month and its 12-month trend."""
	cur, prev = series[-1], series[-2] if len(series) > 1 else {}
	by = {c["icon"]: c for c in cards}
	rate = cur["rate"] if cur["bills"] else None
	prev_name = getdate(add_months(month, -1)).strftime("%b")
	vs = _("vs. {0}").format(prev_name)
	return [
		{"key": "billed", "label": _("Billed this cycle"), "value": rs(cur["billed"]), "raw": cur["billed"],
		 "sub": (_("1 bill") if cur["bills"] == 1 else _("{0} bills").format(f"{cur['bills']:,}")) if cur["bills"] else _("No bills in this cycle"),
		 "delta": delta(cur["billed"], prev.get("billed")), "vs": vs, "spark": [m["billed"] for m in series], "drill": "Bill Register"},
		{"key": "collected", "label": _("Collected"), "value": rs(cur["collected"]), "raw": cur["collected"],
		 "sub": _("of {0} payable").format(rs(cur["payable"])) if cur["payable"] else _("Nothing payable yet"),
		 "delta": delta(cur["collected"], prev.get("collected")), "vs": vs, "spark": [m["collected"] for m in series], "drill": "Collections"},
		{"key": "efficiency", "label": _("Collection efficiency"), "value": f"{rate:.1f}%" if rate is not None else "—", "raw": rate,
		 "sub": _("Target {0}%").format(TARGET), "gauge": rate, "target": TARGET,
		 "tone": None if rate is None else GOOD if rate >= TARGET else WARN if rate >= 60 else BAD,
		 "delta": delta(rate, prev.get("rate"), points=True) if rate is not None and prev.get("bills") else None, "vs": vs,
		 "spark": [m["rate"] for m in series], "drill": "Collection vs Billing by Category and Block"},
		{"key": "receivable", "label": _("Owed by consumers"), "value": rs(fig.owed), "raw": fig.owed,
		 "sub": _("of which arrears {0}").format(by["arrears"]["value"]) if by.get("arrears") else "",
		 "delta": delta(fig.owed, fig.before.owed, up_is_good=False), "vs": _("vs. 1 {0}").format(getdate(month).strftime("%b")),
		 "drill": "Arrears Ageing"},
	]


TARGET = 85  # collection efficiency a society aims for (%); the card turns green from here, amber from 60


def connections_card(society, as_of):
	"""How many connections were active on the day (user, 2026-11-24: "dashboard should also display total no of active
	connections"). One disconnected since then still counts for an earlier month; one only applied for doesn't count."""
	v = {"s": society, "d": getdate(as_of)}
	started = "status != 'Applied' and (connection_date is null or connection_date <= %(d)s)"
	active = frappe.db.sql(f"""select count(*) from `tabConnection` where society = %(s)s and {started}
	                           and (status = 'Active' or (disconnected_on is not null and disconnected_on > %(d)s))""", v)[0][0]
	off = frappe.db.sql(f"""select count(*) from `tabConnection` where society = %(s)s and {started} and status = 'Disconnected'
	                        and (disconnected_on is null or disconnected_on <= %(d)s)""", v)[0][0]
	return kpi(_("Active connections"), f"{active:,}", _("{0} disconnected").format(f"{off:,}") if off else _("None disconnected"), MUTED, active)


def kpis_of(society, month, series, as_of, fig=None, today=None):
	cur, prev = series[-1], series[-2] if len(series) > 1 else None
	prev_month_name = getdate(add_months(month, -1)).strftime("%B")
	billed_note = _("No bills last month")
	if prev and prev["billed"]:
		billed_note = _("{0} vs. {1}").format(signed_pct((cur["billed"] - prev["billed"]) / prev["billed"] * 100), prev_month_name)
	rate = cur["rate"]

	fig = fig or Figures(society, month, as_of)
	arrears = fig.arrears
	delta = arrears - fig.before.arrears

	defaulters, overdue_60 = fig.defaulters

	defective = frappe.get_all("Meter", filters={"society": society, "status": "Defective"}, pluck="modified")
	stale = sum(1 for m in defective if (getdate(as_of) - getdate(m)).days > 60)
	return [
		connections_card(society, as_of),
		kpi(_("Billed this cycle"), rs(cur["billed"]), billed_note, MUTED, cur["billed"]),
		kpi(_("Collected so far"), rs(cur["collected"]),
		    _("{0}% collection efficiency").format(f"{rate:.1f}") if cur["bills"] else _("No bills in this cycle"),
		    (GOOD if rate >= 85 else WARN if rate >= 60 else BAD) if cur["bills"] else MUTED, cur["collected"]),
		kpi(_("Total arrears"), rs(arrears), _("{0} vs. last month").format(signed_rs(delta)) if round(delta) else _("No change vs. last month"),
		    BAD if round(delta) > 0 else GOOD, arrears),
		line_loss_card(society, month, series, today),
		kpi(_("Units billed"), f"{cur['units']:,.0f} kWh", (_("1 connection billed") if cur["bills"] == 1 else _("{0} connections billed").format(f"{cur['bills']:,}")), MUTED, cur["units"]),
		kpi(_("Defaulters (60+ days)"), f"{defaulters:,}", _("{0} outstanding").format(rs(overdue_60)),
		    BAD if defaulters else GOOD, defaulters),
		kpi(_("Defective meters"), f"{len(defective):,}", _("{0} not updated for 2+ months").format(stale), WARN if stale else MUTED, len(defective)),
		service_card(society, as_of),
	]


def service_card(society, as_of):
	"""Open service requests and how many are past their service standard (FS-WF-00)."""
	rows = frappe.get_all("Service Request", filters={"society": society, "status": ["in", ["Open", "In Progress"]]}, pluck="due_by")
	late = sum(1 for d in rows if d and getdate(d) < getdate(as_of))
	note = _("{0} past the service standard").format(late) if late else (_("All within the service standard") if rows else _("Nothing open"))
	return kpi(_("Open service requests"), f"{len(rows):,}", note, WARN if late else (GOOD if rows else MUTED), len(rows))


SEVERITY = {"red": 0, "amber": 1, "blue": 2}


def attention_of(society, month, as_of, fig=None):
	"""What needs somebody's attention now, most serious first, each with where to go. Plain rules over the live data (no AI):
	the place the AI insights planned in notes/ai-analytics-roadmap.md will appear next to."""
	items = []

	def add(tone, title, detail, route, options=None):
		items.append({"tone": tone, "title": title, "detail": detail, "route": route, "options": options or {}})

	cycle = frappe.db.get_value("Reading Cycle", {"society": society, "billing_month": month}, ["name", "status", "flagged_readings"], as_dict=True)
	run = frappe.db.exists("Bill Run", {"society": society, "billing_month": month, "docstatus": ["<", 2]})
	if cycle and cint(cycle.flagged_readings):
		add("amber", _("{0} flagged readings need review").format(cint(cycle.flagged_readings)), _("Reading cycle {0}").format(cycle.name),
		    ["List", "Meter Reading"], {"reading_cycle": cycle.name, "status": "Flagged"})
	if cycle and cycle.status == "Closed" and not run:
		add("blue", _("Readings are closed: start the bill run"), _("Cycle {0}").format(cycle.name), ["List", "Bill Run"])
	if run and frappe.db.get_value("Bill Run", run, "docstatus") == 1 and not frappe.db.get_value("Bill Run", run, "reversal_status"):
		from utility_billing.utility_billing import notify  # noqa: PLC0415

		counts = notify.message_counts(run)
		if counts["missing"] and not notify.writing_now(run):  # increment 62: their consumers weren't told of the bill
			add("amber", _("{0} posted bills have no bill message").format(f"{counts['missing']:,}"),
			    _("Bill run {0}: write them from the run").format(run), ["Form", "Bill Run", run])

	waiting = mis.REPORTS["pending_approvals"]["fn"](frappe._dict({"society": society}))["data"]
	if waiting:
		oldest = max(r["age"] for r in waiting)
		add("blue", _("{0} items waiting for approval").format(len(waiting)), _("The oldest has waited {0} days").format(oldest),
		    ["query-report", "Pending Approvals"], {"society": society})

	for r in losses.network_losses(society, month, "Feeder"):
		if r["status"] == losses.HIGH:
			add("red", _("{0} is losing {1}% of its energy").format(r["name"], f"{r['loss_pct']:.1f}"),
			    _("{0} kWh in, {1} kWh billed").format(f"{r['input_kwh']:,.0f}", f"{r['billed_kwh']:,.0f}"),
			    ["query-report", "Line Losses by Feeder and Transformer"], {"society": society, "month": str(month)})
	if not losses.disco_bill(society, month) and frappe.db.exists("Utility Bill", {"society": society, "billing_month": month, "docstatus": 1}):
		add("blue", _("Enter the DISCO bill to see line losses"), _("No DISCO bill is recorded for this month"), ["Form", "DISCO Bill", "new"],
		    {"society": society, "billing_month": str(month)})

	count, amount = fig.defaulters if fig else defaulter_stats(society, as_of)
	if count:
		add("red", _("{0} accounts are 60+ days overdue").format(f"{count:,}"), _("{0} outstanding").format(rs(amount)),
		    ["ub-recovery"], {"society": society})
	estimated = mis.REPORTS["estimated_bills"]["fn"](frappe._dict({"society": society, "month": str(month), "repeat_only": 1}))["data"]
	if estimated:
		add("amber", _("{0} consumers billed on estimates 2+ months running").format(len(estimated)), _("They need a real reading"),
		    ["query-report", "Estimated Bills"], {"society": society, "month": str(month), "repeat_only": 1})
	from utility_billing.utility_billing import revenue_assurance  # noqa: PLC0415

	ra_open, ra_risk = revenue_assurance.open_findings(society)
	if ra_open:
		add("red" if ra_risk else "amber", _("{0} revenue assurance findings are open").format(ra_open),
		    _("{0} of revenue at risk").format(rs(ra_risk)) if ra_risk else _("Assign and investigate them"), ["List", "RA Finding"],
		    {"society": society, "status": ["!=", "Closed"]})
	closed = frappe.db.count("Cashier Day", {"society": society, "status": "Closed"})
	if closed:
		add("blue", _("{0} cashier days are waiting for approval").format(closed), _("Closed with a cash count"), ["List", "Cashier Day"],
		    {"society": society, "status": "Closed"})
	orders = frappe.db.count("Recovery Notice", {"society": society, "status": "Awaiting Approval"})
	if orders:
		add("amber", _("{0} disconnection orders are waiting for approval").format(orders), _("A second user must approve each order"),
		    ["List", "Recovery Notice"], {"society": society, "status": "Awaiting Approval"})
	held = frappe.db.count("Suspense Payment", {"society": society, "status": "Held"})
	if held:
		add("amber", _("{0} payments are held in suspense").format(held), _("Money received with no matching account"), ["List", "Suspense Payment"],
		    {"society": society, "status": "Held"})
	failed = frappe.db.count("Message Log", {"society": society, "status": "Failed", "creation": [">=", add_days(as_of, -7)]})
	if failed:
		add("amber", _("{0} messages failed in the last 7 days").format(failed), _("Check the Messaging Provider and the consumers' numbers"),
		    ["List", "Message Log"], {"society": society, "status": "Failed"})
	unmatched = frappe.db.count("Settlement File", {"society": society, "status": "Matched"})
	if unmatched:
		add("blue", _("{0} settlement files are waiting to be closed").format(unmatched), _("Rows to post or explain"), ["List", "Settlement File"],
		    {"society": society, "status": "Matched"})
	items.sort(key=lambda i: SEVERITY[i["tone"]])
	return items[:6]


def steps_of(society, month, today):
	"""The five steps of the billing cycle, each done, current or coming (the first unfinished one is current)."""
	cycle = frappe.db.get_value("Reading Cycle", {"society": society, "billing_month": month},
	                            ["name", "status", "total_readings", "pending_readings", "flagged_readings"], as_dict=True)
	run = frappe.db.get_value("Bill Run", {"society": society, "billing_month": month, "docstatus": ["<", 2]},
	                          ["name", "docstatus", "workflow_state", "issue_date", "due_date", "approved_by", "approved_on"], as_dict=True)
	posted = bool(run and run.docstatus == 1)
	grace = cint(frappe.db.get_value("Society", society, "grace_days"))
	steps = []

	total = cint(cycle.total_readings) if cycle else 0
	pending = cint(cycle.pending_readings) if cycle else 0
	read = total - pending
	steps.append({"title": _("Readings"), "done": bool(cycle and total and not pending),
	              "detail": _("{0} of {1} read ({2}%)").format(f"{read:,}", f"{total:,}", f"{mis.pct(read, total):.1f}") if cycle and total
	              else _("No reading cycle yet")})
	flagged = cint(cycle.flagged_readings) if cycle else 0
	steps.append({"title": _("Reading review"), "done": bool(cycle and total and not pending and not flagged),
	              "detail": _("{0} flagged readings waiting").format(flagged) if flagged else
	              (_("All readings reviewed") if cycle and total and not pending else _("Waiting for the readings"))})
	if posted:
		who = frappe.db.get_value("User", run.approved_by, "full_name") if run.approved_by else None
		detail = _("{0}, approved by {1}").format(formatdate(run.issue_date), who or run.approved_by) if run.approved_by else formatdate(run.issue_date)
	elif run:
		detail = _("Bill run {0}: {1}").format(run.name, _(run.workflow_state or "Draft"))
	else:
		detail = _("Not started")
	steps.append({"title": _("Bill run posted"), "done": posted, "detail": detail})
	if posted:
		issue, due = getdate(run.issue_date), getdate(run.due_date)
		if today <= due:
			d = max((today - issue).days + 1, 1)
			detail = _("Day {0} of {1} before due date").format(d, max((due - issue).days, 1))
		else:
			detail = _("Closed on the due date, {0}").format(formatdate(due))
		steps.append({"title": _("Collection period"), "done": today > due, "detail": detail})
		surcharged = frappe.db.exists("Utility Bill", {"society": society, "billing_month": month, "docstatus": 1,
		                                               "surcharge_status": ["in", ["Posted", "Not charged (paid in time)", "Reversed (paid in time)"]]})
		steps.append({"title": _("Surcharge and defaulter run"), "done": bool(surcharged),
		              "detail": _("Ran after the due date") if surcharged else _("Scheduled {0}").format(formatdate(add_days(due, grace + 1)))})
	else:
		steps.append({"title": _("Collection period"), "done": False, "detail": _("Starts when the bills are posted")})
		steps.append({"title": _("Surcharge and defaulter run"), "done": False, "detail": _("After the due date")})
	current = next((i for i, s in enumerate(steps) if not s["done"]), None)
	for i, s in enumerate(steps):
		s["state"] = "done" if s["done"] else "now" if i == current else "next"
		s["mark"] = "✓" if s["done"] else str(i + 1)
	footer = _("Due date {0}").format(formatdate(run.due_date)) if posted else None
	return steps, footer


def feeders_of(society, month):
	"""Loss per feeder: input from the DISCO bill's feeder rows, billed from the bills; "—" where nothing is recorded."""
	return [{"name": r["name"], "connections": r["connections"], "input_kwh": f"{r['input_kwh']:,.0f}" if r["input_kwh"] else "—",
	         "billed_kwh": f"{r['billed_kwh']:,.0f}", "loss": f"{r['loss_pct']:.1f}%" if r["loss_pct"] is not None else "—",
	         "status": _(r["status"]), "tone": r["tone"]} for r in losses.network_losses(society, month, "Feeder")]


def channels_of(society, month):
	rows = mis.sql("""select mode, sum(amount) amount from `tabPayment Receipt` where society = %(s)s and docstatus = 1
		and payment_date between %(a)s and %(b)s group by mode""", {"s": society, "a": month, "b": get_last_day(month)})
	total = sum(flt(r.amount) for r in rows)
	out = [{"name": _(r.mode), "amount": rs(r.amount), "pct": round(mis.pct(r.amount, total)), "raw": flt(r.amount)} for r in rows]
	return sorted(out, key=lambda c: -c["raw"])


ICONS = ("connections", "billed", "collected", "arrears", "losses", "units", "defaulters", "meters", "service")


def cards_of(society, month, series, as_of, fig=None, today=None):
	"""The nine cards with an icon each and, where there is a monthly history, a sparkline of the last 12 months."""
	sparks = {1: [m["billed"] for m in series], 2: [m["collected"] for m in series], 5: [m["units"] for m in series]}
	cards = kpis_of(society, month, series, as_of, fig, today)
	for i, card in enumerate(cards):
		card["icon"] = ICONS[i]
		card["spark"] = sparks.get(i) if any(sparks.get(i) or []) else None
	return cards


# ---------------------------------------------------------------- the society's figures, kept for a while
CACHE_SECONDS = 6 * 3600
FIGURES_VERSION = 2  # raise when the figures' shape changes
# A change to any of these makes the kept figures stale (a posting, a payment, a reading, a DISCO bill ...). Other changes (an
# approval waiting, a failed message) show at the next Refresh, or the next day (the key carries the date).
WATCHED = (("Customer Ledger Entry", "creation"), ("Utility Bill", "modified"), ("Bill Run", "modified"), ("Reading Cycle", "modified"),
           ("Meter Reading", "modified"), ("DISCO Bill", "modified"), ("Payment Receipt", "modified"), ("Service Request", "modified"),
           ("Meter", "modified"), ("Connection", "modified"), ("Message Log", "creation"))


def data_version() -> str:
	return "|".join(cstr(frappe.db.sql(f"select max(`{f}`) from `tab{dt}`")[0][0]) for dt, f in WATCHED)


def figures_for(society, month, refresh=False, allow_stale=False) -> dict:
	"""Everything on the dashboard that is the society's, not the viewer's. Kept in the cache while none of the WATCHED records
	changes, so a second visit opens at once (increment 61: 18.8 s every time before). `allow_stale` (the page): figures kept from
	before a change come back at once marked "stale", and the page asks again for fresh ones. Tests work it out fresh."""
	month = mis.month_start(month)
	today = getdate(nowdate())
	as_of = min(today, getdate(get_last_day(month)))
	use_cache = not frappe.flags.in_test or frappe.flags.ub_dashboard_cache
	# FIGURES_VERSION: figures kept by an older release (other keys) are never handed to a newer page
	key, version = f"ub-dashboard:{FIGURES_VERSION}:{society}:{month}:{today}", data_version()
	if use_cache and not refresh:
		kept = frappe.cache.get_value(key)
		if kept and kept.get("version") == version:
			return {**kept["data"], "cached": True, "stale": False}
		if kept and allow_stale:
			return {**kept["data"], "cached": True, "stale": True}
	series = mis.monthly_series(society, mis.last_months(month, 12))
	fig = Figures(society, month, as_of)
	cards = cards_of(society, month, series, as_of, fig, today)
	steps, footer = steps_of(society, month, today)
	feeders = feeders_of(society, month)
	now = now_datetime()
	data = {
		"kpis": cards, "headline": headline_of(society, month, series, fig, cards), "ageing": ageing_of(fig),
		"mix": mix_of(society, month), "categories": categories_of(society, month),
		"attention": attention_of(society, month, as_of, fig), "series": series, "steps": steps, "footer": footer,
		"feeders": feeders, "feeders_have_input": any(f["input_kwh"] != "—" for f in feeders), "channels": channels_of(society, month),
		"updated": str(now), "updated_label": format_time(now, "HH:mm"),
	}
	if use_cache:
		frappe.cache.set_value(key, {"version": version, "data": data}, expires_in_sec=CACHE_SECONDS)
	return {**data, "cached": False, "stale": False}


# ---------------------------------------------------------------- the call
@frappe.whitelist()
def get_dashboard(society: str | None = None, month: str | None = None, refresh: int | str | None = None,
                  allow_stale: int | str | None = None):
	if not rights.can("dashboard.view"):
		raise frappe.PermissionError(_("You don't have access to the dashboard."))
	societies = access.visible_societies(order_by="creation desc")
	if not societies:
		return {"empty": True, "message": _("Create a society first.")}
	access.check_society(society)
	society = society or default_society(societies)
	if not frappe.db.exists("Society", society):
		frappe.throw(_("Society {0} not found.").format(society))
	month = mis.month_start(month or latest_billing_month(society))
	today = getdate(nowdate())
	energy = frappe.db.get_value("Society", society, "enable_electricity")
	full_name = frappe.db.get_value("User", frappe.session.user, "full_name")
	return {
		"society": society, "society_name": frappe.db.get_value("Society", society, "society_name"),
		"societies": picker(societies, society),
		"data_label": data_label(society), "month": str(month), "month_label": getdate(month).strftime("%B %Y"),
		"months": [{"value": str(m), "label": getdate(m).strftime("%B %Y")} for m in mis.last_months(month if month > today else today, 13)][::-1],
		"subtitle": _("{0} · {1} billing cycle").format(_("Electricity") if energy else _("Billing"), getdate(month).strftime("%B %Y")),
		**figures_for(society, month, cint(refresh), cint(allow_stale)),
		"user": {"name": frappe.session.user, "full_name": full_name,
		         "initials": "".join(w[0] for w in (full_name or "U").split()[:2]).upper()},
	}
