# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""What a reading cycle looks up for every reading, read for a group of accounts at a time (the performance step, part 2: a sheet
of 20,000 readings and its file upload).

While a cycle's sheet is created or a readings file is applied, `for_cycle()` holds the cycle and society, and `prime()` reads a
group's accounts, meters, last actual readings, estimated units and reading history in a handful of statements. The lookups in
readings.py and Meter Reading ask here first; outside a cycle's bulk work, and for an account that isn't in the group, they read
the database as before."""

from contextlib import contextmanager

import frappe
from frappe.utils import add_months, flt, getdate

HISTORY_FIELDS = ("name", "billing_month", "units", "raw_units", "present_reading", "reading_date", "meter", "consumption_type",
                  "estimated_since_units", "status", "present_export_reading")
HISTORY_MONTHS = 30  # the window read for a group; an account with older readings and too few in the window reads on its own


def active():
	return frappe.flags.ub_reading_cache


@contextmanager
def for_cycle(cycle):
	"""`cycle`: the Reading Cycle document."""
	before = frappe.flags.ub_reading_cache
	month = getdate(cycle.billing_month).replace(day=1)
	frappe.flags.ub_reading_cache = frappe._dict(
		cycle_name=cycle.name, month=month,
		cycle=frappe._dict(society=cycle.society, utility=cycle.utility, billing_month=cycle.billing_month, status=cycle.status),
		society=frappe.db.get_value("Society", cycle.society, ["reading_tolerance_percent", "estimation_method"], as_dict=True),
		group=None)
	try:
		yield frappe.flags.ub_reading_cache
	finally:
		frappe.flags.ub_reading_cache = before


def prime(connections, history=False):
	"""Read the group's accounts and what their readings need. `history`: also the past actual readings (the checks of a reading
	that is being entered use them; a new, empty reading doesn't)."""
	c = active()
	names = list(connections)
	g = frappe._dict(names=set(names), connections={}, meters={}, meter={}, last={}, estimated={}, history={}, older=set(), has_history=history)
	c.group = g
	if not names:
		return g
	for r in frappe.db.sql("""select name, society, utility, consumer_name, property, sanctioned_load_kw, last_mdi_date
	                          from `tabConnection` where name in %s""", (names,), as_dict=True):
		g.connections[r.name] = r
	for r in frappe.db.sql("""select parent, meter, installed_on, removed_on, start_reading, start_export_reading, end_reading,
	                          end_export_reading, unreadable from `tabConnection Meter`
	                          where parenttype = 'Connection' and parent in %s order by parent, idx""", (names,), as_dict=True):
		g.meters.setdefault(r.parent, []).append(r)
	meter_names = list({m.meter for rows in g.meters.values() for m in rows if m.meter})
	if meter_names:
		for r in frappe.db.sql("select name, multiplier, digits, meter_type, smart_meter from `tabMeter` where name in %s", (meter_names,), as_dict=True):
			g.meter[r.name] = r
	fields = ", ".join(f"r.{f}" for f in HISTORY_FIELDS)
	# the last actual reading of each account before this month
	for r in frappe.db.sql(f"""select r.connection, {fields} from `tabMeter Reading` r
	                           join (select connection, max(billing_month) m from `tabMeter Reading`
	                                 where connection in %s and billing_month < %s and status in ('Valid', 'Approved')
	                                 and consumption_type = 'Actual' group by connection) x
	                           on x.connection = r.connection and x.m = r.billing_month
	                           where r.status in ('Valid', 'Approved') and r.consumption_type = 'Actual'""", (names, c.month), as_dict=True):
		g.last[r.connection] = r
	# months billed as estimates before this month (trued up by the next actual reading)
	for r in frappe.db.sql("""select connection, billing_month, units from `tabMeter Reading`
	                          where connection in %s and billing_month < %s and status = 'Estimated'""", (names, c.month), as_dict=True):
		g.estimated.setdefault(r.connection, []).append(r)
	if history:
		window = add_months(c.month, -HISTORY_MONTHS)
		for r in frappe.db.sql(f"""select r.connection, {fields} from `tabMeter Reading` r
		                           where r.connection in %s and r.billing_month < %s and r.billing_month >= %s
		                           and r.status in ('Valid', 'Approved') and r.consumption_type = 'Actual' and r.estimated_since_units = 0
		                           order by r.billing_month desc""", (names, c.month, window), as_dict=True):
			g.history.setdefault(r.connection, []).append(r)
		g.older = set(frappe.db.sql("""select distinct connection from `tabMeter Reading` where connection in %s and billing_month < %s
		                               and status in ('Valid', 'Approved')""", (names, window), pluck=True))
	return g


def _group(connection, month=None):
	c = active()
	g = c.group if c else None
	if g is None or connection not in g.names or (month is not None and getdate(month).replace(day=1) != c.month):
		return None
	return g


# ---------------------------------------------------------------- what the lookups ask
def cycle_info(name):
	c = active()
	if c and c.cycle_name == name:
		return c.cycle
	return frappe.db.get_value("Reading Cycle", name, ["society", "utility", "billing_month", "status"], as_dict=True)


def society_info(name):
	c = active()
	if c and c.cycle.society == name:
		return c.society
	return frappe.db.get_value("Society", name, ["reading_tolerance_percent", "estimation_method"], as_dict=True)


def connection_info(name):
	"""society, utility, consumer_name, property, sanctioned_load_kw, last_mdi_date of an account (None if it doesn't exist)."""
	g = _group(name)
	if g:
		return g.connections.get(name)
	return frappe.db.get_value("Connection", name, ["society", "utility", "consumer_name", "property", "sanctioned_load_kw", "last_mdi_date"],
	                           as_dict=True)


def meters_of(connection):
	"""The account's meter rows, in order, or None when the group doesn't hold it."""
	g = _group(connection)
	return g.meters.get(connection, []) if g else None


def meter_info(name):
	c = active()
	if c and c.group and name in c.group.meter:
		return c.group.meter[name]
	return frappe.db.get_value("Meter", name, ["multiplier", "digits", "meter_type", "smart_meter"], as_dict=True)


def last_actual(connection, before_month):
	"""(found, row): the group's answer for the cycle's month; found is False when the database must be asked."""
	g = _group(connection, before_month)
	return (True, g.last.get(connection)) if g else (False, None)


def estimated_since(connection, after_month, before_month):
	g = _group(connection, before_month)
	if not g:
		return False, 0
	after = getdate(after_month).replace(day=1) if after_month else None
	return True, sum(flt(r.units) for r in g.estimated.get(connection, []) if after is None or getdate(r.billing_month) > after)


def history(connection, before_month, months):
	"""(found, rows): the latest `months` actual readings before the cycle's month."""
	g = _group(connection, before_month)
	if not g or not g.has_history:
		return False, None
	rows = g.history.get(connection, [])
	if len(rows) < months and connection in g.older:
		return False, None
	return True, rows[:months]


def same_month_last_year(connection, month):
	g = _group(connection, month)
	if not g or not g.has_history:
		return False, None
	target = add_months(getdate(month).replace(day=1), -12)
	for r in g.history.get(connection, []):
		if getdate(r.billing_month) == getdate(target):
			return True, r
	return True, None
