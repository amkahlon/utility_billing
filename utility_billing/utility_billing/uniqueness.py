# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Numbers and codes that identify something are refused at entry when they are already used (user, 2026-11-15: "duplicate
meter no should be restricted at the time of entering new meter's data, same for other such unique fields").

Values are compared the way people mistype them: ignoring case, spaces, dashes, dots and slashes, and for meter numbers also
leading zeros ("TST-RA-M1", "tst ra m1" and "TSTRAM1" are the same meter). Some numbers share one namespace across DocTypes: a
check meter on a feeder can't carry a consumer meter's number. The check runs on validate (doc_events) only when the value is new
or changed, so an old record that already clashes can still be edited for something else; `report()` lists such clashes."""

import re

import frappe
from frappe import _

# kind: how values are compared
#   code   : letters and digits only, upper case          ("N-2308" == "n 2308")
#   serial : as code, and leading zeros dropped            ("00123-A" == "123A")
#   name   : as code (names differ only in punctuation and case are the same)
# Each group is one namespace: [(doctype, field, scope fields)], a label for messages and a kind.
GROUPS = [
	{"label": "Meter number", "kind": "serial", "members": [("Meter", "serial_no", ()), ("Feeder", "check_meter_no", ()),
	                                                         ("Transformer", "check_meter_no", ()), ("Society", "bulk_meter_no", ())]},
	{"label": "Seal number", "kind": "code", "members": [("Meter", "seal_no", ())]},
	{"label": "Asset serial number", "kind": "serial", "members": [("Network Asset", "serial_no", ())]},
	{"label": "ID number", "kind": "code", "members": [("Consumer", "id_number", ("id_type",))]},
	{"label": "Company registration number", "kind": "code", "members": [("Consumer", "registration_no", ())]},
	{"label": "Tax number (NTN)", "kind": "code", "members": [("Consumer", "tax_no", ())]},
	{"label": "Property number", "kind": "code", "members": [("Property", "property_no", ("society",))]},
	{"label": "Feeder code", "kind": "code", "members": [("Feeder", "feeder_code", ("society",))]},
	{"label": "Grid station code", "kind": "code", "members": [("Grid Station", "station_code", ("society",))]},
	{"label": "Line section code", "kind": "code", "members": [("Line Section", "section_code", ("society",))]},
	{"label": "Transformer code", "kind": "code", "members": [("Transformer", "transformer_code", ("society",))]},
	{"label": "Route code", "kind": "code", "members": [("Reading Route", "route_code", ("society", "utility"))]},
	{"label": "Service type code", "kind": "code", "members": [("Service Request Type", "type_code", ("society",))]},
	{"label": "Fee name", "kind": "name", "members": [("Fee Type", "fee_name", ("society",))]},
	{"label": "Facility name", "kind": "name", "members": [("Common Area Facility", "facility_name", ("society",))]},
	{"label": "Category name", "kind": "name", "members": [("Consumer Category", "category_name", ("utility",))]},
	{"label": "Tax code", "kind": "code", "members": [("Tax", "tax_code", ("society",))]},
	{"label": "DISCO bill number", "kind": "code", "members": [("DISCO Bill", "bill_no", ("society",))]},
	{"label": "Counter code", "kind": "code", "members": [("Cash Counter", "counter_code", ())]},
	{"label": "Account code", "kind": "code", "members": [("Ledger Account", "account_code", ())]},
	{"label": "Charge name", "kind": "name", "members": [("Charge Type", "charge_name", ())]},
	{"label": "Charge code", "kind": "code", "members": [("Charge Type", "charge_code", ())]},
	{"label": "Reason code", "kind": "code", "members": [("Reason Code", "code", ())]},
	{"label": "Exception code", "kind": "code", "members": [("Reading Exception Code", "code", ())]},
	{"label": "Flag code", "kind": "code", "members": [("Connection Flag Type", "flag_code", ())]},
	{"label": "KPI code", "kind": "code", "members": [("KPI Definition", "kpi_code", ())]},
	{"label": "Layout name", "kind": "name", "members": [("Journal Export Layout", "layout_name", ())]},
	{"label": "API client name", "kind": "name", "members": [("API Client", "client_name", ())]},
	{"label": "Society name", "kind": "name", "members": [("Society", "society_name", ())]},
	{"label": "Society short name", "kind": "code", "members": [("Society", "short_name", ())]},
	{"label": "Society registration number", "kind": "code", "members": [("Society", "registration_no", ())]},
	{"label": "DISCO account number", "kind": "code", "members": [("Society", "disco_account_no", ())]},
	{"label": "Licence number", "kind": "code", "members": [("Society", "licence_no", ())]},
]

DOCTYPES = sorted({dt for g in GROUPS for dt, _f, _s in g["members"]})


def norm(value, kind="code") -> str:
	"""The comparison form of a value: upper case letters and digits only; for serials also without leading zeros."""
	v = re.sub(r"[^0-9A-Z]", "", str(value or "").upper())
	if kind == "serial":
		v = v.lstrip("0") or ("0" if v else "")
	return v


def _sql_norm(column, kind):
	expr = f"regexp_replace(upper(`{column}`), '[^0-9A-Z]', '')"
	return f"trim(leading '0' from {expr})" if kind == "serial" else expr


def clash(doc, group, field_dt, field, scope):
	"""The first other record in the group's namespace with the same value, as (doctype, name), or None."""
	value = norm(doc.get(field), group["kind"])
	if not value:
		return None
	for dt, f, member_scope in group["members"]:
		if not frappe.db.table_exists(dt) or not frappe.get_meta(dt).has_field(f):
			continue
		cond, values = [f"{_sql_norm(f, group['kind'])} = %(v)s"], {"v": value}
		for i, s in enumerate(member_scope):
			if s in scope or dt == field_dt:
				cond.append(f"`{s}` = %(s{i})s")
				values[f"s{i}"] = doc.get(s)
		if dt == field_dt and not doc.is_new():
			# a new record already carries its name (from the number itself) and names compare without case: "tst-m1" != "TST-M1"
			# would hide the clash, so only a saved record leaves itself out
			cond.append("name != %(me)s")
			values["me"] = doc.name
		if frappe.get_meta(dt).is_submittable:
			cond.append("docstatus < 2")
		row = frappe.db.sql(f"select name from `tab{dt}` where {' and '.join(cond)} limit 1", values)
		if row:
			return dt, row[0][0]
	return None


def clean(value):
	"""What is stored: the value as typed, without stray spaces."""
	return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value


def validate(doc, method=None):
	"""doc_events validate hook for every DocType in GROUPS."""
	before = None if doc.is_new() else doc.get_doc_before_save()
	for g in GROUPS:
		for dt, field, scope in g["members"]:
			if dt != doc.doctype or not doc.meta.has_field(field):
				continue
			if isinstance(doc.get(field), str):
				doc.set(field, clean(doc.get(field)) or None)
			if not doc.get(field):
				continue
			changed = before is None or norm(before.get(field), g["kind"]) != norm(doc.get(field), g["kind"]) or any(
				before.get(s) != doc.get(s) for s in scope)
			if not changed:
				continue
			found = clash(doc, g, dt, field, scope)
			if found:
				other_dt, other = found
				where = _(" in {0}").format(doc.get(scope[0])) if scope else ""
				frappe.throw(
					_("{0} {1} is already used{2} by {3} {4}. Numbers and codes are compared ignoring case, spaces and dashes{5}.").format(
						_(g["label"]), frappe.bold(doc.get(field)), where, _(other_dt), frappe.bold(other),
						_(" and leading zeros") if g["kind"] == "serial" else ""),
					frappe.DuplicateEntryError, title=_("Already used"))


def report() -> list[dict]:
	"""Clashes already in the data (entered before this check): [{label, value, records}]."""
	out = []
	for g in GROUPS:
		seen = {}
		for dt, field, scope in g["members"]:
			if not frappe.db.table_exists(dt) or not frappe.get_meta(dt).has_field(field):
				continue
			extra = "".join(f", `{s}`" for s in scope)
			cond = " and docstatus < 2" if frappe.get_meta(dt).is_submittable else ""
			for row in frappe.db.sql(f"select name, `{field}`{extra} from `tab{dt}` where ifnull(`{field}`, '') != ''{cond}"):
				key = (norm(row[1], g["kind"]), *row[2:])
				seen.setdefault(key, []).append(f"{dt} {row[0]}")
		for key, records in seen.items():
			if key[0] and len(records) > 1:
				out.append({"label": g["label"], "value": key[0], "records": records})
	return out
