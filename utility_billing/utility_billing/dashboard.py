# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Data for the home dashboard page (modern UI; FS-RPT-01). One call returns everything the page shows, with the
numbers already formatted, so the page only draws and the figures can be tested here.

Figures the system can't produce yet (line losses, service requests) are returned as "not recorded yet" cards, never
as zeros: they need the DISCO bill and the service request workflow."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, formatdate, get_last_day, getdate, nowdate

from utility_billing.utility_billing import losses, mis

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
	"""The society that has the latest posted bill, so the dashboard opens on live figures; else the newest society."""
	name = frappe.db.get_value("Utility Bill", {"docstatus": 1}, "society", order_by="creation desc")
	return name if name and any(s.name == name for s in societies) else societies[0].name


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


def line_loss_card(society, month, series):
	"""AT&C for the month when the DISCO bill (or feeder input units) is recorded; otherwise an honest placeholder."""
	label = _("Line losses (AT&C)")
	cur = losses.at_c_for(society, month, series[-1])
	if not cur:
		return kpi(label, "—", _("Needs the DISCO bill, not recorded yet"), MUTED)
	if cur["atc"] is None:
		return kpi(label, "—", _("Can't be worked out: nothing was billed"), MUTED)
	prev = losses.at_c_for(society, add_months(month, -1), series[-2]) if len(series) > 1 else None
	if prev and prev["atc"] is not None:
		delta = round(cur["atc"] - prev["atc"], 1)
		note = _("{0} pts vs. last month").format(("+" if delta >= 0 else "−") + f"{abs(delta):.1f}")
	else:
		note = _("Line loss {0}%").format(f"{cur['loss_pct']:.1f}")
	watch, high = losses.levels(society)
	tone = losses.TONE[losses.status_of(cur["input_kwh"], cur["billed_kwh"], watch, high)]
	return kpi(label, f"{cur['atc']:.1f}%", note, tone, cur["atc"])


def defaulter_stats(society, as_of):
	"""(accounts with something 60+ days overdue, the amount that is 60+ days overdue)."""
	count, amount = 0, 0.0
	for _acc, parts in mis.aging_for(society, as_of):
		old = [a for age, a in parts if age >= 60]
		if old:
			count += 1
			amount += sum(old)
	return count, amount


def kpis_of(society, month, series, as_of):
	cur, prev = series[-1], series[-2] if len(series) > 1 else None
	prev_month_name = getdate(add_months(month, -1)).strftime("%B")
	billed_note = _("No bills last month")
	if prev and prev["billed"]:
		billed_note = _("{0} vs. {1}").format(signed_pct((cur["billed"] - prev["billed"]) / prev["billed"] * 100), prev_month_name)
	rate = cur["rate"]

	accounts = mis.account_rows(society, as_of)
	arrears = sum(flt(a.arrears_balance) + flt(a.surcharge_balance) + flt(a.fees_balance) for a in accounts)
	before = mis.account_rows(society, add_days(month, -1))
	arrears_before = sum(flt(a.arrears_balance) + flt(a.surcharge_balance) + flt(a.fees_balance) for a in before)
	delta = arrears - arrears_before

	defaulters, overdue_60 = defaulter_stats(society, as_of)

	defective = frappe.get_all("Meter", filters={"society": society, "status": "Defective"}, pluck="modified")
	stale = sum(1 for m in defective if (getdate(as_of) - getdate(m)).days > 60)
	return [
		kpi(_("Billed this cycle"), rs(cur["billed"]), billed_note, MUTED, cur["billed"]),
		kpi(_("Collected so far"), rs(cur["collected"]),
		    _("{0}% collection efficiency").format(f"{rate:.1f}") if cur["bills"] else _("No bills in this cycle"),
		    (GOOD if rate >= 85 else WARN if rate >= 60 else BAD) if cur["bills"] else MUTED, cur["collected"]),
		kpi(_("Total arrears"), rs(arrears), _("{0} vs. last month").format(signed_rs(delta)) if round(delta) else _("No change vs. last month"),
		    BAD if round(delta) > 0 else GOOD, arrears),
		line_loss_card(society, month, series),
		kpi(_("Units billed"), f"{cur['units']:,.0f} kWh", (_("1 connection billed") if cur["bills"] == 1 else _("{0} connections billed").format(f"{cur['bills']:,}")), MUTED, cur["units"]),
		kpi(_("Defaulters (60+ days)"), f"{defaulters:,}", _("{0} outstanding").format(rs(overdue_60)),
		    BAD if defaulters else GOOD, defaulters),
		kpi(_("Defective meters"), f"{len(defective):,}", _("{0} not updated for 2+ months").format(stale), WARN if stale else MUTED, len(defective)),
		kpi(_("Open service requests"), "—", _("Service requests aren't built yet"), MUTED),
	]


SEVERITY = {"red": 0, "amber": 1, "blue": 2}


def attention_of(society, month, as_of):
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

	count, amount = defaulter_stats(society, as_of)
	if count:
		add("red", _("{0} accounts are 60+ days overdue").format(f"{count:,}"), _("{0} outstanding").format(rs(amount)),
		    ["query-report", "Defaulters"], {"society": society, "min_days": 60})
	estimated = mis.REPORTS["estimated_bills"]["fn"](frappe._dict({"society": society, "month": str(month), "repeat_only": 1}))["data"]
	if estimated:
		add("amber", _("{0} consumers billed on estimates 2+ months running").format(len(estimated)), _("They need a real reading"),
		    ["query-report", "Estimated Bills"], {"society": society, "month": str(month), "repeat_only": 1})
	closed = frappe.db.count("Cashier Day", {"society": society, "status": "Closed"})
	if closed:
		add("blue", _("{0} cashier days are waiting for approval").format(closed), _("Closed with a cash count"), ["List", "Cashier Day"],
		    {"society": society, "status": "Closed"})
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


ICONS = ("billed", "collected", "arrears", "losses", "units", "defaulters", "meters", "service")


def cards_of(society, month, series, as_of):
	"""The eight cards with an icon each and, where there is a monthly history, a sparkline of the last 12 months."""
	sparks = {0: [m["billed"] for m in series], 1: [m["collected"] for m in series], 4: [m["units"] for m in series]}
	cards = kpis_of(society, month, series, as_of)
	for i, card in enumerate(cards):
		card["icon"] = ICONS[i]
		card["spark"] = sparks.get(i) if any(sparks.get(i) or []) else None
	return cards


# ---------------------------------------------------------------- the call
@frappe.whitelist()
def get_dashboard(society: str | None = None, month: str | None = None):
	if not set(frappe.get_roles()) & ALLOWED:
		raise frappe.PermissionError(_("You don't have access to the dashboard."))
	societies = frappe.get_all("Society", fields=["name", "society_name"], order_by="creation desc", limit=200)
	if not societies:
		return {"empty": True, "message": _("Create a society first.")}
	society = society or default_society(societies)
	if not frappe.db.exists("Society", society):
		frappe.throw(_("Society {0} not found.").format(society))
	month = mis.month_start(month or latest_billing_month(society))
	today = getdate(nowdate())
	as_of = min(today, getdate(get_last_day(month)))
	series = mis.monthly_series(society, mis.last_months(month, 12))
	steps, footer = steps_of(society, month, today)
	feeders = feeders_of(society, month)
	energy = frappe.db.get_value("Society", society, "enable_electricity")
	return {
		"society": society, "society_name": frappe.db.get_value("Society", society, "society_name"),
		"societies": [{"name": s.name, "label": s.society_name or s.name} for s in societies],
		"data_label": data_label(society), "month": str(month), "month_label": getdate(month).strftime("%B %Y"),
		"months": [{"value": str(m), "label": getdate(m).strftime("%B %Y")} for m in mis.last_months(month if month > today else today, 13)][::-1],
		"subtitle": _("{0} · {1} billing cycle").format(_("Electricity") if energy else _("Billing"), getdate(month).strftime("%B %Y")),
		"kpis": cards_of(society, month, series, as_of), "attention": attention_of(society, month, as_of), "series": series, "steps": steps, "footer": footer,
		"feeders": feeders, "feeders_have_input": any(f["input_kwh"] != "—" for f in feeders), "channels": channels_of(society, month),
		"user": {"name": frappe.session.user, "full_name": frappe.db.get_value("User", frappe.session.user, "full_name"),
		         "initials": "".join(w[0] for w in (frappe.db.get_value("User", frappe.session.user, "full_name") or "U").split()[:2]).upper()},
	}
