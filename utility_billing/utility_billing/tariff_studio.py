# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tariff editor (page ub-tariff), designed after the mock-up "Tariff editor with impact preview" (2026-11-16).

The page edits the ordinary Tariff record, so every rule of the record still holds: the editor saves a draft through Tariff's own
validation, sends it through the Tariff Approval workflow (maker-checker), and never changes an approved tariff: "New version" copies
it to a draft with a later effective date, which closes the old one when approved (FS-CFG-05, 06, 08). The impact panel is the existing
preview (preview.tariff_impact) with the bills grouped by monthly units and one worked example."""

import json

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

from utility_billing.utility_billing import preview

EDITABLE = ("pricing_method", "flat_rate", "minimum_charge", "minimum_applies_to", "effective_from", "notes")
BANDS = ((0, 200), (201, 400), (401, 700), (701, None))


def _tariff(name):
	t = frappe.get_doc("Tariff", name)
	t.check_permission("read")
	return t


def versions(t) -> list[dict]:
	rows = frappe.get_all("Tariff", filters={"society": t.society, "utility": t.utility, "consumer_category": t.consumer_category, "docstatus": ["<", 2]},
	                      fields=["name", "effective_from", "effective_to", "workflow_state", "docstatus", "pricing_method", "flat_rate"],
	                      order_by="effective_from, creation")
	for i, r in enumerate(rows, start=1):
		r["version"] = i
	return rows


def replaced_by(t, rows):
	"""The approved version this one replaces: the latest approved one starting before it."""
	start = getdate(t.effective_from)
	older = [r for r in rows if r.docstatus == 1 and r.name != t.name and getdate(r.effective_from) < start]
	return older[-1] if older else None


@frappe.whitelist()
def load(name: str):
	t = _tariff(name)
	rows = versions(t)
	me = next((r for r in rows if r.name == t.name), None)
	prev = replaced_by(t, rows)
	prev_doc = frappe.get_doc("Tariff", prev.name) if prev else None
	categories_by_charge = {}
	for r in frappe.db.sql("""select c.charge_type, cc.category_name from `tabTariff Charge` c join `tabTariff` t on t.name = c.parent
	                          join `tabConsumer Category` cc on cc.name = t.consumer_category
	                          where t.society = %s and t.utility = %s and t.docstatus < 2 group by c.charge_type, cc.category_name""",
	                       (t.society, t.utility), as_dict=True):
		categories_by_charge.setdefault(r.charge_type, []).append(r.category_name)
	charge_types = {c.name: c for c in frappe.get_all("Charge Type", fields=["name", "charge_name", "calculation_basis", "bill_label"])}
	adjustments = frappe.get_all("Tariff Adjustment", filters={"society": t.society, "utility": t.utility, "docstatus": ["<", 2]},
	                             fields=["name", "adjustment_type", "billing_month", "reference_month", "rate_per_unit", "workflow_state", "docstatus"],
	                             order_by="billing_month desc", limit=6)
	from frappe.model.workflow import get_transitions  # noqa: PLC0415

	try:
		actions = sorted({x.get("action") for x in get_transitions(t)})
	except Exception:  # noqa: BLE001
		actions = []
	state = t.workflow_state or ("Approved" if t.docstatus == 1 else "Draft")
	return {
		"name": t.name, "title": t.tariff_title, "society": t.society, "utility": t.utility, "category": t.consumer_category,
		"category_name": frappe.db.get_value("Consumer Category", t.consumer_category, "category_name"),
		"version": me.version if me else 1, "state": state, "docstatus": t.docstatus, "effective_from": str(t.effective_from),
		"effective_to": str(t.effective_to) if t.effective_to else None, "pricing_method": t.pricing_method, "flat_rate": flt(t.flat_rate),
		"minimum_charge": flt(t.minimum_charge), "minimum_applies_to": t.get("minimum_applies_to") or "Energy charge", "notes": t.notes, "rejection_reason": t.get("rejection_reason"),
		"slabs": [{"from_units": s.from_units, "to_units": s.to_units, "rate": flt(s.rate)} for s in t.slabs],
		"charges": [{"charge_type": c.charge_type, "rate": flt(c.rate), "label": (charge_types.get(c.charge_type) or {}).get("bill_label") or c.charge_type,
		             "basis": (charge_types.get(c.charge_type) or {}).get("calculation_basis"),
		             "applies_to": sorted(categories_by_charge.get(c.charge_type, []))} for c in t.charges],
		"charge_types": [{"name": n, "label": c.bill_label or n, "basis": c.calculation_basis} for n, c in sorted(charge_types.items())],
		"previous": {"name": prev.name, "version": prev.version, "effective_from": str(prev.effective_from), "pricing_method": prev_doc.pricing_method,
		             "flat_rate": flt(prev_doc.flat_rate), "minimum_charge": flt(prev_doc.minimum_charge),
		             "slabs": [{"from_units": s.from_units, "to_units": s.to_units, "rate": flt(s.rate)} for s in prev_doc.slabs]} if prev else None,
		"versions": [{"name": r.name, "version": r.version, "from": str(r.effective_from), "to": str(r.effective_to) if r.effective_to else None,
		              "state": r.workflow_state or ("Approved" if r.docstatus == 1 else "Draft")} for r in rows],
		"adjustments": [{"name": a.name, "label": f"{a.adjustment_type} {getdate(a.reference_month).strftime('%b %Y') if a.reference_month else ''}".strip(),
		                 "rate": flt(a.rate_per_unit), "billing_month": getdate(a.billing_month).strftime("%b %Y") if a.billing_month else "",
		                 "basis": _("Units of {0}").format(getdate(a.reference_month).strftime("%b %Y")) if a.reference_month else "",
		                 "state": a.workflow_state or ("Approved" if a.docstatus == 1 else "Draft")} for a in adjustments],
		"can_edit": t.docstatus == 0 and state in ("Draft", "Rejected", "") and frappe.has_permission("Tariff", "write", t),
		"can_version": t.docstatus == 1 and frappe.has_permission("Tariff", "create"),
		"actions": actions,
	}


@frappe.whitelist(methods=["POST"])
def save(name: str, data: str):
	"""Save the editor's draft through the Tariff record (its validation, permissions and version log)."""
	t = frappe.get_doc("Tariff", name)
	t.check_permission("write")
	if t.docstatus != 0:
		frappe.throw(_("An approved tariff is locked; make a new version."))
	d = json.loads(data) if isinstance(data, str) else data
	for f in EDITABLE:
		if f in d:
			t.set(f, d[f])
	if "slabs" in d:
		t.set("slabs", [{"from_units": cint(s.get("from_units")), "to_units": cint(s.get("to_units")) or None, "rate": flt(s.get("rate"))}
		                for s in d["slabs"]])
	if "charges" in d:
		t.set("charges", [{"charge_type": c["charge_type"], "rate": flt(c.get("rate"))} for c in d["charges"] if c.get("charge_type")])
	t.save()
	return load(t.name)


@frappe.whitelist(methods=["POST"])
def new_version(name: str):
	"""A draft copy of an approved tariff from the first of next month (or the day after the latest version starts)."""
	frappe.has_permission("Tariff", "create", throw=True)
	old = frappe.get_doc("Tariff", name)
	# the first of next month, or the first of the month after the latest version starts (versions start on the 1st)
	latest = max(getdate(r.effective_from) for r in versions(old))
	start = max(getdate(add_months(getdate(nowdate()).replace(day=1), 1)), getdate(add_months(latest.replace(day=1), 1)))
	new = frappe.copy_doc(old)
	new.update({"effective_from": start, "effective_to": None, "workflow_state": "Draft", "rejection_reason": None, "amended_from": None,
	            "docstatus": 0})  # copy_doc keeps the approved copy's docstatus
	for f in ("requested_by", "requested_on", "approved_by", "approved_on", "submitted_snapshot", "changes_since_last_submission"):
		if new.meta.has_field(f):
			new.set(f, None)
	new.set("approval_log", [])
	new.insert()
	return new.name


@frappe.whitelist()
def impact(name: str):
	"""The preview with the bills grouped by monthly units, and one worked example (a typical account)."""
	r = preview.tariff_impact(name, with_rows=1)
	t = frappe.get_doc("Tariff", name)
	month, bills = preview.last_cycle(t.society, t.consumer_category)
	units = {b.connection: flt(frappe.db.get_value("Utility Bill", b.name, "units")) for b in bills}
	rows = r.get("_rows") or []
	bands = []
	for lo, hi in BANDS:
		inside = [x for x in rows if lo <= units.get(x["connection"], 0) and (hi is None or units.get(x["connection"], 0) <= hi)]
		if inside:
			bands.append({"band": f"{lo}–{hi}" if hi else f"{lo - 1}+", "accounts": len(inside),
			              "average": round(sum(x["new"] - x["old"] for x in inside) / len(inside), 2)})
	example = None
	if rows:
		typical = sorted(rows, key=lambda x: units.get(x["connection"], 0))[len(rows) // 2]
		example = {"connection": typical["connection"], "consumer_name": typical["consumer_name"], "units": units.get(typical["connection"], 0),
		           "old": typical["old"], "new": typical["new"], "difference": round(typical["new"] - typical["old"], 2)}
	r.pop("_rows", None)
	return {**r, "bands": bands, "example": example, "average": round(r["difference"] / r["bills"], 2) if r.get("bills") else 0}
