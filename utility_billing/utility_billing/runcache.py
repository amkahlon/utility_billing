# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Facts a bill run looks up once instead of once per account (FS-RUN-07: 20,000 bills in under 10 minutes; increment 23).

While a run calculates or posts, `active()` holds which accounts of the society have holder or category changes, a service agreement or
a former occupant's balance, and the tariffs looked up so far. Accounts that have none (nearly all of them) skip those queries. Outside a
run nothing is cached and every lookup reads the database as before."""

from contextlib import contextmanager

import frappe
from frappe.utils import add_months, getdate


@contextmanager
def for_run(society, utility):
	before = frappe.flags.ub_run_cache
	frappe.flags.ub_run_cache = {
		"changed": set(frappe.db.sql("""select distinct c.parent from `tabConnection Change` c join `tabConnection` n on n.name = c.parent
		                                where n.society = %s and n.utility = %s""", (society, utility), pluck=True)),
		"agreements": set(frappe.db.sql("""select distinct connection from `tabService Agreement` where docstatus = 1 and ifnull(tariff, '') != ''""",
		                                pluck=True)),
		"former": set(frappe.db.sql("""select distinct connection from `tabFormer Occupant Balance` where society = %s""", society, pluck=True)),
		"plans": set(frappe.db.sql("""select distinct connection from `tabInstallment Plan` where docstatus = 1 and plan_status = 'Active'""",
		                           pluck=True)),
		"flagged": set(frappe.db.sql("""select distinct r.parent from `tabConnection Flag Row` r join `tabConnection` n on n.name = r.parent
		                                where r.parenttype = 'Connection' and n.society = %s""", society, pluck=True)),
		"tariffs": {},
		"bulk": {},
		"primed": None,
	}
	try:
		yield frappe.flags.ub_run_cache
	finally:
		frappe.flags.ub_run_cache = before


def active():
	return frappe.flags.ub_run_cache


def none_for(kind, connection) -> bool:
	"""True when the run knows the account has nothing of this kind ('changed', 'agreements', 'former')."""
	c = active()
	return bool(c) and connection not in c[kind]


# ---------------------------------------------------------------- a group of accounts read together (the performance step)
CONSUMER_FIELDS = ("name", "consumer_name", "mobile_no", "address", "id_number", "tax_no", "tax_status", "tax_exempt")
ENTRY_FIELDS = ("name", "seq", "balance", "current_balance", "arrears_balance", "surcharge_balance", "fees_balance", "credit_balance")


def prime(connections, month, months, lock=False):
	"""Read, in a handful of statements, what the bills of these accounts need: the accounts, their consumers, each account's last
	ledger entry and its bill history. `lock` (posting): the accounts are locked first, as account.lock does one by one.
	Accounts with a recorded holder or category change are left out; they are read one by one as before."""
	c = active()
	if not c:
		return None
	names = [n for n in connections if n not in c["changed"]]
	p = frappe._dict(connections={}, consumers={}, last={}, bills={}, older=set(), payments={}, legacy={}, months=months, month=getdate(month))
	c["primed"] = p
	if not names:
		return p
	if lock:
		frappe.db.sql("select name from `tabConnection` where name in %s for update", (names,))
	for r in frappe.db.sql("select * from `tabConnection` where name in %s", (names,), as_dict=True):
		p.connections[r.name] = r
	consumers = list({r.consumer for r in p.connections.values() if r.consumer})
	if consumers:
		for r in frappe.db.sql(f"select {', '.join(CONSUMER_FIELDS)} from `tabConsumer` where name in %s", (consumers,), as_dict=True):
			p.consumers[r.name] = r
	p.last = dict.fromkeys(names)
	for r in frappe.db.sql(f"""select e.connection, {', '.join('e.' + f for f in ENTRY_FIELDS)} from `tabCustomer Ledger Entry` e
	                           join (select connection, max(seq) seq from `tabCustomer Ledger Entry` where connection in %s group by connection) m
	                           on m.connection = e.connection and m.seq = e.seq""", (names,), as_dict=True):
		p.last[r.connection] = r
	# history: the posted bills of the last `months` months; an account with fewer in that window and older bills is read on its own
	window = add_months(p.month, -months)
	earliest = None
	for r in frappe.db.sql("""select connection, billing_month, issue_date, units, current_amount, payable_within_due from `tabUtility Bill`
	                          where connection in %s and docstatus = 1 and billing_month < %s and billing_month >= %s
	                          order by billing_month desc""", (names, p.month, window), as_dict=True):
		p.bills.setdefault(r.connection, []).append(r)
		if r.issue_date and (earliest is None or getdate(r.issue_date) < earliest):
			earliest = getdate(r.issue_date)
	p.older = set(frappe.db.sql("""select distinct connection from `tabUtility Bill` where connection in %s and docstatus = 1
	                               and billing_month < %s""", (names, window), pluck=True))
	if earliest:
		for r in frappe.db.sql("""select connection, payment_date, amount from `tabPayment Receipt` where connection in %s and docstatus = 1
		                          and payment_date >= %s order by payment_date""", (names, earliest), as_dict=True):
			p.payments.setdefault(r.connection, []).append(r)
	for r in frappe.db.sql("""select connection, billing_month, units, current_amount, arrears, bill_amount, paid_on, paid_amount
	                          from `tabLegacy Bill` where connection in %s order by billing_month desc""", (names,), as_dict=True):
		p.legacy.setdefault(r.connection, []).append(r)
	return p


def primed(connection=None):
	"""The group read by prime() when it covers this account, else None (the caller then reads the database)."""
	c = active()
	p = c.get("primed") if c else None
	if p is None or (connection is not None and connection not in p.connections):
		return None
	return p


def bulk(key, loader):
	"""A whole table's worth of rows a run needs ({key: row}), read once per run; None outside a run (callers then read one row)."""
	c = active()
	if not c:
		return None
	if key not in c["bulk"]:
		c["bulk"][key] = loader()
	return c["bulk"][key]


def tariff(key, fn):
	c = active()
	if not c:
		return fn()
	if key not in c["tariffs"]:
		c["tariffs"][key] = fn()
	return c["tariffs"][key]
