# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Meter readings: history lookups, reading sheets, review actions, estimates, import, closing
(FS-RDG-01, 04-11, 15). Calculations themselves live in reading_engine.py.

A reading counts as entered when its Reading Date is set (number fields store 0 when empty, so a
zero reading and "not entered" would otherwise look the same)."""

import frappe
from frappe import _
from frappe.utils import add_months, cstr, flt, getdate, now_datetime, nowdate

from utility_billing.utility_billing import bulk, readcache
from utility_billing.utility_billing import uniqueness
from utility_billing.utility_billing import reading_engine as engine
from utility_billing.utility_billing import routes
from utility_billing.utility_billing import rights

DONE = ("Valid", "Approved", "Estimated")
NET_METERING = "Net Metering"  # Meter.meter_type of a bi-directional (import and export) meter
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
		                  "meter", "consumption_type", "estimated_since_units", "status", "present_export_reading"],
		order_by=order,
		limit=limit,
	)


def monthly_history(connection, before_month, months=6):
	"""Actual monthly units before `before_month`, most recent first. True-up months (which cover
	several months) are left out so they don't distort averages."""
	found, cached = readcache.history(connection, before_month, months)
	if found:
		return [flt(r.units) for r in cached]
	rows = _readings(
		connection,
		{"billing_month": ["<", month_start(before_month)], "status": ["in", ACTUAL_DONE],
		 "consumption_type": "Actual", "estimated_since_units": 0},
		limit=months,
	)
	return [flt(r.units) for r in rows]


def same_month_last_year(connection, month):
	found, cached = readcache.same_month_last_year(connection, month)
	if found:
		return flt(cached.units) if cached else None
	rows = _readings(
		connection,
		{"billing_month": add_months(month_start(month), -12), "status": ["in", ACTUAL_DONE],
		 "consumption_type": "Actual", "estimated_since_units": 0},
		limit=1,
	)
	return flt(rows[0].units) if rows else None


def last_actual(connection, before_month):
	found, cached = readcache.last_actual(connection, before_month)
	if found:
		return cached
	rows = _readings(
		connection,
		{"billing_month": ["<", month_start(before_month)], "status": ["in", ACTUAL_DONE], "consumption_type": "Actual"},
		limit=1,
	)
	return rows[0] if rows else None


def estimated_since(connection, after_month, before_month):
	"""Units billed as estimates after the last actual reading and before this month (true-up)."""
	found, cached = readcache.estimated_since(connection, after_month, before_month)
	if found:
		return cached
	filters = {"billing_month": ["<", month_start(before_month)], "status": "Estimated"}
	if after_month:
		filters["billing_month"] = ["between", [add_months(month_start(after_month), 1), add_months(month_start(before_month), -1)]]
	return sum(flt(r.units) for r in _readings(connection, filters))


def reading_context(connection_name, month):
	"""Previous reading, meter, multiplier, digits, units on a replaced meter and units already
	estimated, for a connection's reading in `month`."""
	meters = readcache.meters_of(connection_name)  # a cycle's bulk work reads a group of accounts at a time
	if meters is None:
		meters = frappe.get_doc("Connection", connection_name).meters
	current = next((m for m in meters if not m.removed_on), None)
	if not current:
		frappe.throw(_("Connection {0} has no installed meter.").format(connection_name))
	meter = readcache.meter_info(current.meter)
	last = last_actual(connection_name, month)
	ctx = frappe._dict(
		meter=current.meter, multiplier=flt(meter.multiplier) or 1, digits=meter.digits or 5,
		previous_reading=flt(current.start_reading), previous_reading_date=current.installed_on,
		old_meter_units=0, estimated_since_units=0,
		# a net-metering meter's export register (increment 28): from the last actual reading, or as the meter was installed
		net_metering=1 if meter.meter_type == NET_METERING else 0, previous_export_reading=flt(current.get("start_export_reading")),
		old_meter_export_units=0, smart_meter=frappe.utils.cint(meter.get("smart_meter")),
	)
	if last and last.meter == current.meter:
		ctx.previous_reading, ctx.previous_reading_date = flt(last.present_reading), last.reading_date
		ctx.previous_export_reading = flt(last.get("present_export_reading"))
	elif last:
		# Meter changed since the last actual reading: bill the old meter up to its end reading.
		old = next((m for m in meters if m.meter == last.meter and m.removed_on), None)
		if old and frappe.utils.cint(old.get("unreadable")):
			return ctx  # FS-WF-08: the old meter couldn't be read at removal, so the months billed on averages stand (no true-up)
		if old:
			old_meter = readcache.meter_info(old.meter) or frappe._dict()
			old_mult = flt(old_meter.multiplier) or 1
			ctx.old_meter_units = max(0, (flt(old.end_reading) - flt(last.present_reading)) * old_mult)
			# a replaced net-metering meter: what it exported after its last reading is credited with this month's exports (increment 28)
			if flt(old.get("end_export_reading")) and old_meter.meter_type == NET_METERING:
				ctx.old_meter_export_units = max(0, (flt(old.end_export_reading) - flt(last.get("present_export_reading"))) * old_mult)
				ctx.net_metering = 1  # credited even if the new meter has no export register
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
	todo = [n for n in sorted(names, key=lambda n: (n not in routed, routed.get(n, {}).get("rank", 0))) if n not in existing]
	if bulk.plain("Meter Reading"):
		created = _create_readings(c, todo)
	else:  # something on the site reacts to a reading being saved: one document at a time, so it still fires
		for conn in todo:
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


FILE_SOURCES = ("Import", "Mobile", "MDM")  # an office spreadsheet, the mobile reading app's file, the meter data system's file
# other names a file may use for our columns (the mobile app's and the MDM's formats aren't fixed yet: user, 2026-11-24)
HEADER_ALIASES = {"present export reading": "export reading", "meter number": "meter no", "meter serial": "meter no", "reading": "present reading",
                  "import reading": "present reading", "not communicating": "mute", "smart meter mute": "mute"}


@frappe.whitelist()
def upload_readings(cycle: str, file_url: str, source: str = "Import"):
	"""Apply readings from an Excel, CSV or text file (FS-RDG-04). Columns: Reference No or Meter No, Present Reading,
	Reading Date (optional, default today), Exception Code (optional), MDI (optional, kW), Export Reading (net-metering meters),
	Mute (yes for a smart meter that isn't communicating and was read by hand).
	`source` says where the file is from (increment 47): an office spreadsheet (Import), the mobile reading app (Mobile) or the
	meter data system (MDM, the only one whose readings a smart meter takes).
	Every row gets the same checks as office entry; rows that can't be applied are returned with the reason."""
	if source not in FILE_SOURCES:
		frappe.throw(_("Say where the file is from: {0}.").format(", ".join(FILE_SOURCES)))
	c = cycle_doc(cycle)
	rows = _read_rows(file_url)
	if not rows:
		frappe.throw(_("The file has no rows."))
	header = [" ".join(cstr(h).strip().lower().replace("_", " ").split()) for h in rows[0]]
	header = [HEADER_ALIASES.get(h, h) if HEADER_ALIASES.get(h, h) not in header else h for h in header]
	col = {k: header.index(k) for k in ("reference no", "meter no", "present reading", "reading date", "exception code", "mdi", "export reading", "mute")
	       if k in header}
	if ("reference no" not in col and "meter no" not in col) or ("present reading" not in col and "exception code" not in col):
		frappe.throw(_("The file needs the columns Reference No (or Meter No) and Present Reading (or Exception Code)."))
	updated, errors = 0, []
	fast = bulk.plain("Meter Reading")
	in_cycle = dict(frappe.db.sql("select connection, name from `tabMeter Reading` where reading_cycle = %s", c.name))
	by_meter = {}
	if "meter no" in col:  # a meter data system knows the meter, not our reference number
		by_meter = {uniqueness.norm(m, "serial"): conn for conn, m in frappe.db.sql(
			"select connection, meter from `tabMeter Reading` where reading_cycle = %s", c.name)}
	lines = []
	for i, row in enumerate(rows[1:], start=2):
		cell = lambda key: cstr(row[col[key]] if key in col and col[key] < len(row) else "").strip()  # noqa: E731
		ref = cell("reference no").split(".")[0]
		meter = cell("meter no").removesuffix(".0")
		if not ref and meter:
			ref = by_meter.get(uniqueness.norm(meter, "serial"))
			if not ref:
				errors.append({"row": i, "reference_no": meter, "error": _("No reading for meter {0} in the cycle").format(meter)})
				continue
		if ref:
			lines.append((i, row, ref))
	with readcache.for_cycle(c):
		for group in bulk.in_chunks(lines, GROUP):
			loaded = _load_readings(c, [in_cycle[ref] for _i, _row, ref in group if ref in in_cycle]) if fast else {}
			updated += _apply_rows(group, col, in_cycle, loaded, errors, fast, source)
	c.refresh_counts()
	return {"updated": updated, "errors": errors}


def kept_by_hand(r, present, date) -> bool:
	"""A smart meter that wasn't communicating was read by hand, and now the MDM sends a reading for the same cycle: the hand
	reading stays (nothing is overwritten silently) and the difference goes to the reviewer. True when that is the case."""
	if not (frappe.utils.cint(r.smart_meter_mute) and r.reading_date and r.source != "MDM"):
		return False
	note = _("The MDM then sent {0} for {1}; the reading taken by hand ({2}) is kept.").format(flt(present), frappe.utils.formatdate(date), flt(r.present_reading))
	lines = [x for x in (r.checks or "").split("\n") if x and not x.startswith(_("The MDM then sent"))]
	frappe.db.set_value("Meter Reading", r.name, {"checks": "\n".join([*lines, note]), "status": "Flagged"})
	return True


def receive_mdm(rows, allowed=None) -> dict:
	"""Readings sent by a meter data system through the API (increment 47): [{meter_no, reading, reading_date, export_reading, mdi}].
	Each goes to the meter's reading in the newest open reading cycle; only smart meters take them. `allowed(society)` refuses
	a society the caller may not write to. Returns what was taken and what wasn't, with the reason."""
	if not isinstance(rows, list) or not rows:
		frappe.throw(_("Send the readings as a list."))
	if len(rows) > 5000:
		frappe.throw(_("Send at most 5,000 readings in one call."))
	accepted, rejected = [], []
	for row in rows:
		row = frappe._dict(row if isinstance(row, dict) else {})
		meter_no = cstr(row.get("meter_no")).strip()
		try:
			if not meter_no or row.get("reading") in (None, ""):
				raise frappe.ValidationError(_("Each reading needs meter_no and reading"))
			meter = frappe.db.get_value("Meter", {"serial_no": meter_no}) or (meter_no if frappe.db.exists("Meter", meter_no) else None)
			found = meter and frappe.db.sql("""select r.name, r.society from `tabMeter Reading` r join `tabReading Cycle` c on c.name = r.reading_cycle
			                                   where r.meter = %s and c.status != 'Closed' order by c.billing_month desc limit 1""", meter, as_dict=True)
			if not found:
				raise frappe.ValidationError(_("No open reading cycle has a reading for this meter"))
			if allowed:
				allowed(found[0].society)
			r = frappe.get_doc("Meter Reading", found[0].name)
			date = getdate(row.get("reading_date")) if row.get("reading_date") else getdate(nowdate())
			if kept_by_hand(r, row.reading, date):
				rejected.append({"meter_no": meter_no, "reason": _("Read by hand already (not communicating): the hand reading is kept and flagged for review")})
				continue
			r.present_reading, r.reading_date, r.exception_code = flt(row.reading), date, None
			if row.get("export_reading") not in (None, ""):
				if not frappe.utils.cint(r.net_metering):
					raise frappe.ValidationError(_("An export reading for a meter that isn't a net-metering meter"))
				r.present_export_reading = flt(row.export_reading)
			if row.get("mdi") not in (None, ""):
				r.mdi_kw = flt(row.mdi)
			r.flags.from_mdm = True
			r.flags.ignore_permissions = True
			r.save()
			accepted.append({"meter_no": meter_no, "reading": r.name, "status": r.status, "units": flt(r.units)})
		except Exception as e:
			frappe.clear_last_message()
			rejected.append({"meter_no": meter_no, "reason": cstr(e) or e.__class__.__name__})
	for cycle in {frappe.db.get_value("Meter Reading", a["reading"], "reading_cycle") for a in accepted}:
		frappe.get_doc("Reading Cycle", cycle).refresh_counts()
	return {"accepted": len(accepted), "rejected": len(rejected), "readings": accepted, "errors": rejected}


GROUP = 500  # the performance step, part 2: a cycle reads and writes its readings this many at a time


def _create_readings(c, connections) -> int:
	"""The cycle's empty readings for these accounts, built as an insert would build them (the previous reading, meter and
	multiplier from the account) and written a group at a time. Held to the one-by-one way by test_bulk_readings."""
	writer, created = bulk.Inserter(), 0
	with readcache.for_cycle(c):
		for group in bulk.in_chunks(connections, GROUP):
			readcache.prime(group)
			for conn in group:
				info = readcache.connection_info(conn) or frappe._dict()
				r = writer.add({"doctype": "Meter Reading", "reading_cycle": c.name, "connection": conn, "source": "Office",
				                "consumer_name": info.get("consumer_name"), "property": info.get("property")})  # the fields fetched from the account
				r.flags.in_bulk = True  # the accounts were chosen as having no reading in this cycle
				r.validate()
				created += 1
			writer.flush()
	return created


def _load_readings(c, names) -> dict:
	"""{connection: Meter Reading document} for a group of the cycle's readings, read together with what their checks need."""
	if not names:
		readcache.prime([])
		return {}
	rows = frappe.db.sql("select * from `tabMeter Reading` where name in %s", (names,), as_dict=True)
	readcache.prime([r.connection for r in rows], history=True)
	out = {}
	for row in rows:
		doc = frappe.get_doc(dict(row, doctype="Meter Reading"))
		doc._doc_before_save = frappe.get_doc(dict(row, doctype="Meter Reading"))
		out[row.connection] = doc
	return out


def _apply_rows(group, col, in_cycle, loaded, errors, fast, source="Import") -> int:
	"""Apply a group of file rows. A reading entered for the first time is checked and written without the document's save
	(the same validate, one update); a reading that already had a value is a correction and is saved as a document, with its
	change history. Returns the number of readings updated."""
	updated, stamp, user = 0, frappe.utils.now(), frappe.session.user
	for i, row, ref in group:
		def val(key):
			return row[col[key]] if key in col and col[key] < len(row) else None

		name = in_cycle.get(ref)
		if not name:
			errors.append({"row": i, "reference_no": ref, "error": _("No reading for this connection in the cycle")})
			continue
		try:
			r = loaded.get(ref) if fast else None
			first_entry = bool(r) and not r.reading_date and not r.exception_code and r.status == "Pending"
			if not first_entry:
				r = frappe.get_doc("Meter Reading", name)
			if cstr(val("mute")).strip().lower() in ("1", "yes", "y", "true", "mute"):
				r.smart_meter_mute = 1  # a smart meter that isn't communicating, read by the meter reader
			code = cstr(val("exception code")).strip()
			if source == "MDM":
				if kept_by_hand(r, val("present reading"), getdate(val("reading date")) if val("reading date") else getdate(nowdate())):
					loaded.pop(ref, None)
					errors.append({"row": i, "reference_no": ref, "error": _("Read by hand already (not communicating): the hand reading is kept and flagged for review")})
					continue
				r.flags.from_mdm = True
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
				if val("export reading") not in (None, ""):
					if not frappe.utils.cint(r.net_metering):
						raise frappe.ValidationError(_("An export reading for a meter that isn't a net-metering meter"))
					r.present_export_reading = flt(val("export reading"))
			r.reading_date = getdate(val("reading date")) if val("reading date") else nowdate()
			r.source = source
			if first_entry:
				r.flags.in_bulk = True
				r.validate()
				r.modified, r.modified_by = stamp, user
				r.db_update()
				r.on_update()
			else:
				r.save()
			updated += 1
		except Exception as e:
			frappe.clear_last_message()
			loaded.pop(ref, None)  # a row that failed must not leave a half-changed reading for a later row of the file
			errors.append({"row": i, "reference_no": ref, "error": cstr(e) or e.__class__.__name__})
	return updated


def _read_rows(file_url):
	file = frappe.get_doc("File", {"file_url": file_url})
	content = file.get_content()
	if file_url.lower().endswith((".xlsx", ".xls")):
		from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

		return read_xlsx_file_from_attached_file(fcontent=content)
	if file_url.lower().endswith(".txt"):
		return _read_text(content)
	from frappe.utils.csvutils import read_csv_content

	return read_csv_content(content)


def _read_text(content) -> list[list]:
	"""A text file from a mobile reading app or a meter data system: one reading a line, the columns parted by tabs, bars,
	semicolons or commas (whichever the first line uses most)."""
	import csv  # noqa: PLC0415

	text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else cstr(content)
	lines = [x for x in text.splitlines() if x.strip()]
	if not lines:
		return []
	mark = max(("\t", "|", ";", ","), key=lines[0].count)
	return [[cell.strip() for cell in row] for row in csv.reader(lines, delimiter=mark)]


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
	if frappe.utils.cint(r.smart_meter) and not frappe.utils.cint(r.smart_meter_mute):
		frappe.throw(_("This is a smart meter's reading from the MDM: it isn't corrected by hand. Ask for a re-read; if the meter isn't "
		               "communicating, tick Not Communicating on the reading and enter the meter reader's reading."))
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
