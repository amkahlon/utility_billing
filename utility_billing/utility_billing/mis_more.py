# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""More reports (increment 20; FS-RPT-03, 12, 22, 25, 28, 36, 38, 43). Registered in the same registry as mis.py (imported at its end)."""

import statistics
from collections import OrderedDict, defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

from utility_billing.utility_billing.mis import (
	BILLING, BLOCK, EXEC, FINANCE, RECOVERY, col, month_start, pct, report, society_of, sql, total_row,
)

PERIOD = [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
          dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
          dict(fieldname="to_date", label="To", fieldtype="Date", default="@today")]


def _period(f):
	return getdate(f.get("from_date") or month_start(nowdate())), getdate(f.get("to_date") or nowdate())


# ---------------------------------------------------------------- installment plans (RPT-25)
@report("installment_plans", "Installment Plans", RECOVERY + ["Billing Officer"],
        [PERIOD[0], dict(fieldname="plan_status", label="Status", fieldtype="Select", options="\nNot started\nActive\nCompleted\nDefaulted\nCancelled")],
        "Every approved plan: amount, down payment, installments billed, paid and missed, and what is still to come.", ref_doctype="Installment Plan")
def installment_plans(f):
	s = society_of(f)
	cond, values = ["p.society = %(s)s", "p.docstatus = 1"], {"s": s}
	if f.get("plan_status"):
		cond.append("p.plan_status = %(st)s")
		values["st"] = f.plan_status
	data = []
	for p in sql(f"""select p.name, p.connection, p.consumer_name, p.plan_status, p.amount_to_split, p.down_payment, p.installments, p.installment_amount,
	                        p.first_month, p.approved_by from `tabInstallment Plan` p where {' and '.join(cond)} order by p.creation""", values):
		rows = frappe.get_all("Installment Row", filters={"parent": p.name}, fields=["status", "amount"])
		count = defaultdict(int)
		left = 0.0
		for r in rows:
			count[r.status] += 1
			if r.status in ("Scheduled", "Billed"):
				left += flt(r.amount)
		data.append({**p, "billed": count["Billed"] + count["Paid"] + count["Missed"], "paid": count["Paid"], "missed": count["Missed"], "to_come": round(left, 2)})
	by = defaultdict(int)
	for d in data:
		by[d["plan_status"]] += 1
	summary = [{"value": by[k], "label": _(k), "datatype": "Int", "indicator": "Red" if k == "Defaulted" else "Blue"}
	           for k in ("Active", "Completed", "Defaulted", "Cancelled")]
	return {"columns": [col("Plan", "name", "Link", 120, "Installment Plan"), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Consumer", "consumer_name", width=150), col("Status", "plan_status", width=100), col("Amount", "amount_to_split", "Currency", 110),
	                    col("Down Payment", "down_payment", "Currency", 110), col("Installments", "installments", "Int", 100),
	                    col("Each", "installment_amount", "Currency", 100), col("Billed", "billed", "Int", 70), col("Paid", "paid", "Int", 70),
	                    col("Missed", "missed", "Int", 70), col("Still to Come", "to_come", "Currency", 110), col("Approved By", "approved_by", width=150)],
	        "data": data, "summary": summary}


# ---------------------------------------------------------------- consumer movement (RPT-36) and disconnections (RPT-28)
MOVEMENT = ("New connection", "Temporary connection", "Disconnection", "Reconnection", "Permanent disconnection", "Change of category",
            "Load change", "Change of name or ownership", "Tenant moving in", "Tenant leaving", "Meter change")


@report("consumer_movement", "Consumer Movement", BILLING + ["Society Admin"], PERIOD,
        "New connections, disconnections, reconnections, closures, category, load, owner and tenant changes completed in the period.",
        ref_doctype="Service Request")
def consumer_movement(f):
	s = society_of(f)
	a, z = _period(f)
	rows = sql("""select kind, count(*) n from `tabService Request` where society = %(s)s and status = 'Completed' and date(completed_on) between %(a)s and %(z)s
	              and kind in %(k)s group by kind""", {"s": s, "a": a, "z": z, "k": MOVEMENT})
	got = {r.kind: cint(r.n) for r in rows}
	direct = cint(sql("""select count(*) n from `tabConnection` c where c.society = %(s)s and c.connection_date between %(a)s and %(z)s
	                     and not exists (select 1 from `tabService Request` r where r.connection = c.name and r.kind in ('New connection', 'Temporary connection'))""",
	                  {"s": s, "a": a, "z": z})[0].n)
	active = cint(sql("select count(*) n from `tabConnection` where society = %(s)s and status = 'Active'", {"s": s})[0].n)
	data = [{"movement": _(k), "count": got.get(k, 0)} for k in MOVEMENT]
	data.append({"movement": _("Connections entered directly (not through a request)"), "count": direct})
	data = [d for d in data if d["count"]]
	summary = [{"value": got.get("New connection", 0) + got.get("Temporary connection", 0) + direct, "label": _("Joined"), "datatype": "Int", "indicator": "Green"},
	           {"value": got.get("Disconnection", 0) + got.get("Permanent disconnection", 0), "label": _("Disconnected or closed"), "datatype": "Int", "indicator": "Red"},
	           {"value": active, "label": _("Active now"), "datatype": "Int", "indicator": "Blue"}]
	return {"columns": [col("Movement", "movement", width=320), col("Count", "count", "Int", 100)], "data": data, "summary": summary}


@report("disconnections_register", "Disconnections and Reconnections", RECOVERY, PERIOD,
        "Every disconnection, reconnection and closure requested in the period: why, when it took effect, its status and the account's balance.",
        ref_doctype="Service Request")
def disconnections_register(f):
	from utility_billing.utility_billing import account  # noqa: PLC0415

	s = society_of(f)
	a, z = _period(f)
	data = []
	for r in sql("""select name, kind, connection, consumer_name, status, requested_on, effective_date, completed_on, disconnection_reason, result_note
	               from `tabService Request` where society = %(s)s and kind in ('Disconnection', 'Reconnection', 'Permanent disconnection')
	               and requested_on between %(a)s and %(z)s order by requested_on, name""", {"s": s, "a": a, "z": z}):
		data.append({**r, "kind": _(r.kind), "owes": round(account.balance(r.connection), 2) if r.connection else None})
	return {"columns": [col("Request", "name", "Link", 120, "Service Request"), col("What", "kind", width=170), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Consumer", "consumer_name", width=150), col("Why", "disconnection_reason", width=120), col("Status", "status", width=100),
	                    col("Requested", "requested_on", "Date", 100), col("Takes Effect", "effective_date", "Date", 100),
	                    col("Done On", "completed_on", "Datetime", 140), col("Owes Now", "owes", "Currency", 110)], "data": data,
	        "message": _("The notices before a disconnection are in the Recovery Notices Register.")}


# ---------------------------------------------------------------- no-dues certificates (RPT-43)
@report("nodues_certificates", "No-Dues Certificates Issued", BILLING + ["Society Admin"], PERIOD,
        "Certificates issued in the period with their number and verification code.", ref_doctype="Service Request")
def nodues_certificates(f):
	s = society_of(f)
	a, z = _period(f)
	data = sql("""select certificate_no, verification_code, connection, consumer_name, date(completed_on) issued_on, name request
	              from `tabService Request` where society = %(s)s and kind = 'No-dues certificate' and status = 'Completed'
	              and date(completed_on) between %(a)s and %(z)s order by completed_on""", {"s": s, "a": a, "z": z})
	return {"columns": [col("Certificate", "certificate_no", width=150), col("Verification Code", "verification_code", width=130),
	                    col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=170),
	                    col("Issued On", "issued_on", "Date", 100), col("Request", "request", "Link", 120, "Service Request")], "data": data}


# ---------------------------------------------------------------- detection bills and abnormal consumption (RPT-12)
@report("detection_abnormal", "Detection Bills and Abnormal Consumption", BILLING + ["Recovery Officer"],
        [PERIOD[0], dict(fieldname="month", label="Billing Month", fieldtype="Date", default="@month_start")],
        "Detection bills assessed, and the month's bills whose units are less than half or more than twice the account's own average.",
        ref_doctype="Utility Bill")
def detection_abnormal(f):
	s = society_of(f)
	month = month_start(f.get("month") or nowdate())
	data = []
	for d in sql("""select name, connection, consumer_name, assessed_units, assessed_amount, status, adjustment, requested_on from `tabService Request`
	               where society = %(s)s and kind = 'Detection bill' and requested_on between %(a)s and %(z)s""",
	             {"s": s, "a": add_months(month, -11), "z": frappe.utils.get_last_day(month)}):
		data.append({"kind": _("Detection bill"), "connection": d.connection, "consumer_name": d.consumer_name, "units": flt(d.assessed_units),
		             "amount": flt(d.assessed_amount), "note": _("{0}, {1}").format(_(d.status), d.adjustment or d.name)})
	bills = sql("""select connection, consumer_name, units from `tabUtility Bill` where society = %(s)s and docstatus = 1 and billing_month = %(m)s""",
	            {"s": s, "m": month})
	for b in bills:
		hist = [flt(x.units) for x in sql("""select units from `tabUtility Bill` where connection = %(c)s and docstatus = 1 and billing_month < %(m)s
		                                    and billing_month >= %(f)s""", {"c": b.connection, "m": month, "f": add_months(month, -3)})]
		if len(hist) < 2:
			continue
		avg = sum(hist) / len(hist)
		if avg <= 0:
			continue
		ratio = flt(b.units) / avg
		if ratio < 0.5 or ratio > 2:
			data.append({"kind": _("Unusually low") if ratio < 0.5 else _("Unusually high"), "connection": b.connection, "consumer_name": b.consumer_name,
			             "units": flt(b.units), "amount": None, "note": _("{0} units against an average of {1} ({2}% of normal)").format(
			                 flt(b.units), round(avg), round(ratio * 100))})
	return {"columns": [col("What", "kind", width=140), col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=160),
	                    col("Units", "units", "Float", 90), col("Amount", "amount", "Currency", 110), col("Note", "note", width=320)], "data": data}


# ---------------------------------------------------------------- channel fees (RPT-22)
def channel_fee(amount, rule):
	fee = flt(amount) * flt(rule.fee_percent) / 100 + flt(rule.fixed_fee)
	return round(min(fee, flt(rule.cap)) if flt(rule.cap) > 0 else fee, 2)


@report("channel_fees", "Channel Fees and Commissions", FINANCE, PERIOD,
        "What each payment channel charges the society for the payments it collected in the period, from the fees set on the Society.",
        ref_doctype="Payment Receipt")
def channel_fees(f):
	s = society_of(f)
	a, z = _period(f)
	rules = {r.mode: r for r in frappe.get_cached_doc("Society", s).get("channel_fees") or []}
	acc = OrderedDict()
	for p in sql("""select mode, amount from `tabPayment Receipt` where society = %(s)s and docstatus = 1 and status != 'Reversed'
	               and payment_date between %(a)s and %(z)s order by mode""", {"s": s, "a": a, "z": z}):
		row = acc.setdefault(p.mode, {"mode": _(p.mode), "payments": 0, "collected": 0.0, "fees": 0.0, "rule": ""})
		row["payments"] += 1
		row["collected"] += flt(p.amount)
		rule = rules.get(p.mode)
		if rule:
			row["fees"] += channel_fee(p.amount, rule)
			row["rule"] = _("{0}% + Rs {1}{2}").format(flt(rule.fee_percent), flt(rule.fixed_fee), _(", at most Rs {0}").format(flt(rule.cap)) if flt(rule.cap) else "")
	data = []
	for row in acc.values():
		row["fees"] = round(row["fees"], 2)
		row["net"] = round(row["collected"] - row["fees"], 2)
		row["rate"] = pct(row["fees"], row["collected"])
		data.append(row)
	if data:
		data.append(total_row("mode", _("Total"), data, ("payments", "collected", "fees", "net")))
	return {"columns": [col("Channel", "mode", width=140), col("Payments", "payments", "Int", 90), col("Collected", "collected", "Currency", 130),
	                    col("Fees", "fees", "Currency", 110), col("Net Received", "net", "Currency", 130), col("Fees %", "rate", "Percent", 80),
	                    col("Fee Rule", "rule", width=200)], "data": data,
	        "message": _("Set each channel's fee on the Society (Channel fees and commissions); channels without a fee show no fee.")}


# ---------------------------------------------------------------- revenue against target (RPT-38)
@report("revenue_vs_target", "Revenue against Target", FINANCE,
        [PERIOD[0], dict(fieldname="year", label="Year", fieldtype="Int")],
        "Billing and collection each month against the society's revenue targets, with the difference and the year to date.", ref_doctype="Revenue Target")
def revenue_vs_target(f):
	s = society_of(f)
	year = cint(f.get("year")) or getdate(nowdate()).year
	budget = defaultdict(lambda: [0.0, 0.0])
	for r in sql("""select m.month, m.billed, m.collected from `tabTarget Month` m join `tabRevenue Target` b on b.name = m.parent
	               where b.society = %(s)s and b.target_year = %(y)s""", {"s": s, "y": year}):
		budget[str(month_start(r.month))][0] += flt(r.billed)
		budget[str(month_start(r.month))][1] += flt(r.collected)
	billed = {str(r.m): flt(r.v) for r in sql("""select billing_month m, sum(current_amount) v from `tabUtility Bill` where society = %(s)s and docstatus = 1
	                                             and status = 'Posted' and year(billing_month) = %(y)s group by billing_month""", {"s": s, "y": year})}
	collected = {str(r.m): flt(r.v) for r in sql("""select date_format(payment_date, '%%Y-%%m-01') m, sum(amount) v from `tabPayment Receipt`
	                                                where society = %(s)s and docstatus = 1 and status != 'Reversed' and year(payment_date) = %(y)s group by 1""",
	                                             {"s": s, "y": year})}
	data, ytd = [], [0.0] * 4
	for m in range(1, 13):
		k = f"{year}-{m:02d}-01"
		bb, bc = budget[k] if k in budget else (0.0, 0.0)
		ab, ac = billed.get(k, 0.0), collected.get(k, 0.0)
		if not (bb or bc or ab or ac):
			continue
		for i, v in enumerate((bb, ab, bc, ac)):
			ytd[i] += v
		data.append({"month": getdate(k).strftime("%b %Y"), "budget_billed": bb, "billed": ab, "billed_diff": round(ab - bb, 2), "billed_pct": pct(ab, bb) if bb else None,
		             "budget_collected": bc, "collected": ac, "collected_diff": round(ac - bc, 2), "collected_pct": pct(ac, bc) if bc else None})
	if data:
		data.append({"month": _("Year to date"), "budget_billed": ytd[0], "billed": ytd[1], "billed_diff": round(ytd[1] - ytd[0], 2),
		             "billed_pct": pct(ytd[1], ytd[0]) if ytd[0] else None, "budget_collected": ytd[2], "collected": ytd[3],
		             "collected_diff": round(ytd[3] - ytd[2], 2), "collected_pct": pct(ytd[3], ytd[2]) if ytd[2] else None})
	return {"columns": [col("Month", "month", width=110), col("Billing Target", "budget_billed", "Currency", 120), col("Billed", "billed", "Currency", 120),
	                    col("Difference", "billed_diff", "Currency", 110), col("% of Target", "billed_pct", "Percent", 100),
	                    col("Collection Target", "budget_collected", "Currency", 130), col("Collected", "collected", "Currency", 120),
	                    col("Difference", "collected_diff", "Currency", 110), col("% of Target", "collected_pct", "Percent", 100)], "data": data,
	        "message": _("Billed is the current bills of the billing month; collected is the payments received in the month. The DISCO margin is in DISCO Bill vs Resident Billing.")}


# ---------------------------------------------------------------- consumption analytics (RPT-03)
@report("consumption_analytics", "Consumption Analytics", BILLING + ["Recovery Officer"],
        [PERIOD[0], dict(fieldname="month", label="Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="show", label="Show", fieldtype="Select", options="Theft Suspicion\nSeasonality\nRevenue Forecast", default="Theft Suspicion")],
        "Rule-based analytics: accounts whose use dropped sharply or is far below similar properties, the seasonal pattern, and a revenue forecast.")
def consumption_analytics(f):
	s = society_of(f)
	month = month_start(f.get("month") or nowdate())
	show = f.get("show") or "Theft Suspicion"
	if show == "Seasonality":
		return _seasonality(s, month)
	if show == "Revenue Forecast":
		return _forecast(s, month)
	return _theft(s, month)


def _theft(s, month):
	bills = sql(f"""select b.connection, b.consumer_name, b.units, b.consumer_category cat, ifnull(p.plot_size_class, '') size, {BLOCK} block
	               from `tabUtility Bill` b left join `tabProperty` p on p.name = b.property
	               where b.society = %(s)s and b.docstatus = 1 and b.billing_month = %(m)s""", {"s": s, "m": month})
	peers = defaultdict(list)
	for b in bills:
		peers[(b.cat, b.size)].append(flt(b.units))
	data = []
	for b in bills:
		reasons = []
		hist = [flt(x.units) for x in sql("""select units from `tabUtility Bill` where connection = %(c)s and docstatus = 1 and billing_month < %(m)s
		                                    and billing_month >= %(f)s""", {"c": b.connection, "m": month, "f": add_months(month, -6)})]
		own = sum(hist) / len(hist) if len(hist) >= 3 else None
		if own and own > 0 and flt(b.units) < own * 0.5:
			reasons.append(_("dropped to {0}% of its own 6-month average ({1})").format(round(flt(b.units) / own * 100), round(own)))
		group = peers[(b.cat, b.size)]
		median = statistics.median(group) if len(group) >= 5 else None
		if median and median > 0 and flt(b.units) < median * 0.3:
			reasons.append(_("{0}% of the median of similar properties ({1})").format(round(flt(b.units) / median * 100), round(median)))
		if reasons:
			data.append({"connection": b.connection, "consumer_name": b.consumer_name, "block": b.block, "units": flt(b.units),
			             "own_average": round(own) if own else None, "peer_median": round(median) if median else None, "why": "; ".join(reasons)})
	data.sort(key=lambda r: r["units"])
	return {"columns": [col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=160), col("Block", "block", width=90),
	                    col("Units", "units", "Float", 90), col("Own Average", "own_average", "Float", 100), col("Similar Median", "peer_median", "Float", 110),
	                    col("Why Suspected", "why", width=380)], "data": data,
	        "message": _("A lead for inspection, not a finding: units below half the account's own 6-month average, or below 30% of the median of at least 5 properties of the same category and plot size.")}


def _seasonality(s, month):
	rows = sql("""select month(billing_month) m, avg(units) u, count(*) n from `tabUtility Bill` where society = %(s)s and docstatus = 1
	              and billing_month between %(f)s and %(t)s group by month(billing_month) order by 1""", {"s": s, "f": add_months(month, -23), "t": month})
	overall = statistics.mean([flt(r.u) for r in rows]) if rows else 0
	data = [{"month": getdate(f"2000-{int(r.m):02d}-01").strftime("%B"), "avg_units": round(flt(r.u), 1), "bills": cint(r.n),
	         "index": round(flt(r.u) / overall * 100) if overall else None} for r in rows]
	return {"columns": [col("Month", "month", width=120), col("Average Units per Bill", "avg_units", "Float", 160), col("Bills", "bills", "Int", 80),
	                    col("Season Index (100 = average)", "index", "Int", 200)], "data": data,
	        "chart": {"data": {"labels": [d["month"] for d in data], "datasets": [{"name": _("Season index"), "values": [d["index"] or 0 for d in data]}]},
	                  "type": "bar"} if data else None,
	        "message": _("The last 24 months of bills, by calendar month.")}


def _forecast(s, month):
	billed = {str(r.m): flt(r.v) for r in sql("""select billing_month m, sum(current_amount) v from `tabUtility Bill` where society = %(s)s
	                                             and docstatus = 1 and status = 'Posted' group by billing_month""", {"s": s})}
	val = lambda d: billed.get(str(month_start(d)), 0.0)  # noqa: E731
	recent = [val(add_months(month, -i)) for i in range(0, 3)]
	year_ago = [val(add_months(month, -12 - i)) for i in range(0, 3)]
	growth = (sum(recent) / sum(year_ago)) if sum(year_ago) else None
	data = []
	for i in range(1, 4):
		m = add_months(month, i)
		last_year = val(add_months(m, -12))
		if growth and last_year:
			fc, how = last_year * growth, _("same month last year × recent growth ({0}%)").format(round((growth - 1) * 100, 1))
		else:
			nonzero = [x for x in recent if x]
			fc, how = (sum(nonzero) / len(nonzero) if nonzero else 0), _("average of the last 3 months")
		data.append({"month": getdate(m).strftime("%b %Y"), "forecast": round(fc, 2), "basis": how})
	return {"columns": [col("Month", "month", width=110), col("Forecast Billing", "forecast", "Currency", 150), col("How", "basis", width=320)],
	        "data": data if any(d["forecast"] for d in data) else [], "message": _("A simple forecast of billing from the society's own history.")}



# ---------------------------------------------------------------- revenue assurance (FS-RA-09; increment 21)
@report("revenue_assurance", "Revenue Assurance Findings", EXEC + ["Recovery Officer"],
        [PERIOD[0], dict(fieldname="from_month", label="From Month", fieldtype="Date"), dict(fieldname="to_month", label="To Month", fieldtype="Date"),
         dict(fieldname="view", label="Show", fieldtype="Select", options="By Check\nBy Officer\nOpen Findings", default="By Check")],
        "Revenue assurance findings: how many each check raised, how many are open or closed, the revenue at risk and what was recovered; or the open findings to work.",
        ref_doctype="RA Finding")
def revenue_assurance(f):
	s = society_of(f)
	cond, values = ["society = %(s)s"], {"s": s}
	if f.get("from_month"):
		cond.append("(billing_month is null or billing_month >= %(fm)s)")
		values["fm"] = month_start(f.from_month)
	if f.get("to_month"):
		cond.append("(billing_month is null or billing_month <= %(tm)s)")
		values["tm"] = month_start(f.to_month)
	where = " and ".join(cond)
	view = f.get("view") or "By Check"
	if view == "Open Findings":
		data = sql(f"""select name, check_type, billing_month, subject, connection, score, amount_at_risk, status, assigned_to, details
		               from `tabRA Finding` where {where} and status != 'Closed' order by score desc, amount_at_risk desc""", values)
		return {"columns": [col("Finding", "name", "Link", 100, "RA Finding"), col("Check", "check_type", width=200), col("Month", "billing_month", "Date", 95),
		                    col("Subject", "subject", width=130), col("Account", "connection", "Link", 110, "Connection"), col("Score", "score", "Int", 65),
		                    col("At Risk", "amount_at_risk", "Currency", 100), col("Status", "status", width=95), col("Assigned To", "assigned_to", width=150),
		                    col("Details", "details", width=380)], "data": data}
	key = "check_type" if view == "By Check" else "ifnull(assigned_to, 'Not assigned')"
	data = sql(f"""select {key} item, count(*) findings, sum(status != 'Closed') open, sum(status = 'Closed') closed,
	                      sum(status = 'Closed' and result = 'No issue') no_issue, sum(case when status != 'Closed' then amount_at_risk else 0 end) at_risk,
	                      sum(amount_recovered) recovered
	               from `tabRA Finding` where {where} group by {key} order by {key}""", values)
	for d in data:
		for k in ("findings", "open", "closed", "no_issue"):
			d[k] = cint(d[k])
		d["hit_rate"] = pct(d["closed"] - d["no_issue"], d["closed"]) if d["closed"] else None
	if data:
		data.append(total_row("item", _("Total"), data, ("findings", "open", "closed", "no_issue", "at_risk", "recovered")))
	summary = [{"value": sum(cint(d["open"]) for d in data[:-1]), "label": _("Open findings"), "datatype": "Int", "indicator": "Orange"},
	           {"value": sum(flt(d["at_risk"]) for d in data[:-1]), "label": _("Revenue at risk (open)"), "datatype": "Currency", "indicator": "Red"},
	           {"value": sum(flt(d["recovered"]) for d in data[:-1]), "label": _("Recovered"), "datatype": "Currency", "indicator": "Green"}] if data else []
	return {"columns": [col("Check" if view == "By Check" else "Officer", "item", width=240), col("Findings", "findings", "Int", 85),
	                    col("Open", "open", "Int", 70), col("Closed", "closed", "Int", 70), col("No Issue", "no_issue", "Int", 80),
	                    col("Confirmed %", "hit_rate", "Percent", 100), col("At Risk (open)", "at_risk", "Currency", 120),
	                    col("Recovered", "recovered", "Currency", 120)], "data": data, "summary": summary}


# ---------------------------------------------------------------- increment 22: defective meters (FS-WF-08), former occupants (SC-13)
@report("defective_meters", "Defective Meters", BILLING + ["Recovery Officer"], [PERIOD[0]],
        "Meters marked defective and still installed: since when, months billed on averages against the society's limit, and the meter "
        "change request. Those over the limit are billed fixed charges only until replaced.", ref_doctype="Meter")
def defective_meters(f):
	from utility_billing.utility_billing import defective  # noqa: PLC0415

	data = defective.still_defective(society_of(f))
	over = sum(1 for d in data if d["over_limit"])
	return {"columns": [col("Meter", "meter", "Link", 130, "Meter"), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Defective Since", "defective_since", "Date", 110), col("Days", "days", "Int", 70),
	                    col("Months Averaged", "months_averaged", "Int", 120), col("Limit", "limit", "Int", 70),
	                    col("Over the Limit", "over_limit", "Check", 100), col("Meter Change Request", "request", "Link", 150, "Service Request"),
	                    col("Request Status", "request_status", width=120)],
	        "data": data,
	        "summary": [{"value": len(data), "label": _("Defective meters"), "datatype": "Int", "indicator": "Orange"},
	                    {"value": over, "label": _("Over the averaging limit"), "datatype": "Int", "indicator": "Red"}]}


@report("former_occupant_balances", "Former Occupant Balances", RECOVERY, [PERIOD[0],
        dict(fieldname="status", label="Status", fieldtype="Select", options="\nOpen\nSettled", default="Open")],
        "What outgoing tenants and owners still owe after their final bills, and who answers for it (the former holder, or the owner "
        "when the tenancy made the owner liable).", ref_doctype="Former Occupant Balance")
def former_occupant_balances(f):
	filters = {"society": society_of(f)}
	if f.get("status"):
		filters["status"] = f.status
	data = frappe.get_all("Former Occupant Balance", filters=filters, order_by="change_date desc",
	                      fields=["name", "connection", "consumer", "consumer_name", "liable_consumer", "change_date", "final_bill", "amount",
	                              "surcharge", "paid", "outstanding", "status"])
	for d in data:
		d["liable_name"] = frappe.db.get_value("Consumer", d.liable_consumer, "consumer_name") if d.liable_consumer else None
	if data:
		data.append(total_row("consumer_name", _("Total"), data, ("amount", "surcharge", "paid", "outstanding")))
	return {"columns": [col("Balance", "name", "Link", 100, "Former Occupant Balance"), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Former Holder", "consumer_name", width=160), col("Who Pays", "liable_name", width=160), col("Left On", "change_date", "Date", 100),
	                    col("Final Bill", "final_bill", "Link", 140, "Utility Bill"), col("Owed at the Change", "amount", "Currency", 130),
	                    col("Surcharge", "surcharge", "Currency", 100), col("Paid", "paid", "Currency", 100),
	                    col("Outstanding", "outstanding", "Currency", 110), col("Status", "status", width=90)],
	        "data": data}
