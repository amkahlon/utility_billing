# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Core management reports and the management dashboard (increment 9; FS-RPT-01, 02, 04-07, 10, 11, 13-18,
21, 23, 24, 26, 35, 41, 42; FS-RPT-45 is the KPI Definition DocType).

Every report is a function registered with @report(...) that returns a dict (columns, data, and optionally
chart, summary, message). `run(key, filters)` turns it into what a Frappe Script Report expects; the files
under utility_billing/report/ only call run(). dev/build_reports.py generates them from this registry.
Reports read the live data directly (FS-RPT-46's separate reporting layer comes later) and are limited to the
roles listed for each report.
"""

import json
from collections import OrderedDict, defaultdict
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, formatdate, get_first_day, get_last_day, getdate, nowdate

from utility_billing.utility_billing import tariff_engine

REPORTS: dict[str, dict] = OrderedDict()

# Who may open each kind of report (System Manager always can).
EXEC = ["Management", "Billing Auditor", "General Manager", "Billing Manager"]
FINANCE = EXEC + ["Accounts Officer"]
BILLING = EXEC + ["Billing Officer"]
RECOVERY = EXEC + ["Recovery Officer", "Accounts Officer"]
READINGS = BILLING + ["Meter Reader"]
APPROVERS = ["Management", "Billing Auditor", "General Manager", "Billing Manager"]
AUDIT = ["Billing Auditor", "General Manager"]

BANDS = ("0-30", "31-60", "61-90", "91-180", "over 180")
BLOCK = "ifnull(nullif(p.block, ''), '(none)')"


def report(key, name, roles, filters=(), description="", ref_doctype="Utility Bill"):
	def wrap(fn):
		REPORTS[key] = {"key": key, "name": name, "roles": roles, "filters": list(filters), "fn": fn,
		                "description": description, "ref_doctype": ref_doctype}
		return fn

	return wrap


def run(key, filters=None):
	"""Frappe Script Report entry point: (columns, data, message, chart, report_summary)."""
	spec = REPORTS[key]
	f = frappe._dict(filters or {})
	out = spec["fn"](f)
	return out["columns"], out["data"], out.get("message"), out.get("chart"), out.get("summary")


# ---------------------------------------------------------------- helpers
def sql(query, values=None):
	return frappe.db.sql(query, values or {}, as_dict=True)


def col(label, fieldname, fieldtype="Data", width=120, options=None):
	d = {"label": _(label), "fieldname": fieldname, "fieldtype": fieldtype, "width": width}
	if fieldtype == "Float":
		d["precision"] = 2
	elif fieldtype == "Percent":
		d["precision"] = 1
	if options:
		d["options"] = options
	return d


def society_of(f):
	if f.get("society"):
		return f.society
	names = frappe.get_all("Society", pluck="name", limit=2)
	if len(names) == 1:
		return names[0]
	frappe.throw(_("Choose a society."))


def pct(part, whole):
	return round(flt(part) / flt(whole) * 100, 1) if flt(whole) else 0


def month_start(d):
	return get_first_day(getdate(d))


def month_key(d):
	return str(month_start(d))


def last_months(end, n):
	end = month_start(end)
	return [add_months(end, -i) for i in range(n - 1, -1, -1)]


def total_row(label_field, label, rows, sum_fields, **extra):
	t = {label_field: label, **extra}
	for k in sum_fields:
		t[k] = round(sum(flt(r.get(k)) for r in rows), 2)
	return t


def period(f, start_default, end_default=None):
	start = getdate(f.get("from_date") or start_default)
	end = getdate(f.get("to_date") or end_default or nowdate())
	if start > end:
		frappe.throw(_("The start date is after the end date."))
	return start, end


# ---------------------------------------------------------------- pure rules (unit tested)
def band_of(age_days) -> str:
	"""Arrears ageing band of an amount that is `age_days` past its due date (FS-REC-02)."""
	a = max(cint(age_days), 0)
	if a <= 30:
		return BANDS[0]
	if a <= 60:
		return BANDS[1]
	if a <= 90:
		return BANDS[2]
	if a <= 180:
		return BANDS[3]
	return BANDS[4]


def allocate_age(components, balance, as_of):
	"""Age what an account owes. `components` are (date, amount) of what was charged: bills by due date,
	surcharges, fees and debit notes by posting date. Payments clear the oldest charges first, so the
	amount still owed belongs to the newest charges. Returns [(age_days, amount)] for the owed balance;
	anything owed beyond the listed charges (opening balances) is aged as the oldest charge."""
	as_of = getdate(as_of)
	left = round(flt(balance), 2)
	out = []
	if left <= 0:
		return out
	ordered = sorted(components, key=lambda c: getdate(c[0]), reverse=True)
	for d, amount in ordered:
		if left <= 0:
			break
		take = round(min(flt(amount), left), 2)
		if take > 0:
			out.append((max((as_of - getdate(d)).days, 0), take))
			left = round(left - take, 2)
	if left > 0:
		oldest = max((as_of - getdate(ordered[-1][0])).days, 0) if ordered else 0
		out.append((oldest, left))
	return out


def streak_months(estimated_months: set, month) -> int:
	"""How many months in a row, ending at `month`, a connection was billed on an estimate (FS-RPT-15)."""
	n, m = 0, month_start(month)
	while str(m) in estimated_months:
		n += 1
		m = add_months(m, -1)
	return n


def slab_label(line) -> str:
	if line.get("from_units") is None:
		return _("Flat rate")
	to = line.get("to_units")
	lo = f"{flt(line['from_units']):g}"
	return f"{lo}-{flt(to):g}" if to else f"{lo}+"


# ---------------------------------------------------------------- shared queries
def account_rows(society, as_of=None):
	"""Each account's latest balances (as of a date), with consumer, block and category."""
	as_of = getdate(as_of or nowdate())
	return sql(
		f"""select e.connection, e.consumer_name, e.balance, e.current_balance, e.arrears_balance, e.surcharge_balance,
			e.fees_balance, e.credit_balance, c.consumer_category, ifnull(cc.category_name, c.consumer_category) category,
			{BLOCK} block, p.property_no, co.mobile_no, c.route
		from `tabCustomer Ledger Entry` e
		join (select connection, max(seq) m from `tabCustomer Ledger Entry`
		      where society = %(s)s and posting_date <= %(as_of)s group by connection) x
		  on x.connection = e.connection and x.m = e.seq
		join `tabConnection` c on c.name = e.connection
		left join `tabProperty` p on p.name = c.property
		left join `tabConsumer` co on co.name = c.consumer
		left join `tabConsumer Category` cc on cc.name = c.consumer_category""",
		{"s": society, "as_of": as_of},
	)


def aging_for(society, as_of, category=None, block=None):
	"""[(account row, [(age, amount)])] for every account owing money."""
	as_of = getdate(as_of)
	rows = [r for r in account_rows(society, as_of) if flt(r.balance) > 0
	        and (not category or r.consumer_category == category) and (not block or r.block == block)]
	comps = defaultdict(list)
	for b in sql("""select connection, due_date, current_amount from `tabUtility Bill`
	               where society = %(s)s and docstatus = 1 and issue_date <= %(a)s""", {"s": society, "a": as_of}):
		comps[b.connection].append((b.due_date, b.current_amount))
	for e in sql("""select connection, posting_date, amount from `tabCustomer Ledger Entry`
	               where society = %(s)s and posting_date <= %(a)s and entry_type in ('Surcharge', 'Fee', 'Debit Note')
	                 and amount > 0 and ifnull(reversed_by, '') = '' and ifnull(reverses, '') = ''""", {"s": society, "a": as_of}):
		comps[e.connection].append((e.posting_date, e.amount))
	return [(r, allocate_age(comps.get(r.connection, []), r.balance, as_of)) for r in rows]


def monthly_series(society, months):
	"""Per month: bills, units, billed (current bills), payable within due date, collected, collection rate. Shared by the
	dashboard report and the home dashboard page, so they always agree."""
	first, last = months[0], get_last_day(months[-1])
	billed = {month_key(r.m): r for r in sql(
		"""select billing_month m, count(*) bills, sum(units) units, sum(current_amount) billed, sum(payable_within_due) payable
		from `tabUtility Bill` where society = %(s)s and docstatus = 1 and billing_month between %(a)s and %(b)s
		group by billing_month""", {"s": society, "a": first, "b": last})}
	paid = {r.m: flt(r.collected) for r in sql(
		"""select date_format(payment_date, '%%Y-%%m-01') m, sum(amount) collected from `tabPayment Receipt`
		where society = %(s)s and docstatus = 1 and payment_date between %(a)s and %(b)s group by 1""",
		{"s": society, "a": first, "b": last})}
	rows = []
	for m in months:
		k = month_key(m)
		b = billed.get(k)
		collected = paid.get(k, 0)
		rows.append({"month": getdate(k).strftime("%b %Y"), "bills": cint(b.bills) if b else 0,
		             "units": flt(b.units) if b else 0, "billed": flt(b.billed) if b else 0,
		             "payable": flt(b.payable) if b else 0, "collected": collected,
		             "rate": pct(collected, b.payable) if b else 0})
	return rows


# ================================================================ 1. dashboard (RPT-01)
@report("dashboard", "Management Dashboard", FINANCE + ["Billing Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="month", label="Month", fieldtype="Date", default="@today")],
        "Billed, collected, collection rate, arrears and trends for this month and the last 12.")
def dashboard(f):
	s = society_of(f)
	end = month_start(f.get("month") or nowdate())
	data = monthly_series(s, last_months(end, 13))
	labels = [d["month"] for d in data]
	cur = data[-1]
	accounts = account_rows(s)
	owed = sum(flt(a.balance) for a in accounts if flt(a.balance) > 0)
	arrears = sum(flt(a.arrears_balance) + flt(a.surcharge_balance) + flt(a.fees_balance) for a in accounts)
	in_arrears = sum(1 for a in accounts if flt(a.arrears_balance) > 0)
	credit = sum(flt(a.credit_balance) for a in accounts)
	summary = [
		{"value": cur["billed"], "label": _("Billed this month"), "datatype": "Currency", "indicator": "Blue"},
		{"value": cur["collected"], "label": _("Collected this month"), "datatype": "Currency", "indicator": "Green"},
		{"value": cur["rate"], "label": _("Collection rate this month (%)"), "datatype": "Percent",
		 "indicator": "Green" if cur["rate"] >= 90 else "Orange"},
		{"value": owed, "label": _("Total owed by consumers"), "datatype": "Currency", "indicator": "Red"},
		{"value": arrears, "label": _("Of which arrears, surcharge and fees"), "datatype": "Currency", "indicator": "Orange"},
		{"value": in_arrears, "label": _("Accounts in arrears"), "datatype": "Int", "indicator": "Orange"},
		{"value": credit, "label": _("Credit held for consumers"), "datatype": "Currency", "indicator": "Blue"},
	]
	links = " · ".join(
		f'<a href="/desk/query-report/{quote(REPORTS[k]["name"])}?society={quote(s)}">{_(REPORTS[k]["name"])}</a>'
		for k in ("billing_summary", "collection_vs_billing", "arrears_ageing", "defaulters", "reading_completion"))
	msg = (f"<b>{_('Collection rate')}</b> = {_('payments received in the month ÷ amount payable within the due date on the bills issued that month')}. "
	       f"{_('Line losses appear here once the DISCO bill and bulk meter units are recorded.')}<br>{_('Drill down')}: {links}")
	return {
		"columns": [col("Month", "month", width=100), col("Bills", "bills", "Int", 70), col("Units", "units", "Float", 100),
		            col("Billed (current bills)", "billed", "Currency", 140), col("Payable within due date", "payable", "Currency", 150),
		            col("Collected", "collected", "Currency", 130), col("Collection rate %", "rate", "Percent", 120)],
		"data": data, "summary": summary, "message": msg,
		"chart": {"data": {"labels": labels, "datasets": [{"name": _("Billed"), "values": [d["billed"] for d in data]},
		                                                   {"name": _("Collected"), "values": [d["collected"] for d in data]}]},
		          "type": "bar", "fieldtype": "Currency"},
	}


# ================================================================ 2. collection vs billing (RPT-02)
CAT_BLOCK_FILTERS = [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
                     dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
                     dict(fieldname="to_date", label="To", fieldtype="Date", default="@today")]


@report("collection_vs_billing", "Collection vs Billing by Category and Block", FINANCE, CAT_BLOCK_FILTERS,
        "Billed (bills issued in the period) against collected (payments dated in the period), with the collection percentage.")
def collection_vs_billing(f):
	s, (a, b) = society_of(f), period(f, month_start(nowdate()))
	billed = sql(f"""select ifnull(cc.category_name, c.consumer_category) category, {BLOCK} block, count(*) bills,
			sum(bl.current_amount) billed, sum(bl.payable_within_due) payable
		from `tabUtility Bill` bl join `tabConnection` c on c.name = bl.connection
		left join `tabProperty` p on p.name = c.property left join `tabConsumer Category` cc on cc.name = c.consumer_category
		where bl.society = %(s)s and bl.docstatus = 1 and bl.issue_date between %(a)s and %(b)s group by 1, 2""",
		{"s": s, "a": a, "b": b})
	paid = sql(f"""select ifnull(cc.category_name, c.consumer_category) category, {BLOCK} block, sum(r.amount) collected
		from `tabPayment Receipt` r join `tabConnection` c on c.name = r.connection
		left join `tabProperty` p on p.name = c.property left join `tabConsumer Category` cc on cc.name = c.consumer_category
		where r.society = %(s)s and r.docstatus = 1 and r.payment_date between %(a)s and %(b)s group by 1, 2""",
		{"s": s, "a": a, "b": b})
	rows = OrderedDict()
	for r in billed:
		rows[(r.category, r.block)] = {"category": r.category, "block": r.block, "bills": cint(r.bills),
		                               "billed": flt(r.billed), "payable": flt(r.payable), "collected": 0}
	for r in paid:
		rows.setdefault((r.category, r.block), {"category": r.category, "block": r.block, "bills": 0, "billed": 0,
		                                        "payable": 0, "collected": 0})["collected"] = flt(r.collected)
	data = sorted(rows.values(), key=lambda r: (r["category"] or "", r["block"]))
	for r in data:
		r["rate"] = pct(r["collected"], r["payable"])
	if data:
		t = total_row("category", _("Total"), data, ("bills", "billed", "payable", "collected"))
		t["rate"] = pct(t["collected"], t["payable"])
		data.append(t)
	return {"columns": [col("Category", "category", width=160), col("Block", "block", width=100), col("Bills", "bills", "Int", 70),
	                    col("Billed (current)", "billed", "Currency", 130), col("Payable within due date", "payable", "Currency", 150),
	                    col("Collected", "collected", "Currency", 120), col("Collected % of payable", "rate", "Percent", 150)],
	        "data": data,
	        "message": _("Collections include payments on earlier bills' arrears, so a rate above 100% means arrears were recovered.")}


# ================================================================ 3. billing summary (RPT-04)
@report("billing_summary", "Billing Summary by Cycle", BILLING + ["Accounts Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="group_by", label="Group By", fieldtype="Select", options="Charge Type\nCategory\nBlock", default="Charge Type")],
        "Bills, units and amounts by charge type, category or block for the billing months chosen.")
def billing_summary(f):
	s = society_of(f)
	a, z = month_start(f.get("from_date") or nowdate()), get_last_day(f.get("to_date") or nowdate())
	v = {"s": s, "a": a, "b": z}
	g = f.get("group_by") or "Charge Type"
	if g == "Charge Type":
		rows = sql("""select l.label item, count(distinct b.name) bills,
				sum(case when l.basis = 'Per Unit' then l.quantity else 0 end) units, sum(l.amount) amount
			from `tabUtility Bill` b join `tabBill Line` l on l.parent = b.name and l.parenttype = 'Utility Bill'
			where b.society = %(s)s and b.docstatus = 1 and b.billing_month between %(a)s and %(b)s
			group by l.label order by min(l.line_order), l.label""", v)
	else:
		key = "ifnull(cc.category_name, c.consumer_category)" if g == "Category" else BLOCK
		rows = sql(f"""select {key} item, count(*) bills, sum(b.units) units, sum(b.current_amount) amount
			from `tabUtility Bill` b join `tabConnection` c on c.name = b.connection
			left join `tabProperty` p on p.name = c.property left join `tabConsumer Category` cc on cc.name = c.consumer_category
			where b.society = %(s)s and b.docstatus = 1 and b.billing_month between %(a)s and %(b)s group by 1 order by 1""", v)
	data = [{"item": r.item, "bills": cint(r.bills), "units": flt(r.units), "amount": flt(r.amount)} for r in rows]
	if data:
		t = total_row("item", _("Total"), data, ("amount",))
		if g != "Charge Type":
			t["bills"], t["units"] = sum(r["bills"] for r in data), round(sum(r["units"] for r in data), 2)
		data.append(t)
	return {"columns": [col(g, "item", width=200), col("Bills", "bills", "Int", 80), col("Units", "units", "Float", 110),
	                    col("Amount", "amount", "Currency", 140)], "data": data,
	        "message": _("By charge type: Bills counts the bills carrying the charge; Units is the units of per-unit charges. By category or block the amount is the current bill (arrears and surcharge excluded).")}


# ================================================================ 4. bill register (RPT-05)
@report("bill_register", "Bill Register", BILLING + ["Accounts Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="bill_run", label="Bill Run", fieldtype="Link", options="Bill Run"),
         dict(fieldname="from_date", label="From Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="show_lines", label="Show Charge Lines", fieldtype="Check")],
        "Every bill with its totals, or every charge line of every bill.")
def bill_register(f):
	s = society_of(f)
	cond, v = "", {"s": s, "a": month_start(f.get("from_date") or nowdate()), "b": get_last_day(f.get("to_date") or nowdate())}
	if f.get("bill_run"):
		cond, v["run"] = " and b.bill_run = %(run)s", f.bill_run
		v["a"], v["b"] = getdate("1900-01-01"), getdate("2999-12-31")
	head = f"""b.name bill, c.reference_no, b.consumer_name, {BLOCK} block, p.property_no, ifnull(cc.category_name, c.consumer_category) category,
		b.billing_month, b.units, b.status"""
	frm = """from `tabUtility Bill` b join `tabConnection` c on c.name = b.connection left join `tabProperty` p on p.name = c.property
		left join `tabConsumer Category` cc on cc.name = c.consumer_category"""
	where = "where b.society = %(s)s and b.docstatus in (1, 2) and b.billing_month between %(a)s and %(b)s" + cond
	base = [col("Bill", "bill", "Link", 140, "Utility Bill"), col("Reference No", "reference_no", width=110),
	        col("Consumer", "consumer_name", width=160), col("Block", "block", width=80), col("Property", "property_no", width=80),
	        col("Category", "category", width=120), col("Month", "billing_month", "Date", 90), col("Units", "units", "Float", 80)]
	if f.get("show_lines"):
		rows = sql(f"select {head}, l.label, l.quantity, l.rate, l.amount, l.note {frm} join `tabBill Line` l on l.parent = b.name "
		           f"{where} order by b.name, l.line_order, l.idx", v)
		cols = base + [col("Charge", "label", width=150), col("Quantity", "quantity", "Float", 90), col("Rate", "rate", "Float", 80),
		               col("Amount", "amount", "Currency", 110), col("How calculated", "note", width=260)]
		return {"columns": cols, "data": rows}
	rows = sql(f"select {head}, b.current_amount, b.arrears, b.payable_within_due, b.surcharge_amount, b.payable_after_due {frm} {where} order by b.name", v)
	cols = base + [col("Status", "status", width=80), col("Current Bill", "current_amount", "Currency", 110),
	               col("Arrears", "arrears", "Currency", 100), col("Within Due Date", "payable_within_due", "Currency", 120),
	               col("Surcharge", "surcharge_amount", "Currency", 100), col("After Due Date", "payable_after_due", "Currency", 120)]
	live = [r for r in rows if r.status != "Cancelled"]
	if rows:
		rows.append(total_row("bill", _("Total"), live, ("units", "current_amount", "arrears", "payable_within_due",
		                                                "surcharge_amount", "payable_after_due")))
	return {"columns": cols, "data": rows, "message": _("Totals exclude cancelled bills.")}


# ================================================================ 5. sales by category and tariff (RPT-06)
@report("sales", "Sales by Category and Tariff", BILLING + ["Accounts Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="show", label="Show", fieldtype="Select", options="Category and Tariff\nUnits by Slab", default="Category and Tariff")],
        "Units and amounts by category and tariff, units by slab, and the average rate per unit.")
def sales(f):
	s = society_of(f)
	v = {"s": s, "a": month_start(f.get("from_date") or nowdate()), "b": get_last_day(f.get("to_date") or nowdate())}
	energy = frappe.db.get_value("Society", s, "energy_charge_type")
	if f.get("show") == "Units by Slab":
		acc = OrderedDict()
		for b in sql("""select tariff, count(*) bills, sum(units) units from `tabUtility Bill`
		               where society = %(s)s and docstatus = 1 and billing_month between %(a)s and %(b)s group by tariff, units""", v):
			if not b.tariff:
				continue
			t = frappe.get_cached_doc("Tariff", b.tariff)
			ec = tariff_engine.calculate_energy(b.units, t.pricing_method, t.flat_rate, t.slabs, 0)
			for line in ec.lines:
				k = (b.tariff, slab_label(line), flt(line["rate"]))
				r = acc.setdefault(k, {"tariff": b.tariff, "slab": k[1], "rate": k[2], "bills": 0, "units": 0, "amount": 0})
				r["bills"] += cint(b.bills)
				r["units"] += flt(line["units"]) * cint(b.bills)
				r["amount"] += flt(line["amount"]) * cint(b.bills)
		data = [dict(r, units=round(r["units"], 2), amount=round(r["amount"], 2)) for r in acc.values()]
		if data:
			data.append(total_row("tariff", _("Total"), data, ("units", "amount")))
		return {"columns": [col("Tariff", "tariff", "Link", 150, "Tariff"), col("Slab (units)", "slab", width=110),
		                    col("Rate", "rate", "Float", 80), col("Bills", "bills", "Int", 70), col("Units", "units", "Float", 110),
		                    col("Energy Amount", "amount", "Currency", 130)], "data": data,
		        "message": _("Slab units are worked out from each bill's units and its tariff; the minimum charge is not included.")}
	rows = sql("""select ifnull(cc.category_name, c.consumer_category) category, b.tariff, count(distinct b.name) bills, sum(b.units) units,
			sum(b.current_amount) amount,
			(select sum(l.amount) from `tabBill Line` l join `tabUtility Bill` b2 on b2.name = l.parent
			  where b2.society = %(s)s and b2.docstatus = 1 and b2.billing_month between %(a)s and %(b)s
			    and l.charge_type = %(e)s and b2.tariff = b.tariff and b2.consumer_category = b.consumer_category) energy
		from `tabUtility Bill` b join `tabConnection` c on c.name = b.connection
		left join `tabConsumer Category` cc on cc.name = c.consumer_category
		where b.society = %(s)s and b.docstatus = 1 and b.billing_month between %(a)s and %(b)s
		group by b.consumer_category, b.tariff, category order by category, b.tariff""", dict(v, e=energy or ""))
	data = [{"category": r.category, "tariff": r.tariff, "bills": cint(r.bills), "units": flt(r.units), "energy": flt(r.energy),
	         "amount": flt(r.amount), "avg_rate": round(flt(r.energy) / flt(r.units), 2) if flt(r.units) else 0} for r in rows]
	if data:
		t = total_row("category", _("Total"), data, ("bills", "units", "energy", "amount"))
		t["avg_rate"] = round(t["energy"] / t["units"], 2) if t["units"] else 0
		data.append(t)
	return {"columns": [col("Category", "category", width=160), col("Tariff", "tariff", "Link", 150, "Tariff"),
	                    col("Bills", "bills", "Int", 70), col("Units", "units", "Float", 110), col("Energy Amount", "energy", "Currency", 130),
	                    col("Current Bills", "amount", "Currency", 130), col("Average Rate per Unit", "avg_rate", "Float", 150)],
	        "data": data}


# ================================================================ 6. FPA / QTA register (RPT-07)
@report("fpa_qta_register", "FPA and QTA Register", BILLING + ["Accounts Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From Month", fieldtype="Date", default="@year_start"),
         dict(fieldname="to_date", label="To Month", fieldtype="Date", default="@today"),
         dict(fieldname="adjustment_type", label="Type", fieldtype="Select", options="\nFPA\nQTA")],
        "Each approved FPA/QTA: reference period, rate, and the units, amount and exemptions by category.")
def fpa_qta_register(f):
	s = society_of(f)
	cond = " and adjustment_type = %(t)s" if f.get("adjustment_type") else ""
	adjs = sql("""select name, adjustment_type, billing_month, reference_month, rate_per_unit, charge_type, notification, approved_by
	             from `tabTariff Adjustment` where society = %(s)s and docstatus = 1 and billing_month between %(a)s and %(b)s""" + cond +
	           " order by billing_month, adjustment_type",
	           {"s": s, "a": month_start(f.get("from_date") or add_months(nowdate(), -12)), "b": get_last_day(f.get("to_date") or nowdate()),
	            "t": f.get("adjustment_type")})
	data = []
	for a in adjs:
		rows = sql("""select ifnull(cc.category_name, b.consumer_category) category, count(distinct b.name) bills, sum(l.quantity) units,
				sum(l.amount) amount, sum(case when l.note like 'Exempt:%%' then 1 else 0 end) exempt
			from `tabUtility Bill` b join `tabBill Line` l on l.parent = b.name and l.parenttype = 'Utility Bill'
			left join `tabConsumer Category` cc on cc.name = b.consumer_category
			where b.society = %(s)s and b.docstatus = 1 and b.billing_month = %(m)s and l.charge_type = %(ct)s
			group by b.consumer_category, category order by category""", {"s": s, "m": a.billing_month, "ct": a.charge_type})
		head = {"adjustment": a.name, "type": a.adjustment_type, "billing_month": a.billing_month, "reference_month": a.reference_month,
		        "rate": flt(a.rate_per_unit), "approved_by": a.approved_by, "notification": a.notification}
		for r in rows or [None]:
			data.append(dict(head, category=r.category if r else _("(no bills)"), bills=cint(r.bills) if r else 0,
			                 units=flt(r.units) if r else 0, amount=flt(r.amount) if r else 0, exempt=cint(r.exempt) if r else 0))
	return {"columns": [col("Adjustment", "adjustment", "Link", 130, "Tariff Adjustment"), col("Type", "type", width=60),
	                    col("Billing Month", "billing_month", "Date", 100), col("Reference Month (units)", "reference_month", "Date", 130),
	                    col("Rate per Unit", "rate", "Float", 90), col("Category", "category", width=150), col("Bills", "bills", "Int", 70),
	                    col("Units", "units", "Float", 100), col("Amount", "amount", "Currency", 110),
	                    col("Exempt Bills", "exempt", "Int", 90), col("Approved By", "approved_by", "Link", 150, "User"),
	                    col("Notification", "notification", "Attach", 120)],
	        "data": data,
	        "message": _("Units are each consumer's own units of the reference month. Exempt bills carry the FPA/QTA line at zero with the reason.")}


# ================================================================ 7. adjustments register (RPT-10)
@report("adjustments_register", "Adjustments by Reason and Approver", FINANCE + ["Billing Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today"),
         dict(fieldname="reason_code", label="Reason", fieldtype="Link", options="Reason Code"),
         dict(fieldname="note_type", label="Type", fieldtype="Select", options="\nCredit Note\nDebit Note")],
        "Approved credit and debit notes with the reason, who made them and who approved them (revised bills come later).",
        ref_doctype="Account Adjustment")
def adjustments_register(f):
	s, (a, b) = society_of(f), period(f, month_start(nowdate()))
	cond = "".join(f" and aa.{k} = %({k})s" for k in ("reason_code", "note_type") if f.get(k))
	rows = sql(f"""select aa.name, aa.note_type, aa.posting_date, aa.connection, aa.consumer_name, aa.against_bill, aa.bucket, aa.amount,
			aa.reason_code, rc.description reason, aa.remarks, aa.owner maker, aa.approved_by, aa.approved_on
		from `tabAccount Adjustment` aa left join `tabReason Code` rc on rc.name = aa.reason_code
		where aa.society = %(s)s and aa.docstatus = 1 and aa.posting_date between %(a)s and %(b)s {cond}
		order by aa.posting_date, aa.name""", {"s": s, "a": a, "b": b, "reason_code": f.get("reason_code"), "note_type": f.get("note_type")})
	data = [dict(r, signed=flt(r.amount) if r.note_type == "Debit Note" else -flt(r.amount)) for r in rows]
	if data:
		data.append(total_row("name", _("Net effect on balances (+ debit, − credit)"), data, ("signed",)))
	return {"columns": [col("Note", "name", "Link", 130, "Account Adjustment"), col("Type", "note_type", width=90),
	                    col("Date", "posting_date", "Date", 90), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Consumer", "consumer_name", width=140), col("Bill Corrected", "against_bill", "Link", 130, "Utility Bill"),
	                    col("Balance", "bucket", width=80), col("Amount", "amount", "Currency", 100), col("Net Effect", "signed", "Currency", 100),
	                    col("Reason", "reason", width=200), col("Explanation", "remarks", width=220),
	                    col("Made By", "maker", "Link", 150, "User"), col("Approved By", "approved_by", "Link", 150, "User"),
	                    col("Approved On", "approved_on", "Datetime", 140)], "data": data}


# ================================================================ 8. surcharge register (RPT-11)
@report("surcharge_register", "Surcharge Register", FINANCE,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today")],
        "Late payment surcharge levied, waived by credit note, and reversed because the payment was in time.",
        ref_doctype="Customer Ledger Entry")
def surcharge_register(f):
	s, (a, b) = society_of(f), period(f, month_start(nowdate()))
	rows = sql("""select e.posting_date, e.connection, e.consumer_name, e.entry_type, e.d_surcharge amount, e.voucher_no, e.remarks,
			aa.approved_by, aa.reason_code
		from `tabCustomer Ledger Entry` e left join `tabAccount Adjustment` aa on e.entry_type = 'Credit Note' and aa.name = e.voucher_no
		where e.society = %(s)s and e.posting_date between %(a)s and %(b)s and (
		  e.entry_type = 'Surcharge'
		  or (e.entry_type = 'Credit Note' and e.d_surcharge < 0)
		  or (e.entry_type = 'Reversal' and exists (select 1 from `tabCustomer Ledger Entry` x where x.name = e.reverses and x.entry_type = 'Surcharge')))
		order by e.posting_date, e.seq""", {"s": s, "a": a, "b": b})
	label = {"Surcharge": _("Levied"), "Credit Note": _("Waived"), "Reversal": _("Reversed (paid in time)")}
	data = [dict(r, kind=label[r.entry_type], amount=flt(r.amount)) for r in rows]
	if data:
		by = defaultdict(float)
		for r in data:
			by[r["kind"]] += r["amount"]
		data.append({"kind": _("Levied"), "amount": round(by[_("Levied")], 2), "remarks": ""})
		data.append({"kind": _("Waived or reversed"), "amount": round(by[_("Waived")] + by[_("Reversed (paid in time)")], 2), "remarks": ""})
		data.append({"kind": _("Net surcharge"), "amount": round(sum(by.values()), 2), "remarks": ""})
	return {"columns": [col("Date", "posting_date", "Date", 90), col("What", "kind", width=150),
	                    col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=150),
	                    col("Amount", "amount", "Currency", 100), col("Document", "voucher_no", width=140),
	                    col("Approved By", "approved_by", "Link", 150, "User"), col("Reason", "reason_code", width=100),
	                    col("Note", "remarks", width=260)], "data": data,
	        "message": _("Extensions of the due date are not built yet, so none appear here.")}


# ================================================================ 9. reading completion (RPT-13)
CYCLE_FILTERS = [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
                 dict(fieldname="reading_cycle", label="Reading Cycle", fieldtype="Link", options="Reading Cycle")]


def cycle_of(f):
	if f.get("reading_cycle"):
		return f.reading_cycle
	s = society_of(f)
	name = frappe.db.get_value("Reading Cycle", {"society": s}, "name", order_by="billing_month desc")
	if not name:
		frappe.throw(_("There is no reading cycle yet."))
	return name


@report("reading_completion", "Reading Completion by Route and Reader", READINGS,
        CYCLE_FILTERS + [dict(fieldname="group_by", label="Group By", fieldtype="Select", options="Route\nReader", default="Route")],
        "How many meters are read, pending or flagged by route or by reader, and each reader's readings per day.",
        ref_doctype="Meter Reading")
def reading_completion(f):
	cycle = cycle_of(f)
	if f.get("group_by") == "Reader":
		key = "case when r.reading_date is null then %(none)s else r.modified_by end"
		join = ""
	else:
		key = "ifnull(nullif(c.route, ''), '(no route)')"
		join = "left join `tabConnection` c on c.name = r.connection"
	rows = sql(f"""select {key} item, count(*) meters, sum(r.status in ('Valid', 'Approved', 'Estimated')) done,
			sum(r.reading_date is not null) entered, sum(r.status = 'Pending') pending, sum(r.status in ('Flagged', 'Re-read Requested')) flagged,
			count(distinct r.reading_date) days
		from `tabMeter Reading` r {join} where r.reading_cycle = %(c)s group by 1 order by 1""", {"c": cycle, "none": _("(not yet read)")})
	data = [{"item": r.item, "meters": cint(r.meters), "entered": cint(r.entered), "done": cint(r.done), "pending": cint(r.pending),
	         "flagged": cint(r.flagged), "rate": pct(r.done, r.meters), "days": cint(r.days),
	         "per_day": round(cint(r.entered) / cint(r.days), 1) if cint(r.days) else 0} for r in rows]
	if data:
		t = total_row("item", _("Total"), data, ("meters", "entered", "done", "pending", "flagged"))
		t["rate"] = pct(t["done"], t["meters"])
		data.append(t)
	return {"columns": [col(f.get("group_by") or "Route", "item", width=180), col("Meters", "meters", "Int", 80),
	                    col("Readings Entered", "entered", "Int", 120), col("Accepted", "done", "Int", 90),
	                    col("Pending", "pending", "Int", 80), col("Flagged", "flagged", "Int", 80),
	                    col("Completion %", "rate", "Percent", 100), col("Days Worked", "days", "Int", 100),
	                    col("Readings per Day", "per_day", "Float", 120)], "data": data,
	        "message": _("Accepted = valid, approved or estimated. By reader, readings are credited to the person who last saved them.")}


# ================================================================ 10. reading exceptions (RPT-14)
@report("reading_exceptions", "Reading Exceptions", READINGS, CYCLE_FILTERS,
        "Unread meters, zero use, lower than last, abnormal and re-read readings, with the photo.", ref_doctype="Meter Reading")
def reading_exceptions(f):
	cycle = cycle_of(f)
	rows = sql("""select r.name, r.connection, r.consumer_name, r.meter, r.status, r.exception_code, r.units, r.expected_low,
			r.expected_high, r.checks, r.photo, r.reviewed_by, r.review_note
		from `tabMeter Reading` r where r.reading_cycle = %(c)s and (
			r.status in ('Pending', 'Flagged', 'Re-read Requested', 'Estimated') or ifnull(r.exception_code, '') != ''
			or ifnull(r.checks, '') != '' or (r.reading_date is not null and r.units = 0))
		order by r.status, r.connection""", {"c": cycle})
	return {"columns": [col("Reading", "name", "Link", 130, "Meter Reading"), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Consumer", "consumer_name", width=150), col("Meter", "meter", "Link", 110, "Meter"),
	                    col("Status", "status", width=110), col("Exception Code", "exception_code", "Link", 110, "Reading Exception Code"),
	                    col("Units", "units", "Float", 80), col("Expected Low", "expected_low", "Float", 100),
	                    col("Expected High", "expected_high", "Float", 100), col("Checks", "checks", width=260),
	                    col("Photo", "photo", "Attach Image", 100), col("Reviewed By", "reviewed_by", "Link", 150, "User"),
	                    col("Review Note", "review_note", width=200)], "data": rows,
	        "message": _("Cycle {0}.").format(cycle)}


# ================================================================ 11. estimated bills (RPT-15)
@report("estimated_bills", "Estimated Bills", BILLING + ["Accounts Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="month", label="Billing Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="repeat_only", label="Only 2 or More Months Running", fieldtype="Check")],
        "Bills based on estimated readings, and how many months in a row each consumer has been estimated.",
        ref_doctype="Meter Reading")
def estimated_bills(f):
	s, month = society_of(f), month_start(f.get("month") or nowdate())
	history = defaultdict(set)
	for r in sql("""select connection, billing_month from `tabMeter Reading` where society = %(s)s and billing_month <= %(m)s
	               and (status = 'Estimated' or consumption_type = 'Estimated')""", {"s": s, "m": month}):
		history[r.connection].add(str(month_start(r.billing_month)))
	rows = sql(f"""select r.connection, r.consumer_name, {BLOCK} block, r.meter, r.units, r.exception_code, b.name bill, b.current_amount
		from `tabMeter Reading` r left join `tabProperty` p on p.name = r.property
		left join `tabUtility Bill` b on b.connection = r.connection and b.billing_month = r.billing_month and b.docstatus = 1
		where r.society = %(s)s and r.billing_month = %(m)s and (r.status = 'Estimated' or r.consumption_type = 'Estimated')
		order by r.connection""", {"s": s, "m": month})
	data = []
	for r in rows:
		n = streak_months(history[r.connection], month)
		if f.get("repeat_only") and n < 2:
			continue
		data.append(dict(r, months_running=n, flag=_("2+ months") if n >= 2 else ""))
	data.sort(key=lambda r: -r["months_running"])
	return {"columns": [col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=160),
	                    col("Block", "block", width=80), col("Meter", "meter", "Link", 110, "Meter"), col("Estimated Units", "units", "Float", 110),
	                    col("Reason", "exception_code", width=100), col("Bill", "bill", "Link", 130, "Utility Bill"),
	                    col("Current Bill", "current_amount", "Currency", 110), col("Months Running", "months_running", "Int", 110),
	                    col("Flag", "flag", width=90)], "data": data}


# ================================================================ 12. meter register (RPT-16)
@report("meter_register", "Meter Register", BILLING,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="status", label="Status", fieldtype="Select", options="\nIn Store\nInstalled\nDefective\nRemoved")],
        "Meters in store, installed, defective and removed, with the account and how long each has been installed.",
        ref_doctype="Meter")
def meter_register(f):
	s = society_of(f)
	cond = " and m.status = %(st)s" if f.get("status") else ""
	rows = sql(f"""select m.name, m.serial_no, m.make, m.meter_type, m.status, m.current_connection connection, c.consumer_name,
			(select cm.installed_on from `tabConnection Meter` cm where cm.meter = m.name and cm.parent = m.current_connection
			   and cm.removed_on is null order by cm.installed_on desc limit 1) installed_on
		from `tabMeter` m left join `tabConnection` c on c.name = m.current_connection
		where m.society = %(s)s {cond} order by m.status, m.serial_no""", {"s": s, "st": f.get("status")})
	today = getdate(nowdate())
	counts = defaultdict(int)
	for r in rows:
		counts[r.status] += 1
		r["age_months"] = max(round((today - getdate(r.installed_on)).days / 30.4), 0) if r.installed_on and r.status != "In Store" else None
	order = ("In Store", "Installed", "Defective", "Removed")
	summary = [{"value": counts[k], "label": _(k), "datatype": "Int",
	            "indicator": "Red" if k == "Defective" else "Blue"} for k in order]
	return {"columns": [col("Meter", "name", "Link", 120, "Meter"), col("Serial No", "serial_no", width=120), col("Make", "make", width=100),
	                    col("Type", "meter_type", width=110), col("Status", "status", width=90),
	                    col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=160),
	                    col("Installed On", "installed_on", "Date", 100), col("Age (months)", "age_months", "Int", 100)],
	        "data": rows, "summary": summary,
	        "message": _("Meters under testing aren't a separate status yet; the age of a defective meter is the time since it was installed.")}


# ================================================================ 13. collections (RPT-17)
@report("collections", "Collections", FINANCE,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today"),
         dict(fieldname="group_by", label="Group By", fieldtype="Select", options="Day\nCounter\nCashier\nPayment Mode\nChannel", default="Day")],
        "Payments received by day, counter, cashier, payment mode or channel, with reversals shown separately.",
        ref_doctype="Payment Receipt")
def collections(f):
	s, (a, b) = society_of(f), period(f, month_start(nowdate()))
	g = f.get("group_by") or "Day"
	key = {"Day": "r.payment_date", "Counter": "ifnull(r.cash_counter, %(bank)s)", "Cashier": "r.owner",
	       "Payment Mode": "r.mode", "Channel": "r.payment_channel"}[g]
	rows = sql(f"""select {key} item, sum(r.docstatus = 1) receipts, sum(case when r.docstatus = 1 then r.amount else 0 end) amount,
			sum(r.docstatus = 2) reversed, sum(case when r.docstatus = 2 then r.amount else 0 end) reversed_amount
		from `tabPayment Receipt` r where r.society = %(s)s and r.docstatus in (1, 2) and r.payment_date between %(a)s and %(b)s
		group by 1 order by 1""", {"s": s, "a": a, "b": b, "bank": _("(bank / channel)")})
	data = [{"item": r.item, "receipts": cint(r.receipts), "amount": flt(r.amount), "reversed": cint(r.reversed),
	         "reversed_amount": flt(r.reversed_amount)} for r in rows]
	if data:
		data.append(total_row("item", _("Total"), data, ("receipts", "amount", "reversed", "reversed_amount")))
	itemtype = "Date" if g == "Day" else "Data"
	return {"columns": [col(g, "item", itemtype, 180), col("Receipts", "receipts", "Int", 90), col("Amount", "amount", "Currency", 130),
	                    col("Reversed Receipts", "reversed", "Int", 120), col("Reversed Amount", "reversed_amount", "Currency", 130)],
	        "data": data,
	        "message": _("Amount counts only receipts still posted, so it is what was really collected. Reversed receipts are those a Billing Manager reversed (for example a dishonoured cheque); they are shown separately and are not in Amount.")}


# ================================================================ 14. cashier closing (RPT-18)
@report("cashier_closing", "Cashier Closing", FINANCE,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today"),
         dict(fieldname="cash_counter", label="Counter", fieldtype="Link", options="Cash Counter"),
         dict(fieldname="status", label="Status", fieldtype="Select", options="\nOpen\nClosed\nApproved")],
        "Each cashier's day: receipts, cash expected and counted, difference, deposit slip and approval.",
        ref_doctype="Cashier Day")
def cashier_closing(f):
	s, (a, b) = society_of(f), period(f, month_start(nowdate()))
	cond = "".join(f" and d.{k} = %({k})s" for k in ("cash_counter", "status") if f.get(k))
	rows = sql(f"""select d.name, d.business_date, d.cash_counter, d.cashier, d.status, d.receipt_count, d.cash_total, d.cheque_total,
			d.card_total, d.total_collected, d.reversed_total, d.expected_cash, d.cash_counted, d.difference, d.deposit_slip_no,
			d.approved_by, d.close_remarks
		from `tabCashier Day` d where d.society = %(s)s and d.business_date between %(a)s and %(b)s {cond}
		order by d.business_date, d.cash_counter""", {"s": s, "a": a, "b": b, "cash_counter": f.get("cash_counter"), "status": f.get("status")})
	if rows:
		rows.append(total_row("name", _("Total"), rows, ("receipt_count", "cash_total", "cheque_total", "card_total", "total_collected",
		                                                  "reversed_total", "difference")))
	return {"columns": [col("Day", "name", "Link", 140, "Cashier Day"), col("Date", "business_date", "Date", 90),
	                    col("Counter", "cash_counter", "Link", 80, "Cash Counter"), col("Cashier", "cashier", "Link", 150, "User"),
	                    col("Status", "status", width=80), col("Receipts", "receipt_count", "Int", 80), col("Cash", "cash_total", "Currency", 110),
	                    col("Cheques/Pay Orders", "cheque_total", "Currency", 130), col("Cards", "card_total", "Currency", 100),
	                    col("Total Collected", "total_collected", "Currency", 120), col("Reversed", "reversed_total", "Currency", 100),
	                    col("Expected Cash", "expected_cash", "Currency", 120), col("Cash Counted", "cash_counted", "Currency", 120),
	                    col("Difference", "difference", "Currency", 100), col("Deposit Slip", "deposit_slip_no", width=100),
	                    col("Approved By", "approved_by", "Link", 150, "User"), col("Remarks", "close_remarks", width=200)],
	        "data": rows,
	        "message": _("Cash, cheques, cards and Total Collected are what was received during the day, before any later reversal; Expected Cash already takes off cash that was reversed. Collection efficiency (collected against billed) is in Collection vs Billing by Category and Block.")}


# ================================================================ 15. payment reversals (RPT-21)
@report("payment_reversals", "Payment and Fee Reversals", FINANCE,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today")],
        "Payments and fees that were reversed, with the reason, the explanation and who did it.", ref_doctype="Payment Receipt")
def payment_reversals(f):
	s, (a, b) = society_of(f), period(f, month_start(nowdate()))
	v = {"s": s, "a": a, "b": b}
	pay = sql("""select r.name document, r.payment_date date, r.connection, r.consumer_name, r.amount, r.mode detail, r.reversal_reason reason,
			r.reversal_remarks remarks, r.reversed_by, r.reversed_on, r.owner taken_by
		from `tabPayment Receipt` r where r.society = %(s)s and r.docstatus = 2 and r.payment_date between %(a)s and %(b)s""", v)
	fees = sql("""select x.name document, x.posting_date date, x.connection, x.consumer_name, x.amount, x.fee_type detail, x.reversal_reason reason,
			x.reversal_remarks remarks, x.modified_by reversed_by, x.modified reversed_on, x.owner taken_by
		from `tabFee Charge` x where x.society = %(s)s and x.docstatus = 2 and x.posting_date between %(a)s and %(b)s""", v)
	data = [dict(r, kind=_("Payment"), doctype="Payment Receipt") for r in pay] + [dict(r, kind=_("Fee"), doctype="Fee Charge") for r in fees]
	data.sort(key=lambda r: (str(r["date"]), r["document"]))
	if data:
		data.append(total_row("document", _("Total"), data, ("amount",)))
	return {"columns": [col("Type", "kind", width=70), col("Document", "document", "Dynamic Link", 140, "doctype"),
	                    col("Date", "date", "Date", 90), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Consumer", "consumer_name", width=150), col("Amount", "amount", "Currency", 100),
	                    col("Mode / Fee", "detail", width=140), col("Reason", "reason", "Link", 110, "Reason Code"),
	                    col("Explanation", "remarks", width=240), col("Taken By", "taken_by", "Link", 150, "User"),
	                    col("Reversed By", "reversed_by", "Link", 150, "User"), col("Reversed On", "reversed_on", "Datetime", 140)],
	        "data": data}


# ================================================================ 16. arrears ageing (RPT-23)
AGE_FILTERS = [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
               dict(fieldname="as_of", label="As Of", fieldtype="Date", default="@today"),
               dict(fieldname="consumer_category", label="Category", fieldtype="Link", options="Consumer Category"),
               dict(fieldname="block", label="Block", fieldtype="Data")]


@report("arrears_ageing", "Arrears Ageing", RECOVERY, AGE_FILTERS,
        "What consumers owe, in bands of days past the due date: 0-30, 31-60, 61-90, 91-180 and over 180.",
        ref_doctype="Customer Ledger Entry")
def arrears_ageing(f):
	s, as_of = society_of(f), getdate(f.get("as_of") or nowdate())
	acc = OrderedDict()
	for r, parts in aging_for(s, as_of, f.get("consumer_category"), f.get("block")):
		k = (r.block, r.category)
		row = acc.setdefault(k, {"block": r.block, "category": r.category, "accounts": 0, **{b: 0 for b in BANDS}})
		row["accounts"] += 1
		for age, amount in parts:
			row[band_of(age)] += amount
	data = sorted(acc.values(), key=lambda r: (r["block"], r["category"] or ""))
	for r in data:
		for b in BANDS:
			r[b] = round(r[b], 2)
		r["total"] = round(sum(r[b] for b in BANDS), 2)
	if data:
		data.append(total_row("block", _("Total"), data, ("accounts",) + BANDS + ("total",)))
	money = lambda label, k: col(label, k, "Currency", 120)  # noqa: E731
	labels = [("0-30 days", BANDS[0]), ("31-60 days", BANDS[1]), ("61-90 days", BANDS[2]), ("91-180 days", BANDS[3]), ("Over 180 days", BANDS[4])]
	chart = None
	if data:
		tot = data[-1]
		chart = {"data": {"labels": [l for l, _k in labels], "datasets": [{"name": _("Owed"), "values": [tot[k] for _l, k in labels]}]},
		         "type": "bar", "fieldtype": "Currency"}
	return {"columns": [col("Block", "block", width=100), col("Category", "category", width=150), col("Accounts", "accounts", "Int", 90)] +
	                   [money(l, k) for l, k in labels] + [money("Total Owed", "total")], "data": data, "chart": chart,
	        "message": _("Age = days past the bill's due date (surcharge and fees from the day they were charged). Payments are taken to clear the oldest charges first, so what is still owed is aged from the newest charges.")}


# ================================================================ 17. defaulters (RPT-24)
@report("defaulters", "Defaulters", RECOVERY,
        AGE_FILTERS[:2] + [dict(fieldname="min_amount", label="Owing At Least", fieldtype="Currency", default="1"),
                           dict(fieldname="min_days", label="Overdue At Least (days)", fieldtype="Int", default="1")] + AGE_FILTERS[2:],
        "Accounts owing at least an amount and overdue at least some days, largest first. Disconnection lists and recovery targets come with recovery.",
        ref_doctype="Customer Ledger Entry")
def defaulters(f):
	s, as_of = society_of(f), getdate(f.get("as_of") or nowdate())
	min_amount, min_days = flt(f.get("min_amount")), cint(f.get("min_days"))
	last_paid = {r.connection: r.d for r in sql("""select connection, max(posting_date) d from `tabCustomer Ledger Entry`
	             where society = %(s)s and entry_type = 'Payment' and ifnull(reversed_by, '') = '' and posting_date <= %(a)s group by connection""",
	                                          {"s": s, "a": as_of})}
	data = []
	for r, parts in aging_for(s, as_of, f.get("consumer_category"), f.get("block")):
		overdue = [(age, amt) for age, amt in parts if age > 0]
		oldest = max((age for age, _amt in overdue), default=0)
		if flt(r.balance) < min_amount or oldest < min_days:
			continue
		data.append({"connection": r.connection, "consumer_name": r.consumer_name, "property_no": r.property_no, "block": r.block,
		             "category": r.category, "mobile_no": r.mobile_no, "owed": flt(r.balance),
		             "overdue": round(sum(a for _age, a in overdue), 2), "oldest": oldest, "last_paid": last_paid.get(r.connection)})
	data.sort(key=lambda r: (-r["owed"], -r["oldest"]))
	if data:
		data.append(total_row("connection", _("Total"), data, ("owed", "overdue")))
	return {"columns": [col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=160),
	                    col("Property", "property_no", width=80), col("Block", "block", width=80), col("Category", "category", width=120),
	                    col("Mobile", "mobile_no", width=110), col("Total Owed", "owed", "Currency", 110),
	                    col("Of Which Overdue", "overdue", "Currency", 120), col("Oldest Overdue (days)", "oldest", "Int", 130),
	                    col("Last Payment", "last_paid", "Date", 100)], "data": data}


# ================================================================ 18. credit balances (RPT-26)
@report("credit_balances", "Credit Balances and Advance Payments", RECOVERY, AGE_FILTERS[:1] + AGE_FILTERS[2:],
        "Accounts holding credit: money paid in advance or in excess.", ref_doctype="Customer Ledger Entry")
def credit_balances(f):
	s = society_of(f)
	last_paid = {r.connection: r.d for r in sql("""select connection, max(posting_date) d from `tabCustomer Ledger Entry`
	             where society = %(s)s and entry_type = 'Payment' and ifnull(reversed_by, '') = '' group by connection""", {"s": s})}
	data = [{"connection": r.connection, "consumer_name": r.consumer_name, "block": r.block, "category": r.category,
	         "credit": flt(r.credit_balance), "owed": flt(r.balance), "last_paid": last_paid.get(r.connection)}
	        for r in account_rows(s) if flt(r.credit_balance) > 0 and (not f.get("consumer_category") or r.consumer_category == f.consumer_category)
	        and (not f.get("block") or r.block == f.block)]
	data.sort(key=lambda r: -r["credit"])
	if data:
		data.append(total_row("connection", _("Total"), data, ("credit",)))
	return {"columns": [col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=170),
	                    col("Block", "block", width=80), col("Category", "category", width=130), col("Credit Held", "credit", "Currency", 120),
	                    col("Net Balance (− = in credit)", "owed", "Currency", 150), col("Last Payment", "last_paid", "Date", 100)],
	        "data": data}


# ================================================================ 19. consumer census (RPT-35)
@report("consumer_census", "Consumer Census", BILLING,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="utility", label="Utility", fieldtype="Link", options="Utility Type", default="Electricity")],
        "Connections by category and block and status, with sanctioned load.", ref_doctype="Connection")
def consumer_census(f):
	s = society_of(f)
	rows = sql(f"""select ifnull(cc.category_name, c.consumer_category) category, {BLOCK} block,
			sum(c.status = 'Active') active, sum(c.status = 'Applied') applied, sum(c.status = 'Disconnected') disconnected,
			sum(c.status = 'Closed') closed, count(*) total, sum(case when c.status = 'Active' then c.sanctioned_load_kw else 0 end) load_kw
		from `tabConnection` c left join `tabProperty` p on p.name = c.property left join `tabConsumer Category` cc on cc.name = c.consumer_category
		where c.society = %(s)s and (%(u)s = '' or c.utility = %(u)s) group by 1, 2 order by 1, 2""", {"s": s, "u": f.get("utility") or ""})
	data = [{"category": r.category, "block": r.block, "active": cint(r.active), "applied": cint(r.applied),
	         "disconnected": cint(r.disconnected), "closed": cint(r.closed), "total": cint(r.total), "load_kw": flt(r.load_kw)} for r in rows]
	if data:
		data.append(total_row("category", _("Total"), data, ("active", "applied", "disconnected", "closed", "total", "load_kw")))
	return {"columns": [col("Category", "category", width=160), col("Block", "block", width=100), col("Active", "active", "Int", 80),
	                    col("Applied", "applied", "Int", 80), col("Disconnected", "disconnected", "Int", 100), col("Closed", "closed", "Int", 80),
	                    col("Total", "total", "Int", 80), col("Sanctioned Load of Active (kW)", "load_kw", "Float", 170)], "data": data,
	        "message": _("Connected load isn't recorded separately yet, so only the sanctioned load is shown.")}


# ================================================================ 20. pending approvals (RPT-41)
PENDING = (("Tariff", "tariff_title", "General Manager"), ("Tariff Adjustment", "adjustment_title", "General Manager"),
           ("Bill Run", "run_title", "Billing Manager"), ("Account Adjustment", "adjustment_title", "General Manager"))


@report("pending_approvals", "Pending Approvals", APPROVERS,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="transaction", label="Transaction", fieldtype="Select", options="\nTariff\nTariff Adjustment\nBill Run\nAccount Adjustment"),
         dict(fieldname="older_than", label="Waiting More Than (days)", fieldtype="Int", default="0")],
        "Everything waiting for approval, by type and how long it has waited.", ref_doctype="Tariff")
def pending_approvals(f):
	s, today = society_of(f), getdate(nowdate())
	data = []
	for dt, title, role in PENDING:
		if f.get("transaction") and f.transaction != dt:
			continue
		for r in sql(f"""select name, {title} title, requested_by, requested_on from `{'tab' + dt}`
		                where society = %(s)s and workflow_state = 'Pending Approval' and docstatus = 0 order by requested_on""", {"s": s}):
			age = (today - getdate(r.requested_on)).days if r.requested_on else 0
			if age >= cint(f.get("older_than")):
				data.append({"doctype": dt, "document": r.name, "title": r.title, "requested_by": r.requested_by,
				             "requested_on": r.requested_on, "age": age, "waiting_for": role})
	data.sort(key=lambda r: -r["age"])
	return {"columns": [col("Transaction", "doctype", width=140), col("Document", "document", "Dynamic Link", 150, "doctype"),
	                    col("Title", "title", width=260), col("Submitted By", "requested_by", "Link", 160, "User"),
	                    col("Submitted On", "requested_on", "Datetime", 140), col("Waiting (days)", "age", "Int", 110),
	                    col("Waiting For", "waiting_for", width=130)], "data": data}


# ================================================================ 21. audit log (RPT-42)
AUDITED = ("Society", "Tariff", "Tariff Adjustment", "Bill Run", "Account Adjustment", "Fee Type", "Charge Type", "Consumer Category",
           "Cash Counter", "Reason Code", "Ledger Account", "Journal Export Layout", "Consumer", "Property", "Connection", "Meter",
           "Reading Cycle", "Meter Reading", "Cashier Day", "KPI Definition")

@report("audit_log", "Audit Log and User Activity", AUDIT,
        [dict(fieldname="show", label="Show", fieldtype="Select", options="Changes to Documents\nLogins and Logouts", default="Changes to Documents"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today"),
         # Plain fields, not links: an auditor can't look up User or DocType records.
         dict(fieldname="user", label="User (email)", fieldtype="Data"),
         dict(fieldname="doctype", label="Document Type", fieldtype="Select", options="\n" + "\n".join(AUDITED))],
        "Who changed which document and what changed, and who logged in.", ref_doctype="Utility Bill")
def audit_log(f):
	a, b = period(f, month_start(nowdate()))
	v = {"a": a, "b": add_days(b, 1), "u": f.get("user"), "d": f.get("doctype")}
	if f.get("show") == "Logins and Logouts":
		rows = sql("""select creation, user, full_name, operation, status, ip_address from `tabActivity Log`
		             where operation in ('Login', 'Logout') and creation >= %(a)s and creation < %(b)s
		               and (%(u)s is null or user = %(u)s) order by creation desc limit 2000""", v)
		return {"columns": [col("When", "creation", "Datetime", 150), col("User", "user", "Link", 190, "User"),
		                    col("Name", "full_name", width=150), col("Action", "operation", width=80),
		                    col("Result", "status", width=80), col("IP Address", "ip_address", width=120)], "data": rows}
	rows = sql("""select v.creation, v.owner user, v.ref_doctype, v.docname, v.data from `tabVersion` v
	             where v.creation >= %(a)s and v.creation < %(b)s and (%(u)s is null or v.owner = %(u)s)
	               and v.ref_doctype in (select name from `tabDocType` where module = 'Utility Billing')
	               and (%(d)s is null or v.ref_doctype = %(d)s) order by v.creation desc limit 2000""", v)
	for r in rows:
		r["summary"] = version_summary(r.ref_doctype, r.pop("data"))
	return {"columns": [col("When", "creation", "Datetime", 150), col("User", "user", "Link", 190, "User"),
	                    col("Document Type", "ref_doctype", width=150), col("Document", "docname", "Dynamic Link", 150, "ref_doctype"),
	                    col("What Changed", "summary", width=460)], "data": rows,
	        "message": _("Shows the latest 2000 changes. Posted ledger entries, journals and receipts can't change, so their history is the document itself.")}


def version_summary(doctype, raw) -> str:
	try:
		d = json.loads(raw or "{}")
	except ValueError:
		return ""
	meta = frappe.get_meta(doctype)
	label = lambda fn: meta.get_label(fn) or fn  # noqa: E731
	parts = []
	for fieldname, old, new in d.get("changed") or []:
		if fieldname in ("modified", "modified_by", "submitted_snapshot", "approval_log"):
			continue
		parts.append(f"{label(fieldname)}: {_short(old)} → {_short(new)}")
	hidden = ("approval_log",)  # internal history rows; the Approval Status change already tells the story
	for table, row in d.get("added") or []:
		if table not in hidden:
			parts.append(f"+ {label(table)}")
	for table, row in d.get("removed") or []:
		if table not in hidden:
			parts.append(f"− {label(table)}")
	for table, *_rest in d.get("row_changed") or []:
		if table not in hidden:
			parts.append(f"{label(table)} edited")
	text = "; ".join(parts)
	return text[:400] + ("…" if len(text) > 400 else "")


def _short(v):
	v = "" if v is None else str(v)
	return v if len(v) <= 40 else v[:37] + "…"
