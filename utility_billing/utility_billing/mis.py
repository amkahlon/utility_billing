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

from utility_billing.utility_billing import allocation, tariff_engine

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
        "Accounts owing at least an amount and overdue at least some days, largest first, with the latest recovery stage, case and officer.",
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
	from utility_billing.utility_billing import recovery

	state = {r["connection"]: r for r in recovery.accounts(s, as_of, f.get("block"), f.get("consumer_category"), include_all=True)}
	for r in data:
		st = state.get(r["connection"], {})
		r.update(last_stage=st.get("last_stage"), next_stage=st.get("next_stage"), case_status=st.get("case_status"),
		         assigned_to=st.get("assigned_to"), hold=st.get("hold"))
	data.sort(key=lambda r: (-r["owed"], -r["oldest"]))
	if data:
		data.append(total_row("connection", _("Total"), data, ("owed", "overdue")))
	return {"columns": [col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=160),
	                    col("Property", "property_no", width=80), col("Block", "block", width=80), col("Category", "category", width=120),
	                    col("Mobile", "mobile_no", width=110), col("Total Owed", "owed", "Currency", 110),
	                    col("Of Which Overdue", "overdue", "Currency", 120), col("Oldest Overdue (days)", "oldest", "Int", 130),
	                    col("Last Payment", "last_paid", "Date", 100), col("Latest Stage", "last_stage", width=130),
	                    col("Next Stage Due", "next_stage", width=130), col("Case", "case_status", width=110),
	                    col("Assigned To", "assigned_to", "Link", 150, "User"), col("On Hold", "hold", width=170)], "data": data,
	        "message": _("Latest stage, case and hold come from Defaulters and Recovery. Stage rules and the defaulter amount and days are set on the Society.")}


# ================================================================ 17b. recovery (RPT-24: disconnection lists, recovery against target)
@report("disconnection_list", "Disconnection List", RECOVERY,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="status", label="Order Status", fieldtype="Select", options="\nAwaiting Approval\nIssued")],
        "Accounts with a disconnection order that has not been cancelled: the list the field team works from.",
        ref_doctype="Recovery Notice")
def disconnection_list(f):
	s = society_of(f)
	rows = sql("""select n.name, n.connection, n.consumer_name, n.block, n.consumer_category category, n.issue_date, n.status, n.owed,
	                    n.oldest_days, n.approved_by, c.status connection_status, co.mobile_no
	              from `tabRecovery Notice` n join `tabConnection` c on c.name = n.connection left join `tabConsumer` co on co.name = c.consumer
	              where n.society = %(s)s and n.stage = 'Disconnection Order' and n.status in ('Awaiting Approval', 'Issued')
	                and (%(st)s = '' or n.status = %(st)s) order by n.block, n.owed desc""", {"s": s, "st": f.get("status") or ""})
	data = [dict(r) for r in rows]
	if data:
		data.append(total_row("connection", _("Total"), data, ("owed",)))
	return {"columns": [col("Order", "name", "Link", 130, "Recovery Notice"), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Consumer", "consumer_name", width=160), col("Block", "block", width=80), col("Category", "category", width=120),
	                    col("Mobile", "mobile_no", width=110), col("Owed", "owed", "Currency", 110), col("Overdue (days)", "oldest_days", "Int", 110),
	                    col("Ordered On", "issue_date", "Date", 100), col("Status", "status", width=130),
	                    col("Approved By", "approved_by", "Link", 150, "User"), col("Connection Status", "connection_status", width=130)],
	        "data": data,
	        "message": _("An order marked Awaiting Approval has not been approved by a second user yet. Carrying out the disconnection in the field comes with the work-order workflow.")}


@report("recovery_vs_target", "Recovery against Target", RECOVERY,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="year", label="Year", fieldtype="Int")],
        "Each month's recovery target against what payments took off arrears, surcharge and fees.", ref_doctype="Recovery Target")
def recovery_vs_target(f):
	from utility_billing.utility_billing import recovery

	s = society_of(f)
	year = cint(f.get("year")) or getdate(nowdate()).year
	data = []
	for m in range(1, 13):
		first = getdate(f"{year}-{m:02d}-01")
		if first > getdate(nowdate()) and not recovery.target_of(s, first):
			continue
		p = recovery.target_progress(s, first)
		data.append({"month": first.strftime("%b %Y"), "target": p["target"], "recovered": p["recovered"],
		             "percent": p["percent"] if p["percent"] is not None else None, "remaining": p["remaining"] if p["target"] else None,
		             "status": _("No target set") if not p["target"] else (_("Reached") if p["reached"] else _("Below target"))})
	tot_t, tot_r = sum(r["target"] for r in data), sum(r["recovered"] for r in data)
	chart = None
	if data:
		chart = {"data": {"labels": [r["month"] for r in data], "datasets": [{"name": _("Target"), "values": [r["target"] for r in data]},
		                                                                   {"name": _("Recovered"), "values": [r["recovered"] for r in data]}]},
		         "type": "bar", "fieldtype": "Currency"}
	return {"columns": [col("Month", "month", width=100), col("Target", "target", "Currency", 120), col("Recovered", "recovered", "Currency", 120),
	                    col("Reached (%)", "percent", "Percent", 100), col("Still To Recover", "remaining", "Currency", 130), col("Result", "status", width=120)],
	        "data": data, "chart": chart,
	        "summary": [{"value": tot_t, "label": _("Targets this year"), "datatype": "Currency", "indicator": "Blue"},
	                    {"value": tot_r, "label": _("Recovered this year"), "datatype": "Currency", "indicator": "Green"}],
	        "message": _("Recovered = payments taken against arrears, surcharge and fees (not the current bill), leaving out reversed payments. Set the targets in Recovery Target.")}


@report("recovery_notices", "Recovery Notices Register", RECOVERY,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today"),
         dict(fieldname="stage", label="Stage", fieldtype="Select", options="\nReminder\nNotice\nFinal Notice\nDisconnection Order"),
         dict(fieldname="block", label="Block", fieldtype="Data")],
        "Every reminder, notice, final notice and disconnection order issued, with its date and channel.", ref_doctype="Recovery Notice")
def recovery_notices(f):
	s = society_of(f)
	start, end = period(f, get_first_day(nowdate()))
	where = ""
	vals = {"s": s, "a": start, "b": end}
	for k in ("stage", "block"):
		if f.get(k):
			where += f" and n.{k} = %({k})s"
			vals[k] = f[k]
	data = [dict(r) for r in sql(f"""select n.name, n.issue_date, n.connection, n.consumer_name, n.block, n.stage, n.channel, n.status, n.owed,
	                    n.oldest_days, n.recovery_run from `tabRecovery Notice` n
	             where n.society = %(s)s and n.issue_date between %(a)s and %(b)s{where} order by n.issue_date, n.name""", vals)]
	by_stage = {}
	for r in data:
		if r["status"] != "Cancelled":
			by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
	return {"columns": [col("Notice", "name", "Link", 130, "Recovery Notice"), col("Date", "issue_date", "Date", 100),
	                    col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=160), col("Block", "block", width=80),
	                    col("Stage", "stage", width=140), col("Channel", "channel", width=90), col("Status", "status", width=130),
	                    col("Owed", "owed", "Currency", 110), col("Overdue (days)", "oldest_days", "Int", 110),
	                    col("Run", "recovery_run", "Link", 130, "Recovery Run")], "data": data,
	        "summary": [{"value": n, "label": _(stage), "datatype": "Int", "indicator": "Orange"} for stage, n in by_stage.items()],
	        "message": _("Reconnections are recorded with the work-order workflow, which comes later.")}


# ================================================================ 17c. messages, settlement, suspense, refunds (increment 12)
PAY_ROLES = FINANCE + ["Society Admin"]


@report("message_cost", "Message Cost by Channel", PAY_ROLES,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today")],
        "Messages and their cost per channel: how many went out, were delivered, failed or wait.", ref_doctype="Message Log")
def message_cost(f):
	s = society_of(f)
	start, end = period(f, get_first_day(nowdate()))
	rows = sql("""select channel, count(*) total, sum(status in ('Sent', 'Delivered', 'Read')) sent, sum(status in ('Delivered', 'Read')) delivered,
	                    sum(status = 'Read') is_read, sum(status = 'Failed') failed, sum(status = 'Skipped') skipped, sum(status = 'Queued') waiting,
	                    sum(parts) parts, sum(cost) cost from `tabMessage Log`
	             where society = %(s)s and date(creation) between %(a)s and %(b)s group by channel order by channel""", {"s": s, "a": start, "b": end})
	data = [{"channel": r.channel, "total": cint(r.total), "sent": cint(r.sent), "delivered": cint(r.delivered), "read": cint(r.is_read),
	         "failed": cint(r.failed), "skipped": cint(r.skipped), "waiting": cint(r.waiting), "parts": cint(r.parts), "cost": round(flt(r.cost), 2)} for r in rows]
	if data:
		data.append(total_row("channel", _("Total"), data, ("total", "sent", "delivered", "read", "failed", "skipped", "waiting", "parts", "cost")))
	wa = frappe.db.get_value("Society", s, "whatsapp_enabled")
	return {"columns": [col("Channel", "channel", width=110), col("Messages", "total", "Int", 100), col("Sent", "sent", "Int", 80),
	                    col("Delivered", "delivered", "Int", 90), col("Read", "read", "Int", 70), col("Failed", "failed", "Int", 80),
	                    col("Skipped", "skipped", "Int", 80), col("Waiting", "waiting", "Int", 80), col("SMS Parts", "parts", "Int", 90),
	                    col("Cost (Rs)", "cost", "Currency", 110)], "data": data,
	        "message": _("WhatsApp is {0} for this society. Skipped = the consumer accepts none of the rule's channels. Waiting = no provider set up yet, or held by quiet hours or a retry.").format(_("on") if cint(wa) else _("switched off"))}


@report("settlement_exceptions", "Settlement Exceptions", PAY_ROLES,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today"),
         dict(fieldname="open_only", label="Open Items Only", fieldtype="Check", default=1)],
        "Rows of bank and channel settlement files that don't match the recorded payments, and what was done about them.", ref_doctype="Settlement File")
def settlement_exceptions(f):
	s = society_of(f)
	start, end = period(f, get_first_day(nowdate()))
	data = [dict(r) for r in sql(
		"""select sf.name file, sf.file_date, sf.mode, sf.status file_status, r.txn_id, r.reference_no, r.paid_on, r.amount, r.recorded_amount,
		          r.result, r.action_taken, r.remarks, r.receipt, r.suspense
		   from `tabSettlement Row` r join `tabSettlement File` sf on sf.name = r.parent
		   where sf.society = %(s)s and sf.file_date between %(a)s and %(b)s and r.result != 'Matched'
		     and (%(open)s = 0 or (ifnull(r.action_taken, '') = '' and ifnull(r.remarks, '') = ''))
		   order by sf.file_date, sf.name, r.idx""", {"s": s, "a": start, "b": end, "open": cint(f.get("open_only", 1))})]
	for r in data:
		r["difference"] = round(flt(r["amount"]) - flt(r["recorded_amount"]), 2) if r["result"] in ("Amount differs",) else None
	return {"columns": [col("File", "file", "Link", 130, "Settlement File"), col("File Date", "file_date", "Date", 100), col("Channel", "mode", width=100),
	                    col("Transaction ID", "txn_id", width=150), col("Reference No", "reference_no", width=120), col("Amount in File", "amount", "Currency", 120),
	                    col("Recorded", "recorded_amount", "Currency", 110), col("Difference", "difference", "Currency", 100), col("Result", "result", width=170),
	                    col("Action", "action_taken", width=90), col("Remarks", "remarks", width=200), col("Receipt", "receipt", "Link", 130, "Payment Receipt"),
	                    col("Suspense", "suspense", "Link", 130, "Suspense Payment")], "data": data,
	        "message": _("A row stays open until it has an action (posted or held in suspense) or a remark. A file can be closed only when none are open.")}


@report("suspense_payments", "Suspense Payments", PAY_ROLES,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="as_of", label="As Of", fieldtype="Date", default="@today"),
         dict(fieldname="status", label="Status", fieldtype="Select", options="Held\nMatched\nReturned to Payer\nAll", default="Held")],
        "Payments received with a wrong or missing reference number, oldest first, until they are matched or returned.", ref_doctype="Suspense Payment")
def suspense_payments(f):
	s, as_of = society_of(f), getdate(f.get("as_of") or nowdate())
	status = f.get("status") or "Held"
	data = [dict(r) for r in sql(
		"""select name, payment_date, mode, channel_txn_id, reference_text, amount, status, matched_connection, payment_receipt, settlement_file
		   from `tabSuspense Payment` where society = %(s)s and (%(st)s = 'All' or status = %(st)s) order by payment_date, name""", {"s": s, "st": status})]
	for r in data:
		r["age"] = max((as_of - getdate(r["payment_date"])).days, 0)
	if data:
		data.append(total_row("name", _("Total"), data, ("amount",)))
	return {"columns": [col("Suspense", "name", "Link", 130, "Suspense Payment"), col("Paid On", "payment_date", "Date", 100), col("Age (days)", "age", "Int", 90),
	                    col("Paid By", "mode", width=100), col("Transaction ID", "channel_txn_id", width=150), col("Reference Given", "reference_text", width=140),
	                    col("Amount", "amount", "Currency", 110), col("Status", "status", width=120), col("Matched To", "matched_connection", "Link", 110, "Connection"),
	                    col("Receipt", "payment_receipt", "Link", 130, "Payment Receipt"), col("File", "settlement_file", "Link", 130, "Settlement File")], "data": data}


@report("refunds_register", "Refunds Register", PAY_ROLES,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today")],
        "Refunds of credit balances with their reason, payment details and approval.", ref_doctype="Refund")
def refunds_register(f):
	s = society_of(f)
	start, end = period(f, get_first_day(nowdate()))
	data = [dict(r) for r in sql(
		"""select rf.name, date(rf.creation) created, rf.connection, rf.consumer_name, rf.amount, rf.reason_code, rf.refund_mode, rf.payee_name,
		          rf.instrument_no, rf.workflow_state, rf.requested_by, rf.approved_by, rf.posting_date
		   from `tabRefund` rf where rf.society = %(s)s and rf.docstatus < 2 and date(rf.creation) between %(a)s and %(b)s order by rf.creation""",
		{"s": s, "a": start, "b": end})]
	if data:
		data.append(total_row("name", _("Total"), data, ("amount",)))
	return {"columns": [col("Refund", "name", "Link", 130, "Refund"), col("Created", "created", "Date", 100), col("Account", "connection", "Link", 110, "Connection"),
	                    col("Consumer", "consumer_name", width=160), col("Amount", "amount", "Currency", 110), col("Reason", "reason_code", "Link", 120, "Reason Code"),
	                    col("Paid By", "refund_mode", width=100), col("Payee", "payee_name", width=140), col("Cheque / Account", "instrument_no", width=130),
	                    col("Status", "workflow_state", width=130), col("Requested By", "requested_by", "Link", 150, "User"),
	                    col("Approved By", "approved_by", "Link", 150, "User"), col("Posted On", "posting_date", "Date", 100)], "data": data}


# ================================================================ 17d. corrections register (increment 13)
@report("corrections_register", "Corrections Register", PAY_ROLES + ["Billing Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today"),
         dict(fieldname="kind", label="Kind", fieldtype="Select", options="\nBill revision\nDue date extension\nBill dispute\nBulk adjustment\nInstallment plan\nWrite-off")],
        "Revised bills, due date extensions, bill disputes and bulk adjustments: what was corrected, by whom, and the effect.", ref_doctype="Bill Revision")
def corrections_register(f):
	s = society_of(f)
	start, end = period(f, get_first_day(nowdate()))
	v = {"s": s, "a": start, "b": end}
	data = []
	for r in sql("""select name, date(creation) d, connection, consumer_name, difference amount, kind detail, workflow_state status, requested_by by_user,
	                      original_bill ref, revised_bill ref2, reason_code from `tabBill Revision`
	               where society = %(s)s and docstatus < 2 and date(creation) between %(a)s and %(b)s""", v):
		data.append({"kind": "Bill revision", "name": r.name, "date": r.d, "connection": r.connection, "consumer_name": r.consumer_name,
		             "amount": flt(r.amount), "detail": f"{r.detail}: {r.ref} → {r.ref2 or 'not yet revised'}", "status": r.status, "by_user": r.by_user, "reason": r.reason_code})
	for r in sql("""select name, date(creation) d, connection, consumer_name, days_extended, old_due_date, new_due_date, reason_code, granted_by
	               from `tabDue Date Extension` where society = %(s)s and date(creation) between %(a)s and %(b)s""", v):
		data.append({"kind": "Due date extension", "name": r.name, "date": r.d, "connection": r.connection, "consumer_name": r.consumer_name, "amount": None,
		             "detail": f"+{r.days_extended} days: {formatdate(r.old_due_date)} → {formatdate(r.new_due_date)}", "status": "Granted", "by_user": r.granted_by, "reason": r.reason_code})
	for r in sql("""select name, date(creation) d, connection, consumer_name, disputed_amount, status, reason, resolved_by, owner from `tabBill Dispute`
	               where society = %(s)s and date(creation) between %(a)s and %(b)s""", v):
		data.append({"kind": "Bill dispute", "name": r.name, "date": r.d, "connection": r.connection, "consumer_name": r.consumer_name, "amount": flt(r.disputed_amount),
		             "detail": r.reason, "status": r.status, "by_user": r.resolved_by or r.owner, "reason": None})
	for r in sql("""select name, date(creation) d, note_type, audience, accounts, total_amount, workflow_state, requested_by, reason_code
	               from `tabBulk Adjustment` where society = %(s)s and docstatus < 2 and date(creation) between %(a)s and %(b)s""", v):
		data.append({"kind": "Bulk adjustment", "name": r.name, "date": r.d, "connection": None, "consumer_name": f"{cint(r.accounts)} accounts ({r.audience})",
		             "amount": flt(r.total_amount) * (1 if r.note_type == "Debit Note" else -1), "detail": r.note_type, "status": r.workflow_state,
		             "by_user": r.requested_by, "reason": r.reason_code})
	for r in sql("""select name, date(creation) d, connection, consumer_name, amount_to_split, installments, down_payment, plan_status, workflow_state, requested_by
	               from `tabInstallment Plan` where society = %(s)s and docstatus < 2 and date(creation) between %(a)s and %(b)s""", v):
		data.append({"kind": "Installment plan", "name": r.name, "date": r.d, "connection": r.connection, "consumer_name": r.consumer_name, "amount": flt(r.amount_to_split),
		             "detail": f"{cint(r.installments)} installments after a down payment of {flt(r.down_payment):,.0f}", "status": r.plan_status if r.workflow_state == "Approved" else r.workflow_state,
		             "by_user": r.requested_by, "reason": None})
	for r in sql("""select name, date(creation) d, connection, consumer_name, amount, workflow_state, requested_by, reason_code, resolution_no
	               from `tabWrite Off` where society = %(s)s and docstatus < 2 and date(creation) between %(a)s and %(b)s""", v):
		data.append({"kind": "Write-off", "name": r.name, "date": r.d, "connection": r.connection, "consumer_name": r.consumer_name, "amount": -flt(r.amount),
		             "detail": f"Board resolution {r.resolution_no}", "status": r.workflow_state, "by_user": r.requested_by, "reason": r.reason_code})
	if f.get("kind"):
		data = [d for d in data if d["kind"] == f.kind]
	data.sort(key=lambda d: (d["date"], d["name"]))
	return {"columns": [col("Kind", "kind", width=140), col("Document", "name", width=140), col("Date", "date", "Date", 100),
	                    col("Account", "connection", "Link", 110, "Connection"), col("Consumer", "consumer_name", width=170),
	                    col("Amount (+ owed, - credited)", "amount", "Currency", 150), col("What", "detail", width=300), col("Status", "status", width=130),
	                    col("By", "by_user", "Link", 150, "User"), col("Reason", "reason", "Link", 120, "Reason Code")], "data": data,
	        "message": _("For a revision the amount is the change in the current bill; for a dispute, the amount held; for a bulk adjustment, the total across the accounts.")}


# ================================================================ 17e. taxes (increment 14; FS-REC-17, tax register)
@report("tax_register", "Tax Register and Taxes Payable", PAY_ROLES,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From Month", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="To Month", fieldtype="Date", default="@today"),
         dict(fieldname="as_of", label="Collected As Of", fieldtype="Date", default="@today"),
         dict(fieldname="group_by", label="Group By", fieldtype="Select", options="Tax\nTax and Category", default="Tax")],
        "Tax billed and collected by tax type (and category), from the posted bills of the months, for filing.", ref_doctype="Utility Bill")
def tax_register(f):
	s = society_of(f)
	a, z = month_start(f.get("from_date") or nowdate()), get_last_day(f.get("to_date") or nowdate())
	as_of = getdate(f.get("as_of") or nowdate())
	bills = sql("""select bt.parent bill, bt.tax, bt.tax_name, bt.base, bt.amount, bt.inclusive, b.consumer_category category, b.connection, b.issue_date,
	                     b.payable_within_due payable from `tabBill Tax` bt join `tabUtility Bill` b on b.name = bt.parent
	              where b.society = %(s)s and b.docstatus = 1 and b.status = 'Posted' and b.billing_month between %(a)s and %(z)s and bt.amount != 0""",
	            {"s": s, "a": a, "z": z})
	# exact: each account's payments up to the date are allocated oldest bill first (allocation.py)
	paid = {e.connection: flt(e.paid) for e in sql(
		"""select connection, sum(-amount) paid from `tabCustomer Ledger Entry` where society = %(s)s and entry_type = 'Payment'
		   and ifnull(reversed_by, '') = '' and posting_date <= %(as_of)s group by connection""", {"s": s, "as_of": as_of})}
	everything = defaultdict(list)
	for x in sql("""select name, connection, current_amount from `tabUtility Bill` where society = %(s)s and docstatus = 1 and status = 'Posted'
	               and billing_month <= %(z)s order by connection, issue_date, name""", {"s": s, "z": z}):
		everything[x.connection].append((x.name, flt(x.current_amount)))
	fraction = {}
	for conn, rows in everything.items():
		for (name, _amt), sh in zip(rows, allocation.shares(paid.get(conn, 0), [amt for _n, amt in rows]), strict=True):
			fraction[name] = float(sh)
	by_cat = f.get("group_by") == "Tax and Category"
	acc = OrderedDict()
	for b in bills:
		share = fraction.get(b.bill, 0.0)
		k = (b.tax, b.category if by_cat else "")
		row = acc.setdefault(k, {"tax": b.tax_name, "category": b.category if by_cat else None, "inclusive": cint(b.inclusive), "bills": 0, "base": 0.0, "billed": 0.0, "collected": 0.0})
		row["bills"] += 1
		row["base"] += flt(b.base)
		row["billed"] += flt(b.amount)
		row["collected"] += flt(b.amount) * share
	data = []
	for row in acc.values():
		row = {**row, "base": round(row["base"], 2), "billed": round(row["billed"], 2), "collected": round(row["collected"], 2)}
		row["to_collect"] = round(row["billed"] - row["collected"], 2)
		row["kind"] = _("Included in charges") if row.pop("inclusive") else _("Added to bill")
		data.append(row)
	if data:
		data.append(total_row("tax", _("Total"), data, ("bills", "base", "billed", "collected", "to_collect")))
	return {"columns": [col("Tax", "tax", width=170)] + ([col("Category", "category", width=140)] if by_cat else []) +
	                   [col("How", "kind", width=140), col("Bills", "bills", "Int", 80), col("Charged On", "base", "Currency", 130),
	                    col("Tax Billed", "billed", "Currency", 120), col("Collected", "collected", "Currency", 130), col("Still To Collect", "to_collect", "Currency", 130)],
	        "data": data,
	        "message": _("Payments go to the account, so each account's payments up to the date are allocated to its bills oldest first, each bill taking up to its own charges (surcharge and fees take none); a bill's tax counts as collected in the share of the bill that way paid.")}


# ================================================================ 17f. period control (increment 16; FS-RUN-10, FS-RUN-11)
DELIVERY_STATUSES = ("Printed", "Handed to Distributor", "Delivered", "Sent by Message", "Returned", "Not Delivered")


@report("bill_delivery_status", "Bill Delivery Status", BILLING + ["Meter Reader"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="bill_run", label="Bill Run", fieldtype="Link", options="Bill Run"),
         dict(fieldname="from_date", label="Issued From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="Issued To", fieldtype="Date", default="@today"),
         dict(fieldname="status", label="Delivery Status", fieldtype="Select", options="\nNot recorded\n" + "\n".join(DELIVERY_STATUSES))],
        "Where each issued bill stands: printed, handed to a distributor, delivered, sent by message, by block and route.", ref_doctype="Utility Bill")
def bill_delivery_status(f):
	s = society_of(f)
	cond, values = ["b.society = %(s)s", "b.docstatus = 1"], {"s": s}
	if f.get("bill_run"):
		cond.append("b.bill_run = %(run)s")
		values["run"] = f.bill_run
	else:
		cond.append("b.issue_date between %(a)s and %(z)s")
		values.update(a=getdate(f.get("from_date") or month_start(nowdate())), z=getdate(f.get("to_date") or nowdate()))
	if f.get("status"):
		cond.append("ifnull(nullif(b.delivery_status, ''), 'Not recorded') = %(st)s")
		values["st"] = f.status
	rows = sql(f"""select {BLOCK} block, ifnull(nullif(c.route, ''), '(none)') route, ifnull(nullif(b.delivery_status, ''), 'Not recorded') st,
	                      ifnull(max(b.distributor), '') distributor, count(*) n, max(b.delivery_date) last_date
	               from `tabUtility Bill` b left join `tabProperty` p on p.name = b.property left join `tabConnection` c on c.name = b.connection
	               where {' and '.join(cond)} group by block, route, st order by block, route""", values)
	acc = OrderedDict()
	for r in rows:
		a = acc.setdefault((r.block, r.route), {"block": r.block, "route": r.route, "bills": 0, "not_recorded": 0, "distributor": "", "last_date": None,
		                                         **{frappe.scrub(x): 0 for x in DELIVERY_STATUSES}})
		a["bills"] += r.n
		a["not_recorded" if r.st == "Not recorded" else frappe.scrub(r.st)] += r.n
		a["distributor"] = a["distributor"] or r.distributor
		a["last_date"] = max(filter(None, [a["last_date"], r.last_date]), default=None)
	data = list(acc.values())
	for d in data:
		d["pending"] = d["bills"] - d["delivered"] - d["sent_by_message"] - d["returned"] - d["not_delivered"]
	if data:
		data.append(total_row("block", _("Total"), data, ("bills", "not_recorded", "pending", *[frappe.scrub(x) for x in DELIVERY_STATUSES])))
	return {"columns": [col("Block", "block", width=120), col("Route", "route", width=120), col("Bills", "bills", "Int", 80),
	                    col("Not Recorded", "not_recorded", "Int", 110), col("Printed", "printed", "Int", 90), col("With Distributor", "handed_to_distributor", "Int", 120),
	                    col("Delivered", "delivered", "Int", 90), col("Sent by Message", "sent_by_message", "Int", 120), col("Returned", "returned", "Int", 90),
	                    col("Not Delivered", "not_delivered", "Int", 110), col("Still Out", "pending", "Int", 90), col("Distributor", "distributor", width=130),
	                    col("Last Update", "last_date", "Date", 100)],
	        "data": data,
	        "message": _("Still Out = bills not yet delivered, sent by message, returned or marked not delivered (printed, with a distributor, or not recorded).")}


@report("period_close_status", "Period Close Status", FINANCE,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="year", label="Year", fieldtype="Int")],
        "Each month: open or closed, who closed it and when, the checks that failed or warned, and reopenings.", ref_doctype="Billing Period")
def period_close_status(f):
	s = society_of(f)
	cond, values = ["society = %(s)s"], {"s": s}
	if f.get("year"):
		cond.append("year(period_start) = %(y)s")
		values["y"] = cint(f.year)
	data = []
	for p in sql(f"select name, period_start, status, closed_by, closed_on from `tabBilling Period` where {' and '.join(cond)} order by period_start", values):
		checks = frappe.get_all("Period Check", filters={"parent": p.name}, fields=["result"])
		reopened = frappe.db.count("Period Log", {"parent": p.name, "action": "Reopened"})
		data.append({"period": p.name, "month": p.period_start, "status": p.status, "closed_by": p.closed_by, "closed_on": p.closed_on,
		             "warnings": sum(1 for c in checks if c.result == "Warning"), "failed": sum(1 for c in checks if c.result == "Failed"), "reopened": reopened})
	years = sql("select name, year_start, year_end, status, closing_balance from `tabYear End Close` where society = %(s)s order by year_start", {"s": s})
	msg = _("Year end: {0}.").format("; ".join(f"{formatdate(y.year_start)} to {formatdate(y.year_end)} {y.status.lower()} (owed {flt(y.closing_balance):,.0f})" for y in years)) if years else None
	return {"columns": [col("Period", "period", "Link", 120, "Billing Period"), col("Month", "month", "Date", 100), col("Status", "status", width=80),
	                    col("Closed By", "closed_by", "Link", 150, "User"), col("Closed On", "closed_on", "Datetime", 150), col("Warnings", "warnings", "Int", 90),
	                    col("Failed Checks", "failed", "Int", 110), col("Reopened", "reopened", "Int", 90)], "data": data, "message": msg}


# ================================================================ 17g. consumers (increment 17; FS-MST-10, 15)
@report("consumer_statement", "Consumer Statement (All Accounts)", BILLING + ["Accounts Officer", "Cashier", "Recovery Officer"],
        [dict(fieldname="consumer", label="Consumer", fieldtype="Link", options="Consumer", reqd=1),
         dict(fieldname="from_date", label="From", fieldtype="Date"),
         dict(fieldname="to_date", label="To", fieldtype="Date", default="@today")],
        "One statement over every account of a consumer: each entry by date with the account's balance after it, and the total owed.",
        ref_doctype="Customer Ledger Entry")
def consumer_statement(f):
	from utility_billing.utility_billing import consumer360  # noqa: PLC0415

	if not f.get("consumer"):
		return {"columns": [col("Date", "posting_date", "Date", 100)], "data": [], "message": _("Choose a consumer.")}
	rows = consumer360.combined_statement(f.consumer, f.get("from_date"), f.get("to_date"))
	last = {}
	for r in rows:
		last[r["connection"]] = flt(r["balance"])
	data = [{"posting_date": r["posting_date"], "connection": r["connection"], "entry": _(r["entry_type"]), "voucher": r["voucher_no"],
	         "remarks": r["remarks"], "amount": flt(r["amount"]), "balance": flt(r["balance"])} for r in rows]
	summary = [{"value": len(last), "label": _("Accounts"), "datatype": "Int"},
	           {"value": round(sum(last.values()), 2), "label": _("Owed on all accounts"), "datatype": "Currency", "indicator": "Red" if sum(last.values()) > 0 else "Green"}]
	return {"columns": [col("Date", "posting_date", "Date", 100), col("Account", "connection", "Link", 130, "Connection"), col("Entry", "entry", width=150),
	                    col("Document", "voucher", width=150), col("Remarks", "remarks", width=200), col("Amount", "amount", "Currency", 120),
	                    col("Account Balance", "balance", "Currency", 130)],
	        "data": data, "summary": summary,
	        "message": _("Each account keeps its own balance; Owed on all accounts adds the last balance of each.")}


@report("deposits_held", "Security Deposits Held", FINANCE + ["Billing Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="as_of", label="As Of", fieldtype="Date", default="@today")],
        "Security deposit held per account (received less refunded and offset), with what the account owes, for the liability and for closures.",
        ref_doctype="Security Deposit")
def deposits_held(f):
	s = society_of(f)
	as_of = getdate(f.get("as_of") or nowdate())
	rows = sql("""select d.connection, max(d.consumer_name) consumer_name,
	                     sum(case when d.deposit_type = 'Received' then d.amount else 0 end) received,
	                     sum(case when d.deposit_type = 'Refunded' then d.amount else 0 end) refunded,
	                     sum(case when d.deposit_type = 'Offset Against Arrears' then d.amount else 0 end) offset_amount,
	                     max(d.posting_date) last_date
	              from `tabSecurity Deposit` d where d.society = %(s)s and d.docstatus = 1 and d.posting_date <= %(d)s group by d.connection order by d.connection""",
	           {"s": s, "d": as_of})
	data = []
	for r in rows:
		held = round(flt(r.received) - flt(r.refunded) - flt(r.offset_amount), 2)
		owed = flt(sql("select sum(amount) v from `tabCustomer Ledger Entry` where connection = %(c)s and posting_date <= %(d)s", {"c": r.connection, "d": as_of})[0].v)
		data.append({"connection": r.connection, "consumer_name": r.consumer_name, "received": flt(r.received), "refunded": flt(r.refunded),
		             "offset": flt(r.offset_amount), "held": held, "owed": round(owed, 2), "last_date": r.last_date})
	if data:
		data.append(total_row("connection", _("Total"), data, ("received", "refunded", "offset", "held", "owed")))
	return {"columns": [col("Account", "connection", "Link", 130, "Connection"), col("Consumer", "consumer_name", width=170), col("Received", "received", "Currency", 110),
	                    col("Refunded", "refunded", "Currency", 110), col("Offset", "offset", "Currency", 110), col("Held", "held", "Currency", 110),
	                    col("Account Owes", "owed", "Currency", 120), col("Last Movement", "last_date", "Date", 110)], "data": data,
	        "message": _("Held is the society's liability to the consumers; it should agree with the Security Deposits Held account in the journals.")}


# ================================================================ 17h. network (increment 18; FS-MST-25, 26, 27)
@report("common_area_allocation", "Common Area Costs and Sharing", FINANCE + ["Billing Officer"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="month", label="Month", fieldtype="Date", default="@month_start")],
        "Each common-area facility: what its meter used and cost in the month, how the cost is shared and the share per account.",
        ref_doctype="Common Area Facility")
def common_area_allocation(f):
	from utility_billing.utility_billing import network  # noqa: PLC0415

	s = society_of(f)
	rows = network.allocation(s, f.get("month") or nowdate())
	data = [{**r, "kind": _(r["kind"]), "method": _(r["method"])} for r in rows]
	if data:
		data.append(total_row("facility_name", _("Total"), data, ("units", "amount")))
	return {"columns": [col("Facility", "facility_name", width=170), col("Kind", "kind", width=110), col("Units", "units", "Float", 90),
	                    col("Cost", "amount", "Currency", 110), col("Shared", "method", width=190), col("Accounts", "accounts", "Int", 90),
	                    col("Per Account (equal share)", "per_account", "Currency", 170), col("Per Unit of Basis", "rate", "Float", 130)],
	        "data": data,
	        "message": _("Cost is the facility's own bill for the month. By area and by units: an account's share is Per Unit of Basis × its area or units. Bills don't carry these shares yet.")}


@report("asset_register", "Asset Register and Maintenance", BILLING + ["Society Admin"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="asset_type", label="Kind", fieldtype="Select",
              options="\nMeter\nTransformer\nFeeder\nGrid Station\nLine Section\nPole\nCable\nPump\nStreetlight\nGenerator\nOther"),
         dict(fieldname="status", label="Status", fieldtype="Select", options="\nIn store\nIn service\nUnder repair\nRetired")],
        "Every asset with its make, dates, warranty, status, last maintenance and when the next is due.", ref_doctype="Network Asset")
def asset_register(f):
	from utility_billing.utility_billing import network  # noqa: PLC0415

	s = society_of(f)
	filters = {"society": s}
	for k in ("asset_type", "status"):
		if f.get(k):
			filters[k] = f.get(k)
	today = getdate(nowdate())
	data = []
	for a in frappe.get_all("Network Asset", filters=filters, fields=["name", "asset_name", "asset_type", "status", "make", "model", "serial_no",
	                                                                   "installation_date", "warranty_until", "next_maintenance", "purchase_cost"],
	                        order_by="asset_type, asset_name"):
		last = frappe.db.get_value("Network Asset Maintenance", {"parent": a.name}, "maintenance_date", order_by="maintenance_date desc")
		data.append({**a, "last_maintenance": last, "flag": network.maintenance_status(a.next_maintenance, a.warranty_until, today)})
	link = f"/api/method/utility_billing.utility_billing.network.geojson?society={quote(s)}"
	return {"columns": [col("Asset", "name", "Link", 110, "Network Asset"), col("Name", "asset_name", width=160), col("Kind", "asset_type", width=110),
	                    col("Status", "status", width=100), col("Make / Model", "make", width=130), col("Serial No", "serial_no", width=120),
	                    col("Installed", "installation_date", "Date", 100), col("Warranty Until", "warranty_until", "Date", 110),
	                    col("Last Maintenance", "last_maintenance", "Date", 120), col("Next Due", "next_maintenance", "Date", 100),
	                    col("Attention", "flag", width=170), col("Cost", "purchase_cost", "Currency", 110)],
	        "data": data,
	        "message": _("The society's network on a map: download {0} and open it in a GIS or Google My Maps.").format(f'<a href="{link}">GeoJSON</a>')}


@report("service_requests", "Service Requests and Service Standards", BILLING + ["Recovery Officer", "Society Admin"],
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="Requested From", fieldtype="Date", default="@month_start"),
         dict(fieldname="to_date", label="Requested To", fieldtype="Date", default="@today")],
        "Requests by type: received, open, late, completed, rejected, and the average days to complete against the service standard.",
        ref_doctype="Service Request")
def service_requests(f):
	s = society_of(f)
	a, z = getdate(f.get("from_date") or month_start(nowdate())), getdate(f.get("to_date") or nowdate())
	today = getdate(nowdate())
	acc = OrderedDict()
	for r in sql("""select r.request_type, t.type_name, t.days_allowed, r.status, r.requested_on, r.due_by, r.completed_on
	               from `tabService Request` r join `tabService Request Type` t on t.name = r.request_type
	               where r.society = %(s)s and r.requested_on between %(a)s and %(z)s order by t.type_name""", {"s": s, "a": a, "z": z}):
		row = acc.setdefault(r.request_type, {"type_name": r.type_name, "standard": cint(r.days_allowed), "received": 0, "open": 0, "late": 0,
		                                      "completed": 0, "rejected": 0, "days": []})
		row["received"] += 1
		if r.status in ("Open", "In Progress"):
			row["open"] += 1
			if r.due_by and getdate(r.due_by) < today:
				row["late"] += 1
		elif r.status == "Completed":
			row["completed"] += 1
			row["days"].append((getdate(r.completed_on) - getdate(r.requested_on)).days)
		elif r.status in ("Rejected", "Cancelled"):
			row["rejected"] += 1
	data = []
	for row in acc.values():
		days = row.pop("days")
		row["avg_days"] = round(sum(days) / len(days), 1) if days else None
		row["within"] = pct(sum(1 for d in days if not row["standard"] or d <= row["standard"]), len(days)) if days else None
		data.append(row)
	if data:
		data.append(total_row("type_name", _("Total"), data, ("received", "open", "late", "completed", "rejected")))
	return {"columns": [col("Request", "type_name", width=230), col("Standard (days)", "standard", "Int", 120), col("Received", "received", "Int", 90),
	                    col("Open", "open", "Int", 80), col("Late", "late", "Int", 80), col("Completed", "completed", "Int", 100),
	                    col("Rejected or Cancelled", "rejected", "Int", 150), col("Average Days", "avg_days", "Float", 110),
	                    col("Within Standard %", "within", "Percent", 140)], "data": data}


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
           ("Bill Run", "run_title", "Billing Manager"), ("Account Adjustment", "adjustment_title", "General Manager"), ("Refund", "refund_title", "General Manager"),
           ("Bill Revision", "revision_title", "Billing Manager"), ("Bulk Adjustment", "adjustment_title", "General Manager"),
           ("Installment Plan", "plan_title", "Billing Manager"), ("Write Off", "writeoff_title", "General Manager"))


@report("pending_approvals", "Pending Approvals", APPROVERS,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="transaction", label="Transaction", fieldtype="Select", options="\nTariff\nTariff Adjustment\nBill Run\nAccount Adjustment\nRefund\nBill Revision\nBulk Adjustment\nInstallment Plan\nWrite Off"),
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


# ================================================================ 22-26. line losses and the DISCO bill (increment 10)
LOSS_ROLES = FINANCE + ["Billing Officer"]
MONTH_FILTERS = [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
                 dict(fieldname="month", label="Month", fieldtype="Date", default="@month_start")]
LEVEL_NOTE = "Normal below the society's Watch level, Watch from it, High loss from the High level (Society → Line Loss Alert Levels)."


def _losses():
	from utility_billing.utility_billing import losses  # imported late: losses imports this module

	return losses


@report("line_losses", "Line Losses by Feeder and Transformer", LOSS_ROLES,
        MONTH_FILTERS + [dict(fieldname="group_by", label="Group By", fieldtype="Select", options="Feeder\nTransformer", default="Feeder")],
        "Units that entered each feeder or transformer against the units billed to the connections on it; and the bulk meter against all consumers.",
        ref_doctype="DISCO Bill")
def line_losses(f):
	L = _losses()
	s, month, by = society_of(f), month_start(f.get("month") or nowdate()), f.get("group_by") or "Feeder"
	rows = L.network_losses(s, month, by)
	data = []
	whole = L.society_loss(s, month)
	if whole:
		data.append({"name": _("Whole society: bulk meter against all consumers"), "connections": sum(r["connections"] for r in rows),
		             "input_kwh": whole["input_kwh"], "billed_kwh": whole["billed_kwh"], "loss_kwh": whole["loss_kwh"],
		             "loss_pct": whole["loss_pct"], "status": _(whole["status"])})
	# the engineers' estimate of technical loss on each feeder, from its line sections weighted by length (FS-MST-24)
	est = {}
	if by == "Feeder":
		for e in sql("""select feeder, sum(length_km * estimated_loss_percent) / nullif(sum(length_km), 0) pct from `tabLine Section`
		               where society = %(s)s and enabled = 1 group by feeder""", {"s": s}):
			est[e.feeder] = round(flt(e.pct), 2) if e.pct is not None else None
	data += [{"name": r["name"], "connections": r["connections"], "input_kwh": r["input_kwh"] or None, "billed_kwh": r["billed_kwh"],
	          "loss_kwh": r["loss_kwh"], "loss_pct": r["loss_pct"], "status": _(r["status"]), "technical_pct": est.get(r["item"])} for r in rows]
	message = _(LEVEL_NOTE)
	if not whole:
		message = _("No DISCO bill or feeder input units are recorded for this month yet (Utility Billing → DISCO Bill). ") + message
	return {"columns": [col(by, "name", width=240), col("Connections", "connections", "Int", 100), col("Input kWh", "input_kwh", "Float", 120),
	                    col("Billed kWh", "billed_kwh", "Float", 120), col("Loss kWh", "loss_kwh", "Float", 110),
	                    col("Loss %", "loss_pct", "Percent", 90), col("Status", "status", width=120)] +
	                   ([col("Estimated Technical Loss %", "technical_pct", "Percent", 190)] if by == "Feeder" else []), "data": data, "message": message}


@report("loss_ranking", "Highest-Loss Feeders and Loss Trend", LOSS_ROLES,
        MONTH_FILTERS + [dict(fieldname="group_by", label="Group By", fieldtype="Select", options="Feeder\nTransformer", default="Feeder"),
                         dict(fieldname="show", label="Show", fieldtype="Select", options="Ranking\n12-Month Trend", default="Ranking")],
        "Feeders or transformers ranked by loss, or the society's loss month by month for the last 12 months.", ref_doctype="DISCO Bill")
def loss_ranking(f):
	L = _losses()
	s, month = society_of(f), month_start(f.get("month") or nowdate())
	if f.get("show") == "12-Month Trend":
		data = []
		for m in last_months(month, 12):
			loss = L.society_loss(s, m)
			data.append({"month": getdate(m).strftime("%b %Y"), "input_kwh": loss["input_kwh"] if loss else None,
			             "billed_kwh": loss["billed_kwh"] if loss else billed_units_of(s, m), "loss_kwh": loss["loss_kwh"] if loss else None,
			             "loss_pct": loss["loss_pct"] if loss else None, "status": _(loss["status"]) if loss else _(L.NO_INPUT)})
		chart = {"data": {"labels": [d["month"] for d in data], "datasets": [{"name": _("Loss %"), "values": [d["loss_pct"] or 0 for d in data]}]},
		         "type": "line", "fieldtype": "Percent"} if any(d["loss_pct"] is not None for d in data) else None
		return {"columns": [col("Month", "month", width=110), col("Input kWh", "input_kwh", "Float", 120), col("Billed kWh", "billed_kwh", "Float", 120),
		                    col("Loss kWh", "loss_kwh", "Float", 110), col("Loss %", "loss_pct", "Percent", 90), col("Status", "status", width=120)],
		        "data": data, "chart": chart, "message": _(LEVEL_NOTE)}
	by = f.get("group_by") or "Feeder"
	ranked = [r for r in L.network_losses(s, month, by) if r["loss_pct"] is not None]
	data = [{"rank": i, "name": r["name"], "input_kwh": r["input_kwh"], "billed_kwh": r["billed_kwh"], "loss_kwh": r["loss_kwh"],
	         "loss_pct": r["loss_pct"], "status": _(r["status"])} for i, r in enumerate(ranked, start=1)]
	chart = {"data": {"labels": [d["name"] for d in data[:10]], "datasets": [{"name": _("Loss %"), "values": [d["loss_pct"] for d in data[:10]]}]},
	         "type": "bar", "fieldtype": "Percent"} if data else None
	return {"columns": [col("#", "rank", "Int", 50), col(by, "name", width=240), col("Input kWh", "input_kwh", "Float", 120),
	                    col("Billed kWh", "billed_kwh", "Float", 120), col("Loss kWh", "loss_kwh", "Float", 110), col("Loss %", "loss_pct", "Percent", 90),
	                    col("Status", "status", width=120)], "data": data, "chart": chart,
	        "message": _("Only those with input units recorded are ranked, highest loss first.")}


def billed_units_of(society, month):
	return _losses().billed_units(society, month)


@report("atc_losses", "AT&C Losses", LOSS_ROLES, MONTH_FILTERS,
        "Aggregate technical and commercial losses for the last 12 months: 1 − (billing efficiency × collection efficiency).", ref_doctype="DISCO Bill")
def atc_losses(f):
	L = _losses()
	s, month = society_of(f), month_start(f.get("month") or nowdate())
	months = last_months(month, 12)
	series = monthly_series(s, months)
	data = []
	for m, row in zip(months, series):
		r = L.at_c_for(s, m, row)
		data.append({"month": row["month"], "input_kwh": r["input_kwh"] if r else None, "billed_kwh": r["billed_kwh"] if r else row["units"],
		             "billing_efficiency": r["billing_efficiency"] if r else None, "payable": row["payable"], "collected": row["collected"],
		             "collection_efficiency": r["collection_efficiency"] if r else None, "atc": r["atc"] if r else None})
	chart = {"data": {"labels": [d["month"] for d in data], "datasets": [{"name": _("AT&C %"), "values": [d["atc"] or 0 for d in data]}]},
	         "type": "line", "fieldtype": "Percent"} if any(d["atc"] is not None for d in data) else None
	return {"columns": [col("Month", "month", width=110), col("Input kWh", "input_kwh", "Float", 120), col("Billed kWh", "billed_kwh", "Float", 120),
	                    col("Billing efficiency %", "billing_efficiency", "Percent", 130), col("Payable within due date", "payable", "Currency", 150),
	                    col("Collected", "collected", "Currency", 120), col("Collection efficiency %", "collection_efficiency", "Percent", 150),
	                    col("AT&C loss %", "atc", "Percent", 110)], "data": data, "chart": chart,
	        "message": _("Billing efficiency = units billed ÷ units that entered the society. Collection efficiency = collected in the month ÷ payable within the due date on that month's bills, capped at 100%.")}


@report("transformer_loading", "Transformer Loading", LOSS_ROLES, MONTH_FILTERS[:1],
        "Sanctioned load of the connections on each transformer against its kVA rating.", ref_doctype="Transformer")
def transformer_loading_report(f):
	L = _losses()
	s = society_of(f)
	rows = sql("""select t.name, t.transformer_code, t.feeder, t.kva_rating, t.location, sum(c.status = 'Active') connections,
			sum(case when c.status = 'Active' then c.sanctioned_load_kw else 0 end) kw
		from `tabTransformer` t left join `tabConnection` c on c.transformer = t.name
		where t.society = %(s)s and t.enabled = 1 group by t.name order by t.transformer_code""", {"s": s})
	data = []
	for r in rows:
		pct_ = L.transformer_loading(r.kw, r.kva_rating)
		data.append({"transformer": r.name, "feeder": r.feeder, "location": r.location, "kva": flt(r.kva_rating), "connections": cint(r.connections),
		             "kw": flt(r.kw), "loading": pct_, "status": L.loading_status(pct_) if pct_ is not None else _("No rating")})
	data.sort(key=lambda r: -(r["loading"] or 0))
	return {"columns": [col("Transformer", "transformer", "Link", 150, "Transformer"), col("Feeder", "feeder", "Link", 140, "Feeder"),
	                    col("Location", "location", width=150), col("Rating (kVA)", "kva", "Float", 100), col("Active Connections", "connections", "Int", 120),
	                    col("Sanctioned Load (kW)", "kw", "Float", 140), col("Loading %", "loading", "Percent", 100), col("Status", "status", width=110)],
	        "data": data,
	        "message": _("Loading = sanctioned load of the active connections ÷ (kVA rating × 0.9 power factor). It is an upper bound: metered peak load isn't recorded. Heavy from 80%, overloaded above 100%.")}


@report("disco_reconciliation", "DISCO Bill vs Resident Billing", FINANCE,
        [dict(fieldname="society", label="Society", fieldtype="Link", options="Society"),
         dict(fieldname="from_date", label="From Month", fieldtype="Date", default="@year_start"),
         dict(fieldname="to_date", label="To Month", fieldtype="Date", default="@today")],
        "The DISCO's bill (units, amount, FPA, QTA) against what the society billed its residents: difference, margin and FPA/QTA recovery.",
        ref_doctype="DISCO Bill")
def disco_reconciliation(f):
	L = _losses()
	s = society_of(f)
	start, end = month_start(f.get("from_date") or add_months(nowdate(), -11)), month_start(f.get("to_date") or nowdate())
	data, m = [], start
	while m <= end:
		r = L.disco_comparison(s, m)
		if r["disco_bill"] or r["billed_units"]:
			data.append({"month": getdate(m).strftime("%b %Y"), **r})
		m = add_months(m, 1)
	if data:
		t = total_row("month", _("Total"), [d for d in data if d["disco_bill"]], ("disco_units", "billed_units", "units_difference", "disco_total", "billed_amount",
		                                                                          "margin", "disco_fpa", "billed_fpa", "disco_qta", "billed_qta"))
		t["margin_pct"] = pct(t["margin"], t["billed_amount"])
		t["fpa_recovery"] = pct(t["billed_fpa"], t["disco_fpa"]) if t["disco_fpa"] else None
		t["qta_recovery"] = pct(t["billed_qta"], t["disco_qta"]) if t["disco_qta"] else None
		data.append(t)
	money = lambda label, k: col(label, k, "Currency", 120)  # noqa: E731
	return {"columns": [col("Month", "month", width=100), col("DISCO Bill", "disco_bill", "Link", 130, "DISCO Bill"), col("DISCO kWh", "disco_units", "Float", 110),
	                    col("Billed kWh", "billed_units", "Float", 110), col("Difference kWh", "units_difference", "Float", 120),
	                    money("DISCO Total", "disco_total"), money("Billed (electricity)", "billed_amount"), money("Margin", "margin"),
	                    col("Margin %", "margin_pct", "Percent", 90), money("DISCO FPA", "disco_fpa"), money("FPA Billed", "billed_fpa"),
	                    col("FPA Recovered %", "fpa_recovery", "Percent", 110), money("DISCO QTA", "disco_qta"), money("QTA Billed", "billed_qta"),
	                    col("QTA Recovered %", "qta_recovery", "Percent", 110)], "data": data,
	        "message": _("Billed (electricity) is every electricity-group bill line (energy, FPA, QTA, per-unit add-ons); fixed society charges aren't DISCO cost. Margin = billed − DISCO total. Revenue against budget needs budgets, which aren't recorded yet.")}


# ================================================================ 21. audit log (RPT-42)
AUDITED = ("Society", "Tariff", "Tariff Adjustment", "Bill Run", "Account Adjustment", "Refund", "Bill Revision", "Bulk Adjustment", "Installment Plan", "Write Off", "Due Date Extension", "Bill Dispute", "Settlement File", "Suspense Payment", "Notification Template", "Messaging Provider", "Fee Type", "Charge Type", "Consumer Category",
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
