# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Reading Entry (increment 47): a reading cycle's meters on one screen, a row an account, with only the reading typed.

User, 2026-11-24, showing a keypunch screen: "this screen is designed to enter consumer wise meter reading data, most of the
fields are prefilled, only reading related fields i.e present reading, units to be billed and meter status etc are editable",
and "meter reading fields should be editable only other than AMI/smart meters". Agreed the same day: units stay worked out from
the readings (a change is a reviewer's correction), the meter's status is given by an exception code, and a smart meter is typed
only when it isn't communicating.

Each row is saved as its Meter Reading document, so every check, flag and review step is the same as on the form and in a file."""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, nowdate

from utility_billing.utility_billing import access, rights

PAGE = 100
SHOW = {"all": [], "to_enter": [["reading_date", "is", "not set"], ["exception_code", "is", "not set"]],
        "exceptions": [["status", "in", ["Flagged", "Re-read Requested"]]], "not_read": [["exception_code", "is", "set"]]}
FIELDS = ["name", "connection", "consumer_name", "property", "meter", "multiplier", "digits", "smart_meter", "smart_meter_mute", "net_metering",
          "previous_reading", "previous_reading_date", "present_reading", "reading_date", "exception_code", "mdi_kw", "previous_export_reading",
          "present_export_reading", "units", "export_units", "status", "checks", "source", "route", "route_sequence", "consumption_type"]
TYPED = ("present_reading", "present_export_reading", "mdi_kw", "reading_date", "exception_code", "smart_meter_mute")


def _cycle(name):
	cycle = frappe.get_doc("Reading Cycle", name)
	cycle.check_permission("read")
	access.check_society(cycle.society)
	return cycle


def _totals(cycle, route=None) -> dict:
	cond, values = "reading_cycle = %(c)s", {"c": cycle, "r": route}
	if route:
		cond += " and route = %(r)s"
	t = frappe.db.sql(f"""select count(*) records,
	        sum(reading_date is not null or ifnull(exception_code, '') != '') entered,
	        sum(status in ('Flagged', 'Re-read Requested')) exceptions,
	        sum(ifnull(exception_code, '') != '') not_read,
	        sum(smart_meter = 1) smart,
	        sum(ifnull(units, 0)) import_units, sum(ifnull(export_units, 0)) export_units
	        from `tabMeter Reading` where {cond}""", values, as_dict=True)[0]
	return {k: flt(v) if "units" in k else cint(v) for k, v in t.items()}


@frappe.whitelist()
def get_sheet(society: str | None = None, cycle: str | None = None, route: str | None = None, show: str = "all", search: str | None = None,
              start: int = 0):
	"""The cycle's readings for the screen: the picker's lists, the totals and one page of rows."""
	if not frappe.has_permission("Meter Reading", "read"):
		raise frappe.PermissionError(_("You don't have access to meter readings."))
	societies = access.visible_societies()
	if society:
		access.check_society(society)
	cycles = frappe.get_list("Reading Cycle", filters={"society": society} if society else {}, fields=["name", "cycle_name", "society", "billing_month", "status"],
	                         order_by="billing_month desc, creation desc", limit=24)
	if not cycle:
		cycle = next((c.name for c in cycles if c.status == "Open"), cycles[0].name if cycles else None)
	out = {"societies": [{"name": s.name, "label": s.society_name or s.name} for s in societies], "society": society,
	       "cycles": [{"name": c.name, "label": f"{c.cycle_name or c.name} · {getdate(c.billing_month).strftime('%B %Y')}" + ("" if c.status == "Open" else f" ({_(c.status)})")}
	                  for c in cycles], "cycle": cycle, "rows": [], "page": PAGE}
	if not cycle:
		return out
	c = _cycle(cycle)
	if show not in SHOW:
		show = "all"
	filters = [["reading_cycle", "=", c.name], *SHOW[show]]
	if route:
		filters.append(["route", "=", route])
	text = cstr(search).strip()
	or_filters = [["connection", "like", f"%{text}%"], ["consumer_name", "like", f"%{text}%"], ["meter", "like", f"%{text}%"]] if text else None
	rows = frappe.get_list("Meter Reading", filters=filters, or_filters=or_filters, fields=FIELDS, order_by="route asc, route_sequence asc, connection asc",
	                       start=cint(start), page_length=PAGE + 1)
	more, rows = len(rows) > PAGE, rows[:PAGE]
	extra = {r.name: r for r in frappe.get_all("Connection", filters={"name": ["in", [r.connection for r in rows]]},
	                                            fields=["name", "sanctioned_load_kw", "consumer_category"])} if rows else {}
	open_cycle = c.status == "Open"
	can_write = open_cycle and frappe.has_permission("Meter Reading", "write")
	for r in rows:
		e = extra.get(r.connection) or frappe._dict()
		r.sanctioned_load_kw, r.category = flt(e.sanctioned_load_kw), (e.consumer_category or "").split("-", 1)[-1]
		r.previous_reading_date, r.reading_date = cstr(r.previous_reading_date or ""), cstr(r.reading_date or "")
		r.entered = bool(r.reading_date or r.exception_code)
	routes = [x for x in frappe.db.sql_list("select distinct route from `tabMeter Reading` where reading_cycle = %s and ifnull(route, '') != '' order by route", c.name)]
	codes = frappe.get_all("Reading Exception Code", filters={"enabled": 1}, fields=["name", "description"], order_by="name")
	out.update({
		"society": c.society, "society_name": frappe.db.get_value("Society", c.society, "society_name"), "month_label": getdate(c.billing_month).strftime("%B %Y"),
		"status": c.status, "utility": c.utility, "can_write": can_write, "rows": rows, "more": more, "start": cint(start), "show": show, "route": route or "",
		"search": text, "routes": routes, "codes": [{"name": x.name, "label": f"{x.name}: {x.description}" if x.description else x.name} for x in codes],
		"totals": _totals(c.name, route), "today": nowdate(),
		"can_review": open_cycle and rights.can("readings.review"),  # a reviewer may approve flagged readings of one kind together
	})
	return out


def _same(field, old, new) -> bool:
	if field in ("present_reading", "present_export_reading", "mdi_kw"):
		return flt(old) == flt(new)
	if field == "smart_meter_mute":
		return cint(old) == cint(new)
	return cstr(old or "") == cstr(new or "")


@frappe.whitelist(methods=["POST"])
def save_rows(cycle: str, rows):
	"""Save the rows typed on the screen: [{name, present_reading, present_export_reading, mdi_kw, reading_date, exception_code,
	smart_meter_mute}]. Each is saved as its document; a row that can't be saved comes back with the reason and the others still save."""
	c = _cycle(cycle)
	if c.status != "Open":
		frappe.throw(_("Reading cycle {0} is closed; its readings can't change.").format(c.name))
	rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
	if not isinstance(rows, list) or len(rows) > 500:
		frappe.throw(_("Send at most 500 rows at a time."))
	results = []
	for row in rows:
		row = frappe._dict(row)
		try:
			r = frappe.get_doc("Meter Reading", row.name)
			if r.reading_cycle != c.name:
				raise frappe.ValidationError(_("This reading isn't in the cycle"))
			r.check_permission("write")
			typed = {f: row.get(f) for f in TYPED if f in row}
			code = cstr(typed.get("exception_code") or "").strip()
			if "exception_code" in typed and code:
				# not read: the code says why; the estimate step works out the units
				typed.update({"present_reading": 0, "present_export_reading": 0, "reading_date": None})
			elif typed.get("present_reading") not in (None, "") and not typed.get("reading_date") and not r.reading_date:
				typed["reading_date"] = nowdate()
			if typed.get("present_reading") in (None, "") and "present_reading" in typed and not code:
				typed.update({"present_reading": 0, "reading_date": None})  # a reading cleared: back to not entered
			changed = [f for f, v in typed.items() if not _same(f, r.get(f), v)]
			if changed:
				for f in changed:
					r.set(f, typed[f] if f in ("reading_date", "exception_code") else (cint(typed[f]) if f == "smart_meter_mute" else flt(typed[f])))
				if not r.exception_code:
					r.exception_code = None
				if any(f != "smart_meter_mute" for f in changed) and r.source != "Office":
					r.source = "Office"
				r.save()
			results.append({"name": r.name, "ok": True, "saved": bool(changed), **{f: r.get(f) for f in FIELDS if f not in ("name",)},
			                "reading_date": cstr(r.reading_date or ""), "previous_reading_date": cstr(r.previous_reading_date or ""),
			                "entered": bool(r.reading_date or r.exception_code)})
		except Exception as e:
			frappe.clear_last_message()
			results.append({"name": row.get("name"), "ok": False, "error": frappe.utils.strip_html(cstr(e)) or e.__class__.__name__})
	if any(x["ok"] and x.get("saved") for x in results):
		c.refresh_counts()
	return {"rows": results, "totals": _totals(c.name), "saved": sum(1 for x in results if x["ok"] and x.get("saved")),
	        "failed": sum(1 for x in results if not x["ok"])}
