# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Walk order (increment 60): the order a reader walks to read meters and a distributor walks to hand out bills.

User, 2026-11-26: "there should be some mechanism to assign/change ref nos in walk order so that printing and distribution becomes
easy". Reference numbers stay as they are: they are the account's permanent number, printed on past bills and known to banks and
consumers. The walk is kept apart from them, as the accounts' places on the Reading Routes: a route is an ordered list, and an
account's place on it (1, 2, 3 ...) is its walk position. Reading sheets, bill printing and bill delivery follow it.

- build_from_references: a first walk in one go, from the reference numbers a migrated society brought from its old system (their
  leading digits group the accounts into the old batches, and the numbers run in the old order): one route per group.
- place / move: put accounts on a route at the start, at the end or after an account, taking them off the routes they were on.
- import_walk_order: a sheet of route and reference number (and an optional order) from the field staff.
- of: an account's route and place, printed on its bill ("Route 07 · 123") and on the route's cover sheet.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint, cstr

from utility_billing.utility_billing.bulk import in_chunks

ROUTE = "Reading Route"
START, END, AFTER = "start", "end", "after"


# ---------------------------------------------------------------- reading
def of(connection):
	"""An account's walk: {route, position, description, distributor, label}, or None when it is on no route in use."""
	if not connection:
		return None
	row = frappe.db.sql(
		"""select r.route_code route, s.sequence position, r.description, r.distributor
		from `tabRoute Stop` s join `tabReading Route` r on r.name = s.parent
		where s.parenttype = 'Reading Route' and s.connection = %s and r.enabled = 1
		order by r.modified desc limit 1""", connection, as_dict=True)
	if not row:
		return None
	w = row[0]
	w["label"] = label(w.route, w.position)
	return w


def label(route, position) -> str:
	return _("Route {0} · {1}").format(route, cint(position)) if route else ""


def _enabled_routes(society, utility) -> dict:
	"""{route_code: route name} of the routes in use."""
	return {r.route_code: r.name for r in frappe.get_all(ROUTE, filters={"society": society, "utility": utility, "enabled": 1},
	                                                     fields=["name", "route_code"])}


def _routes_holding(utility, connections, exclude=()) -> list:
	"""Names of this utility's routes (in use or not) that hold any of these accounts."""
	names = set()
	for chunk in in_chunks(sorted(set(connections)), 1000):
		names.update(frappe.db.sql_list(
			"""select distinct s.parent from `tabRoute Stop` s join `tabReading Route` r on r.name = s.parent
			where s.parenttype = 'Reading Route' and s.connection in %(c)s and r.utility = %(u)s""", {"c": chunk, "u": utility}))
	return sorted(names - set(exclude))


def accounts_of(society, utility, text) -> list:
	"""The accounts named in `text` (reference numbers, one per line or separated by commas or spaces), in the order given.
	Raises, naming them, when any isn't an account of this society and utility."""
	refs = list(dict.fromkeys(x for x in re.split(r"[\s,;]+", cstr(text)) if x))
	if not refs:
		frappe.throw(_("Give the reference numbers of the accounts."))
	found = set()
	for chunk in in_chunks(refs, 1000):
		found.update(frappe.get_all("Connection", filters={"name": ["in", chunk], "society": society, "utility": utility}, pluck="name"))
	missing = [r for r in refs if r not in found]
	if missing:
		frappe.throw(_("Not {0} accounts of {1}: {2}").format(utility, society, ", ".join(missing[:20]) + (" …" if len(missing) > 20 else "")))
	return refs


# ---------------------------------------------------------------- placing accounts
def _save(doc, check):
	if check:
		doc.check_permission("write")
	doc.flags.ignore_permissions = not check
	doc.save()


def _renumber(doc):
	for n, s in enumerate(doc.stops, start=1):
		s.idx = n


def place(society, utility, connections, route_code, where=END, after=None, check=True) -> dict:
	"""Take these accounts off every route of the utility and put them, in the order given, on the route in use with code
	`route_code`: at the start, at the end, or after account `after`. No route_code: they end up on no route."""
	conns = list(dict.fromkeys(connections))
	moving = set(conns)
	target = None
	if route_code:
		name = _enabled_routes(society, utility).get(route_code)
		if name:
			target = frappe.get_doc(ROUTE, name)
		elif check:
			frappe.throw(_("There is no route {0} in use for {1} {2}.").format(route_code, society, utility))
	if where == AFTER:
		if not after or after in moving:
			frappe.throw(_("Say which account they go after (one that isn't being moved)."))
		if not target or not any(s.connection == after for s in target.stops):
			frappe.throw(_("Account {0} isn't on route {1}.").format(after, route_code))
	for name in _routes_holding(utility, conns, exclude=[target.name] if target else []):
		r = frappe.get_doc(ROUTE, name)
		r.set("stops", [s for s in r.stops if s.connection not in moving])
		_renumber(r)
		_save(r, check)
	if not target:
		return {"route": None, "moved": len(conns)}
	kept = [s for s in target.stops if s.connection not in moving]
	at = 0 if where == START else len(kept) if where != AFTER else next(i for i, s in enumerate(kept) if s.connection == after) + 1
	new = [frappe._dict(stop_type="Connection", connection=c) for c in conns]
	target.set("stops", [])
	for s in kept[:at] + new + kept[at:]:
		target.append("stops", s if isinstance(s, dict) else s.as_dict(no_default_fields=True))
	_renumber(target)
	_save(target, check)
	return {"route": target.name, "moved": len(conns)}


@frappe.whitelist(methods=["POST"])
def move(route: str, accounts: str, where: str = END, after: str | None = None) -> dict:
	"""Move Accounts on a route's form: these accounts (reference numbers) onto this route at the start, at the end or after an
	account, in the order given; they come off the routes they were on."""
	doc = frappe.get_doc(ROUTE, route)
	doc.check_permission("write")
	if not doc.enabled:
		frappe.throw(_("Route {0} isn't in use. Tick In Use first.").format(doc.route_code))
	if where not in (START, END, AFTER):
		frappe.throw(_("Choose where the accounts go: at the start, at the end, or after an account."))
	conns = accounts_of(doc.society, doc.utility, accounts)
	after = cstr(after).strip() or None
	out = place(doc.society, doc.utility, conns, doc.route_code, where, after)
	out["position"] = {s.connection: s.sequence for s in frappe.get_doc(ROUTE, route).stops if s.connection in set(conns)}
	return out


# ---------------------------------------------------------------- a first walk from the reference numbers
def _check_may_build(society, utility):
	if not frappe.has_permission(ROUTE, "create"):
		raise frappe.PermissionError(_("You can't make reading routes."))
	if not society or not utility:
		frappe.throw(_("Choose the society and the utility."))
	frappe.get_doc("Society", society).check_permission("read")


def _unrouted(society, utility) -> list:
	"""This society's accounts of the utility that are on no route in use, in reference-number order."""
	return frappe.db.sql_list(
		"""select c.name from `tabConnection` c where c.society = %(s)s and c.utility = %(u)s and not exists (
			select 1 from `tabRoute Stop` s join `tabReading Route` r on r.name = s.parent
			where s.parenttype = 'Reading Route' and s.connection = c.name and r.utility = %(u)s and r.enabled = 1)
		order by length(c.name), c.name""", {"s": society, "u": utility})


def _groups(names, digits) -> dict:
	groups = {}
	for n in names:
		groups.setdefault(n[:digits], []).append(n)
	return groups


@frappe.whitelist()
def preview_build(society: str, utility: str, digits: int | str = 2) -> dict:
	"""What Build from Reference Numbers would make: the routes (one per group of the leading digits) and their account counts."""
	_check_may_build(society, utility)
	digits = cint(digits)
	if not 1 <= digits <= 12:
		frappe.throw(_("Group by 1 to 12 leading digits."))
	names = _unrouted(society, utility)
	existing = {r.route_code: r for r in frappe.get_all(ROUTE, filters={"society": society, "utility": utility},
	                                                    fields=["route_code", "enabled", "stops_count"])}
	groups = [{"code": code, "accounts": len(these), "existing": bool(existing.get(code)),
	           "in_use": bool(existing.get(code) and existing[code].enabled)} for code, these in sorted(_groups(names, digits).items())]
	return {"accounts": frappe.db.count("Connection", {"society": society, "utility": utility}), "unrouted": len(names),
	        "digits": digits, "groups": groups}


def build_from_references(society: str, utility: str, digits: int | str = 2) -> dict:
	"""Build from Reference Numbers (a background job): the accounts on no route, grouped by the leading digits of their reference
	numbers, each group a route with that code and the accounts in reference-number order. A route with that code already in use
	gets the group at its end. Returns {"created", "extended", "accounts"}."""
	from utility_billing.utility_billing import progress  # noqa: PLC0415

	plan = preview_build(society, utility, digits)
	stopped = [g["code"] for g in plan["groups"] if g["existing"] and not g["in_use"]]
	if stopped:
		frappe.throw(_("Routes {0} exist but aren't in use. Tick In Use on them, or group by a different number of digits.").format(
			", ".join(stopped)))
	if not plan["unrouted"]:
		frappe.throw(_("Every account is on a route already."))
	groups = _groups(_unrouted(society, utility), cint(digits))
	routes = _enabled_routes(society, utility)
	created = extended = done = 0
	for code, these in sorted(groups.items()):
		progress.step(_("Making route {0}").format(code), done, plan["unrouted"])
		if code in routes:
			doc = frappe.get_doc(ROUTE, routes[code])
			extended += 1
		else:
			doc = frappe.get_doc({"doctype": ROUTE, "society": society, "utility": utility, "route_code": code, "enabled": 1,
			                      "description": _("Made from reference numbers starting {0}").format(code)})
			created += 1
		for c in these:
			doc.append("stops", {"stop_type": "Connection", "connection": c})
		doc.save() if not doc.is_new() else doc.insert()
		done += len(these)
	progress.step(_("Making routes"), done, done)
	_fresh_statistics()
	return {"created": created, "extended": extended, "accounts": done}


def _fresh_statistics():
	"""After thousands of stops are added at once the database's figures for the table are stale for a while, and printing a run in
	walk order was planned badly: 51 seconds instead of half a second for 21,446 bills (rehearsal, 2026-11-26). The work is done by
	now, so it is kept first (ANALYZE ends the transaction); a test keeps its own transaction instead."""
	if frappe.flags.in_test:
		return
	frappe.db.commit()
	frappe.db.sql("analyze table `tabRoute Stop`")


# ---------------------------------------------------------------- a walk sheet to correct and import again
def sheet_rows(society, utility) -> list:
	"""Every account of the society and utility with its route and place, in walk order (accounts on no route at the end)."""
	rows = frappe.db.sql(
		"""select ifnull(r.route_code, '') route, s.sequence, c.name reference_no, c.consumer_name, ifnull(p.block, '') block,
			ifnull(p.property_no, '') property_no, ifnull(c.legacy_account_no, '') legacy_account_no
		from `tabConnection` c
		left join `tabProperty` p on p.name = c.property
		left join `tabRoute Stop` s on s.connection = c.name and s.parenttype = 'Reading Route'
			and s.parent in (select name from `tabReading Route` where society = %(s)s and utility = %(u)s and enabled = 1)
		left join `tabReading Route` r on r.name = s.parent
		where c.society = %(s)s and c.utility = %(u)s
		order by r.route_code is null, length(r.route_code), r.route_code, s.sequence, length(c.name), c.name""",
		{"s": society, "u": utility}, as_dict=True)
	return [["route", "order", "reference_no", "consumer_name", "block", "property_no", "old_account_no"]] + [
		[r.route, cint(r.sequence) or "", r.reference_no, r.consumer_name or "", r.block, r.property_no, r.legacy_account_no] for r in rows]


@frappe.whitelist()
def walk_sheet(society: str, utility: str):
	"""Download Walk Sheet: a CSV of sheet_rows, the same columns Import Walk Order reads."""
	if not frappe.has_permission(ROUTE, "read"):
		raise frappe.PermissionError(_("You can't see reading routes."))
	frappe.get_doc("Society", society).check_permission("read")
	from frappe.utils.csvutils import build_csv_response  # noqa: PLC0415

	build_csv_response(sheet_rows(society, utility), f"walk-order-{frappe.scrub(society)}-{frappe.scrub(utility)}")


# ---------------------------------------------------------------- a walk from a sheet
COLUMNS = {
	"route": ("route", "route code", "route no", "route_code"),
	"reference_no": ("reference_no", "reference no", "reference", "reference number", "account", "account no"),
	"order": ("order", "position", "sequence", "walk order", "walk_order"),
}


def _columns(header) -> dict:
	seen = {cstr(h).strip().lower(): i for i, h in enumerate(header)}
	out = {key: next((seen[a] for a in names if a in seen), None) for key, names in COLUMNS.items()}
	if out["route"] is None or out["reference_no"] is None:
		frappe.throw(_("The sheet needs a route column and a reference_no column (order is optional). It has: {0}.").format(
			", ".join(cstr(h) for h in header)))
	return out


def read_sheet(society, utility, rows) -> dict:
	"""The sheet's walk: {route_code: [account, ...]} in its order. Raises listing the bad rows; then nothing is changed."""
	if not rows:
		frappe.throw(_("The sheet is empty."))
	col, errors, seen, walk = _columns(rows[0]), [], {}, []
	for n, row in enumerate(rows[1:], start=2):
		cell = lambda key, row=row: cstr(row[col[key]]).strip() if col[key] is not None and col[key] < len(row) and row[col[key]] is not None else ""
		route, ref = cell("route"), cell("reference_no").split(".")[0]  # Excel may hand back 1000000016.0
		if not route and not ref:
			continue
		if not route or not ref:
			errors.append(_("Row {0}: give both the route and the reference number.").format(n))
			continue
		if ref in seen:
			errors.append(_("Row {0}: {1} is also on row {2}.").format(n, ref, seen[ref]))
			continue
		order = cell("order")
		if order and not re.fullmatch(r"\d+(\.0+)?", order):
			errors.append(_("Row {0}: the order {1} isn't a whole number.").format(n, order))
			continue
		seen[ref] = n
		walk.append((route, cint(float(order)) if order else n, n, ref))
	known = set()
	for chunk in in_chunks(list(seen), 1000):
		known.update(frappe.get_all("Connection", filters={"name": ["in", chunk], "society": society, "utility": utility}, pluck="name"))
	errors += [_("Row {0}: {1} isn't a {2} account of {3}.").format(seen[r], r, utility, society) for r in seen if r not in known]
	if errors:
		frappe.throw("<br>".join(errors[:20]) + (_("<br>… and {0} more.").format(len(errors) - 20) if len(errors) > 20 else ""),
		             title=_("Nothing was changed"))
	out = {}
	for route, _order, _row, ref in sorted(walk, key=lambda w: (w[0], w[1], w[2])):
		out.setdefault(route, []).append(ref)
	return out


def import_walk_order(society: str, utility: str, file_url: str) -> dict:
	"""Import Walk Order (a background job): each route of the sheet gets its accounts in the sheet's order, followed by the accounts
	it already had that the sheet doesn't list. A route not there yet is made; listed accounts come off the routes they were on.
	All of it or none of it. Returns {"routes", "created", "accounts"}."""
	from utility_billing.utility_billing import opening, progress  # noqa: PLC0415

	_check_may_build(society, utility)
	walk = read_sheet(society, utility, opening.read_file(file_url))
	dest = {ref: route for route, refs in walk.items() for ref in refs}
	existing = {r.route_code: r for r in frappe.get_all(ROUTE, filters={"society": society, "utility": utility}, fields=["name", "route_code", "enabled"])}
	stopped = [code for code in walk if code in existing and not existing[code].enabled]
	if stopped:
		frappe.throw(_("Routes {0} aren't in use. Tick In Use on them first.").format(", ".join(stopped)))
	# first every listed account comes off the routes it isn't going to, so no route refuses one that is still elsewhere
	for name in _routes_holding(utility, dest):
		r = frappe.get_doc(ROUTE, name)
		keep = [s for s in r.stops if not s.connection or dest.get(s.connection, r.route_code) == r.route_code]
		if len(keep) != len(r.stops):
			r.set("stops", keep)
			_renumber(r)
			_save(r, True)
	created, done, total = 0, 0, len(dest)
	for code, refs in walk.items():
		progress.step(_("Putting route {0} in order").format(code), done, total)
		if code in existing:
			doc = frappe.get_doc(ROUTE, existing[code].name)
		else:
			doc = frappe.get_doc({"doctype": ROUTE, "society": society, "utility": utility, "route_code": code, "enabled": 1})
			created += 1
		listed = set(refs)
		rest = [s.as_dict(no_default_fields=True) for s in doc.stops if s.connection not in listed]
		doc.set("stops", [])
		for s in [{"stop_type": "Connection", "connection": r} for r in refs] + rest:
			doc.append("stops", s)
		_renumber(doc)
		if doc.is_new():
			doc.insert()
		else:
			_save(doc, True)
		done += len(refs)
	progress.step(_("Putting routes in order"), done, total)
	_fresh_statistics()
	return {"routes": len(walk), "created": created, "accounts": done}
