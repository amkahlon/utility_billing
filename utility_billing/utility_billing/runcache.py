# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Facts a bill run looks up once instead of once per account (FS-RUN-07: 20,000 bills in under 10 minutes; increment 23).

While a run calculates or posts, `active()` holds which accounts of the society have holder or category changes, a service agreement or
a former occupant's balance, and the tariffs looked up so far. Accounts that have none (nearly all of them) skip those queries. Outside a
run nothing is cached and every lookup reads the database as before."""

from contextlib import contextmanager

import frappe


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
		"tariffs": {},
		"bulk": {},
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
