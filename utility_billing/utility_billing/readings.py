# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Meter readings: history lookups, reading sheets, review actions, estimates, import, closing
(FS-RDG-01, 04-11, 15). Calculations themselves live in reading_engine.py.

A reading counts as entered when its Reading Date is set (number fields store 0 when empty, so a
zero reading and "not entered" would otherwise look the same)."""

import frappe
from frappe import _
from frappe.utils import add_months, cstr, flt, getdate, now_datetime, nowdate

from utility_billing.utility_billing import reading_engine as engine
from utility_billing.utility_billing import routes
from utility_billing.utility_billing import rights

DONE = ("Valid", "Approved", "Estimated")
ACTUAL_DONE = ("Valid", "Approved")
REVIEW_ROLES = {"Billing Officer", "Billing Manager", "System Manager"}


def month_start(d):
	return getdate(d).replace(day=1)


def require_reviewer():
	if not rights.can("readings.review"):
		frappe.throw(_("Only a Billing Officer or Billing Manager can do this."), frappe.PermissionError)


# ----- history -----
def _readings(connection, filters=None, order="billing_month desc", limit=None, fields=None):
	return frappe.get_all(
		"Meter Reading",
		filters={"connection": connection, **(filters or {})},
		fields=fields or ["name", "billing_month", "units", "raw_units", "present_reading", "reading_date",
		                  "meter", "consumption_type", "estimated_since_units", "status"],
		order_by=order,
		limit=limit,
	)


def monthly_history(connection, before_month, months=6):
	"""Actual monthly units before `before_month`, most recent first. True-up months (which cover
	several months) are left out so they don't distort averages."""
	rows = _readings(
		connection,
		{"billing_month": ["<", month_start(before_month)], "status": ["in", ACTUAL_DONE],
		 "consumption_type": "Actual", "estimated_since_units": 0},
		limit=months,
	)
	return [flt(r.units) for r in rows]


def same_month_last_year(connection, month):
	rows = _readings(
		connection,
		{"billing_month": add_months(month_start(month), -12), "status": ["in", ACTUAL_DONE],
		 "consumption_type": "Actual", "estimated_since_units": 0},
		limit=1,
	)
	return flt(rows[0].units) if rows else None


def last_actual(connection, before_month):
	rows = _readings(
		connection,
		{"billing_month": ["<", month_start(before_month)], "status": ["in", ACTUAL_DONE], "consumption_type": "Actual"},
		limit=1,
	)
	return rows[0] if rows else None


def estimated_since(connection, after_month, before_month):
	"""Units billed as estimates after the last actual reading and before this month (true-up)."""
	filters = {"billing_month": ["<", month_start(before_month)], "status": "Estimated"}
	if after_month:
		filters["billing_month"] = ["between", [add_months(month_start(after_month), 1), add_months(month_start(before_month), -1)]]
	return sum(flt(r.units) for r in _readings(connection, filters))


def reading_context(connection_name, month):
	"""Previous reading, meter, multiplier, digits, units on a replaced meter and units already
	estimated, for a connection's reading in `month`."""
	conn = frappe.get_doc("Connection", connection_name)
	current = next((m for m in conn.meters if not m.removed_on), None)
	if not current:
		frappe.throw(_("Connection {0} has no installed meter.").format(connection_name))
	meter = frappe.db.get_value("Meter", current.meter, ["multiplier", "digits"], as_dict=True)
	last = last_actual(connection_name, month)
	ctx = frappe._dict(
		meter=current.meter, multiplier=flt(meter.multiplier) or 1, digits=meter.digits or 5,
		previous_reading=flt(current.start_reading), previous_reading_date=current.installed_on,
		old_meter_units=0, estimated_since_units=0,
	)
	if last and last.meter == current.meter:
		ctx.previous_reading, ctx.previous_reading_date = flt(last.present_reading), last.reading_date
	elif last:
		# Meter changed since the last actual reading: bill the old meter up to its end reading.
		old = next((m for m in conn.meters if m.meter == last.meter and m.removed_on), None)
		if old and frappe.utils.cint(old.get("unreadable")):
			return ctx  # FS-WF-08: the old meter couldn't be read at removal, so the months billed on averages stand (no true-up)
		if old:
			old_mult = flt(frappe.db.get_value("Meter", old.meter, "multiplier")) or 1
			ctx.old_meter_units = max(0, (flt(old.end_reading) - flt(last.present_reading)) * old_mult)
	ctx.estimated_since_units = estimated_since(connection_name, last.billing_month if last else None, month)
	return ctx


def get_billable_units(connection, month):
	"""Billable units of a connection for a billing month, or None if there's no final reading.
	Used by the bill engine (energy) and for FPA on the units of an earlier month."""
	rows = _readings(connection, {"billing_month": month_start(month), "status": ["in", DONE]}, limit=1)
	return flt(rows[0].units) if rows else None


# ----- reading cycle actions -----
def cycle_doc(name):
	cycle = frappe.get_doc("Reading Cycle", name)
	cycle.check_permission("write")
	if cycle.status == "Closed":
		frappe.throw(_("Reading cycle {0} is closed.").format(name))
	return cycle


@frappe.whitelist()
def create_reading_sheet(cycle: str):
	"""One Meter Reading per active connection with an installed meter (FS-RDG-01)."""
	c = cycle_doc(cycle)
	existing = set(frappe.get_all("Meter Reading", filters={"reading_cycle": c.name}, pluck="connection"))
	created = 0
	routed = routes.plan(c)  # the sheet follows the order of the routes given a reader (FS-MST-09)
	names = frappe.get_all(
		"Connection",
		filters={"society": c.society, "utility": c.utility, "status": "Active", "current_meter": ["is", "set"]},
		pluck="name",
		order_by="name",
	)
	for conn in sorted(names, key=lambda n: (n not in routed, routed.get(n, {}).get("rank", 0))):
		if conn in existing:
			continue
		frappe.get_doc({"doctype": "Meter Reading", "reading_cycle": c.name, "connection": conn, "source": "Office"}).insert()
		created += 1
	routes.apply(c)
	checks = routes.check_meters(c)  # the routes' check meters are read with the month (FS-RDG-12)
	c.refresh_counts()
	return {"created": created, "total": c.total_readings, "check_meters": checks}


@frappe.whitelist()
def estimate_unread(cycle: str):
	"""Estimate every reading still Pending or Re-read Requested, or with an exception code (FS-RDG-10)."""
	require_reviewer()
	c = cycle_doc(cycle)
	method = frappe.db.get_value("Society", c.society, "estimation_method") or engine.AVG_3
	done = 0
	for name in frappe.get_all(
		"Meter Reading", filters={"reading_cycle": c.name, "status": ["in", ["Pending", "Re-read Requested"]]}, pluck="name"
	):
		r = frappe.get_doc("Meter Reading", name)
		r.apply_estimate(method)
		r.save()
		done += 1
	c.refresh_counts()
	return {"estimated": done}


@frappe.whitelist()
def close_cycle(cycle: str):
	require_reviewer()
	c = cycle_doc(cycle)
	c.refresh_counts()
	open_items = c.pending_readings + c.flagged_readings
	if open_items:
		frappe.throw(
			_("{0} readings are still pending or flagged. Review them or estimate unread meters before closing.").format(open_items)
		)
	c.status = "Closed"
	c.closed_by = frappe.session.user
	c.closed_on = now_datetime()
	c.save()
	return c.status


@frappe.whitelist()
def upload_readings(cycle: str, file_url: str):
	"""Apply readings from an Excel or CSV file (FS-RDG-04). Columns: Reference No, Present Reading,
	Reading Date (optional, default today), Exception Code (optional), MDI (optional, kW). Every row gets the same checks
	as office entry; rows that can't be applied are returned with the reason."""
	c = cycle_doc(cycle)
	rows = _read_rows(file_url)
	if not rows:
		frappe.throw(_("The file has no rows."))
	header = [cstr(h).strip().lower() for h in rows[0]]
	col = {k: header.index(k) for k in ("reference no", "present reading", "reading date", "exception code", "mdi") if k in header}
	if "reference no" not in col or ("present reading" not in col and "exception code" not in col):
		frappe.throw(_("The file needs the columns Reference No and Present Reading (or Exception Code)."))
	updated, errors = 0, []
	for i, row in enumerate(rows[1:], start=2):
		def val(key):
			return row[col[key]] if key in col and col[key] < len(row) else None

		ref = cstr(val("reference no")).strip().split(".")[0]
		if not ref:
			continue
		name = frappe.db.get_value("Meter Reading", {"reading_cycle": c.name, "connection": ref}, "name")
		if not name:
			errors.append({"row": i, "reference_no": ref, "error": _("No reading for this connection in the cycle")})
			continue
		try:
			r = frappe.get_doc("Meter Reading", name)
			code = cstr(val("exception code")).strip()
			if code:
				r.exception_code = code
			else:
				present = val("present reading")
				if present in (None, ""):
					raise frappe.ValidationError(_("Present Reading is empty"))
				r.present_reading = flt(present)
				r.exception_code = None
				if val("mdi") not in (None, ""):
					r.mdi_kw = flt(val("mdi"))  # the maximum demand shown on the meter (MDI), in kW
			r.reading_date = getdate(val("reading date")) if val("reading date") else nowdate()
			r.source = "Import"
			r.save()
			updated += 1
		except Exception as e:
			frappe.clear_last_message()
			errors.append({"row": i, "reference_no": ref, "error": cstr(e) or e.__class__.__name__})
	c.refresh_counts()
	return {"updated": updated, "errors": errors}


def _read_rows(file_url):
	file = frappe.get_doc("File", {"file_url": file_url})
	content = file.get_content()
	if file_url.lower().endswith((".xlsx", ".xls")):
		from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

		return read_xlsx_file_from_attached_file(fcontent=content)
	from frappe.utils.csvutils import read_csv_content

	return read_csv_content(content)


# ----- review actions (FS-RDG-07) -----
def _review_doc(name, allowed_status):
	require_reviewer()
	r = frappe.get_doc("Meter Reading", name)
	r.check_permission("write")
	if frappe.db.get_value("Reading Cycle", r.reading_cycle, "status") == "Closed":
		frappe.throw(_("The reading cycle is closed."))
	if r.status not in allowed_status:
		frappe.throw(_("This reading is {0}; that action isn't available.").format(r.status))
	return r


def _mark_reviewed(r, note=None):
	r.reviewed_by = frappe.session.user
	r.reviewed_on = now_datetime()
	if note:
		r.review_note = note


@frappe.whitelist()
def approve_reading(name: str, note: str):
	r = _review_doc(name, ("Flagged",))
	if len(cstr(note).strip()) < 5:
		frappe.throw(_("Write why this reading is accepted (at least 5 characters)."))
	_mark_reviewed(r, note.strip())
	r.flags.approve_as_is = True
	r.save()
	return r.status


@frappe.whitelist()
def correct_reading(name: str, present_reading: float, reason: str):
	r = _review_doc(name, ("Flagged", "Valid"))
	if len(cstr(reason).strip()) < 5:
		frappe.throw(_("Give the reason for the correction (at least 5 characters)."))
	r.corrected_from = r.present_reading
	r.correction_reason = reason.strip()
	r.present_reading = flt(present_reading)
	r.exception_code = None
	r.reading_date = r.reading_date or nowdate()
	_mark_reviewed(r)
	r.save()
	return r.status


@frappe.whitelist()
def request_reread(name: str, note: str):
	r = _review_doc(name, ("Flagged", "Valid"))
	_mark_reviewed(r, cstr(note).strip() or _("Re-read requested"))
	r.flags.request_reread = True
	r.save()
	return r.status
