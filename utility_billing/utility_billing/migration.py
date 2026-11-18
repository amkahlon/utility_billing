# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Migration Workbench (increment 29): a society's data from its previous system, in one Excel workbook, through a mapping profile.

User's decisions, 2026-11-18: every society's source system is different (SAP is the one known); 12 months of history come across;
old account numbers stay usable for six months after go-live; a parallel run of about two months comes before go-live.

The workbook has a sheet per kind of data (TARGETS). A Migration Profile says which source column is which of our fields, which source
codes mean which of our values, how house numbers are written and how dates are. A Migration Batch:
  check -> a dry run that writes nothing and lists every problem (errors stop the load; warnings don't);
  load  -> creates the network, properties, consumers, meters and connections (with the last readings as the meters' start readings),
           the 12-month history as Legacy Bills, and an Opening Balance Import from the Balances sheet; all in one transaction;
  undo  -> before approval, removes everything the batch created;
  approve (workflow, not the maker) -> the batch can no longer be undone. The opening balances are approved and posted as before.
"""

import datetime
import io
import json
import re
from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

TARGETS = {
	"Connections": ["old_account_no", "old_customer_id", "old_collection_no", "consumer_name", "cnic", "ntn", "filer", "mobile", "address",
	                "house", "block", "sector", "plot_area", "plot_unit", "property_type", "utility", "category", "sanctioned_load_kw", "phase",
	                "connection_date", "status", "meter_no", "meter_make", "meter_type", "multiplier", "digits", "feeder", "transformer", "route"],
	"Readings": ["old_account_no", "meter_no", "last_reading", "last_export_reading", "reading_date"],
	"Balances": ["old_account_no", "current_bill", "arrears", "surcharge", "fees", "credit", "security_deposit", "plan_remaining", "plan_installments",
	             "solar_credit_units"],
	"History": ["old_account_no", "month", "units", "current_amount", "arrears", "adjustment", "surcharge", "bill_amount", "paid_on", "paid_amount",
	            "balance"],
	"Network": ["kind", "code", "name", "feeder", "kva"],
	# the parallel run: the previous system's bills of a month, and the payments it took
	"Old Bills": ["old_account_no", "units", "current_bill", "arrears", "surcharge", "payable", "balance"],
	"Payments": ["old_account_no", "paid_on", "amount", "reference", "mode"],
}
LOAD_SHEETS = ("Connections", "Readings", "Balances", "History", "Network")
REQUIRED_SHEETS = ("Connections",)
NUMBERS = {"sanctioned_load_kw", "multiplier", "digits", "plot_area", "last_reading", "last_export_reading", "current_bill", "arrears", "surcharge",
           "fees", "credit", "security_deposit", "plan_remaining", "plan_installments", "solar_credit_units", "units", "current_amount", "adjustment",
           "bill_amount", "paid_amount", "balance", "kva", "payable", "amount"}
DATES = {"connection_date", "reading_date", "month", "paid_on"}
MAPPED = ("utility", "category", "status", "phase", "meter_type", "property_type", "plot_unit", "filer")

# SAP IS-U standard field names (exports through SE16N or a report): BUT000 partners, FKKVKP contract accounts, EVER contracts,
# EANL installations, EQUI/EGER devices, EABL meter readings, ERCH bills, DFKKOP open items.
SAP_ISU = [
	("Connections", "VKONT", "old_account_no"), ("Connections", "GPART", "old_customer_id"), ("Connections", "NAME_ORG1", "consumer_name"),
	("Connections", "TEL_NUMBER", "mobile"), ("Connections", "STREET", "address"), ("Connections", "HOUSE_NUM1", "house"),
	("Connections", "TARIFTYP", "category"), ("Connections", "SPARTE", "utility"), ("Connections", "EINZDAT", "connection_date"),
	("Connections", "SERNR", "meter_no"), ("Connections", "HERST", "meter_make"), ("Connections", "ANLART", "meter_type"),
	("Readings", "VKONT", "old_account_no"), ("Readings", "SERNR", "meter_no"), ("Readings", "V_ZWSTAND", "last_reading"),
	("Readings", "ADAT", "reading_date"),
	("Balances", "VKONT", "old_account_no"), ("Balances", "BETRH", "arrears"),
	("History", "VKONT", "old_account_no"), ("History", "BEGABRPE", "month"), ("History", "VERBRAUCH", "units"),
	("History", "BETRW", "bill_amount"), ("History", "AUGDT", "paid_on"), ("History", "AUGBT", "paid_amount"),
]


def _key(v) -> str:
	return " ".join(str(v or "").strip().lower().replace("_", " ").split())


# ---------------------------------------------------------------- profile
def profile_of(name) -> frappe._dict:
	"""The profile as lookups; with no profile, our own column names and day-first dates."""
	if not name:
		return frappe._dict(name=None, society=None, columns={}, valuemap={}, pattern=None, dayfirst=True, yearfirst=False, utility=None, category=None)
	p = frappe.get_doc("Migration Profile", name)
	cols = defaultdict(dict)
	for c in p.columns:
		cols[c.sheet][_key(c.source_column)] = c.target.strip()
	values = defaultdict(dict)
	for v in p.value_maps:
		values[v.target.strip()][_key(v.source_value)] = v.our_value.strip()
	fmt = p.date_format or ""
	return frappe._dict(name=p.name, society=p.society, columns=cols, valuemap=values, pattern=(p.house_pattern or "").strip() or None,
	                    dayfirst=fmt.startswith("Day"), yearfirst=fmt.startswith("Year"), utility=p.default_utility, category=p.default_category)


def mapped(prof, target, v):
	if v in (None, ""):
		return v
	return prof.valuemap.get(target, {}).get(_key(v), str(v).strip())


def parse_date(v, prof):
	if v in (None, ""):
		return None
	if isinstance(v, datetime.datetime):
		return v.date()
	if isinstance(v, datetime.date):
		return v
	from dateutil import parser  # noqa: PLC0415

	return parser.parse(str(v).strip(), dayfirst=prof.dayfirst, yearfirst=prof.yearfirst).date()


def split_house(row, prof):
	"""House, block and sector from the source's house column, by the profile's pattern."""
	house = str(row.get("house") or "").strip()
	block, sector = str(row.get("block") or "").strip(), str(row.get("sector") or "").strip()
	if prof.pattern and house:
		m = re.fullmatch(prof.pattern, house, re.I)
		if not m:
			return None
		parts = m.groupdict()
		house = (parts.get("house") or "").strip()
		block = block or (parts.get("block") or "").strip()
		sector = sector or (parts.get("sector") or "").strip()
	return house, block, sector


def property_no(house, block):
	return f"{block}-{house}" if block else house


# ---------------------------------------------------------------- workbook
def read_workbook(file_url) -> dict:
	"""{sheet name as in TARGETS: [[cells], ...]} from the attached .xlsx (sheet names matched without case)."""
	if not (file_url or "").lower().endswith(".xlsx"):
		frappe.throw(_("Attach the workbook as an Excel .xlsx file."))
	from openpyxl import load_workbook  # noqa: PLC0415

	content = frappe.get_doc("File", {"file_url": file_url}).get_content()
	wb = load_workbook(io.BytesIO(content if isinstance(content, bytes) else content.encode()), read_only=True, data_only=True)
	names = {_key(n): n for n in TARGETS}
	out = {}
	for ws in wb.worksheets:
		sheet = names.get(_key(ws.title))
		if sheet:
			out[sheet] = [list(r) for r in ws.iter_rows(values_only=True)]
	return out


def normalise(sheet, table, prof, problems):
	"""Row dicts keyed by our field names (with `_row`), numbers and dates parsed; unreadable cells reported."""
	if not table:
		return []
	ours = {_key(t): t for t in TARGETS[sheet]}
	header = []
	for h in table[0]:
		k = _key(h)
		header.append(prof.columns.get(sheet, {}).get(k) or ours.get(k))
	unknown = [str(h) for h, t in zip(table[0], header) if h not in (None, "") and not t]
	if unknown:
		problems.append(dict(sheet=sheet, row_no=1, severity="Warning", problem=_("Columns not used: {0}.").format(", ".join(unknown))))
	rows = []
	for i, line in enumerate(table[1:], start=2):
		if not any(str(v or "").strip() for v in line):
			continue
		r = frappe._dict(_row=i)
		for t, v in zip(header, line):
			if not t or t not in TARGETS[sheet]:
				continue
			if isinstance(v, str):
				v = v.strip()
			try:
				if t in NUMBERS and v not in (None, ""):
					v = float(str(v).replace(",", "")) if not isinstance(v, (int, float)) else v
				elif t in DATES and v not in (None, ""):
					v = parse_date(v, prof)
			except (ValueError, OverflowError):
				problems.append(dict(sheet=sheet, row_no=i, account=str(r.get("old_account_no") or ""), severity="Error",
				                     problem=_("{0}: {1} isn't a {2}.").format(t, v, _("date") if t in DATES else _("number"))))
				v = None
			if t in ("old_account_no", "meter_no", "old_customer_id", "old_collection_no", "code", "reference") and v not in (None, ""):
				v = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)  # Excel may hand back 7402006006.0
			r[t] = v
		rows.append(r)
	return rows


# ---------------------------------------------------------------- check
def _category(value, utility):
	if not value:
		return None
	return frappe.db.get_value("Consumer Category", {"category_name": value, "utility": utility}) or (
		value if frappe.db.exists("Consumer Category", value) else None)


def analyse(batch) -> frappe._dict:
	"""Everything the load needs, and every problem, without writing anything."""
	prof = profile_of(batch.profile)
	if prof.society != batch.society:
		frappe.throw(_("Profile {0} belongs to society {1}.").format(batch.profile, prof.society))
	problems = []
	book = read_workbook(batch.workbook)
	for s in REQUIRED_SHEETS:
		if s not in book:
			frappe.throw(_("The workbook has no {0} sheet.").format(s))
	data = {s: normalise(s, book.get(s), prof, problems) for s in LOAD_SHEETS}
	as_of = getdate(batch.as_of_date)

	def err(sheet, r, text, account=None, severity="Error"):
		problems.append(dict(sheet=sheet, row_no=r._row if r else 0, account=account or (r.get("old_account_no") if r else "") or "",
		                     severity=severity, problem=text))

	# the network
	feeders, transformers = set(), set()
	for r in data["Network"]:
		kind = (mapped(prof, "kind", r.kind) or "").strip().title()
		r.kind = kind
		if kind not in ("Feeder", "Transformer") or not r.code:
			err("Network", r, _("Each row needs a kind (Feeder or Transformer) and a code."))
		elif kind == "Feeder":
			feeders.add(str(r.code))
		else:
			if not r.feeder:
				err("Network", r, _("Transformer {0} needs its feeder.").format(r.code))
			transformers.add(str(r.code))
	# accounts
	accounts, cnic_names = {}, defaultdict(set)
	seen_meters = Counter(str(r.meter_no) for r in data["Connections"] if r.meter_no)
	existing = set(frappe.get_all("Connection", filters={"society": batch.society, "legacy_account_no": ["is", "set"]}, pluck="legacy_account_no"))
	for r in data["Connections"]:
		ref = str(r.old_account_no or "").strip()
		if not ref:
			err("Connections", r, _("The old account number is missing."))
			continue
		if ref in accounts:
			err("Connections", r, _("Old account number {0} is in the file twice (rows {1} and {2}).").format(ref, accounts[ref]._row, r._row))
			continue
		if ref in existing:
			err("Connections", r, _("Old account number {0} is already in the system (migrated before?).").format(ref))
		if not r.consumer_name:
			err("Connections", r, _("The consumer's name is missing."))
		r.utility = mapped(prof, "utility", r.utility) or prof.utility
		if not r.utility or not frappe.db.exists("Utility Type", r.utility):
			err("Connections", r, _("Utility {0} isn't one of ours. Map it in the profile or set a default utility.").format(r.utility or _("(none)")))
		raw_category = r.category
		r.category = _category(mapped(prof, "category", r.category), r.utility) or prof.category
		if not r.category:
			err("Connections", r, _("Category {0} isn't one of ours for {1}. Map it in the profile.").format(raw_category or _("(none)"), r.utility))
		r.status = mapped(prof, "status", r.status) or "Active"
		if r.status not in ("Active", "Disconnected"):
			err("Connections", r, _("Status {0}: use Active or Disconnected (map it in the profile).").format(r.status))
		r.phase = mapped(prof, "phase", r.phase) or None
		if r.phase and r.phase not in ("Single", "Three"):
			err("Connections", r, _("Phase {0}: use Single or Three.").format(r.phase))
		r.property_type = mapped(prof, "property_type", r.property_type) or "House"
		if r.property_type not in ("House", "Apartment", "Plot", "Shop", "Office", "Commercial Building", "Other"):
			err("Connections", r, _("Property type {0} isn't one of ours.").format(r.property_type))
		r.plot_unit = mapped(prof, "plot_unit", r.plot_unit) or None
		if r.plot_unit and r.plot_unit not in ("Marla", "Kanal", "Sq Ft", "Sq Yd", "Sq M"):
			err("Connections", r, _("Plot unit {0} isn't one of ours.").format(r.plot_unit))
		r.filer = mapped(prof, "filer", r.filer) or None
		if r.filer and r.filer not in ("Filer", "Non-filer"):
			err("Connections", r, _("Filer status {0}: use Filer or Non-filer.").format(r.filer), severity="Warning")
			r.filer = None
		parts = split_house(r, prof)
		if not parts or not parts[0]:
			err("Connections", r, _("House number {0} doesn't match the profile's pattern.").format(r.house or _("(none)")))
		else:
			r.house, r.block, r.sector = parts
		metered = cint(frappe.get_cached_value("Utility Type", r.utility, "is_metered")) if r.utility and frappe.db.exists("Utility Type", r.utility) else 1
		r.meter_type = mapped(prof, "meter_type", r.meter_type) or "Single Phase"
		if metered:
			if not r.meter_no:
				err("Connections", r, _("A metered utility needs a meter number."))
			else:
				if seen_meters[str(r.meter_no)] > 1:
					err("Connections", r, _("Meter {0} is on more than one account in the file.").format(r.meter_no))
				if frappe.db.exists("Meter", str(r.meter_no)):
					err("Connections", r, _("Meter {0} is already in the system.").format(r.meter_no))
			if r.meter_type not in ("Single Phase", "Three Phase", "TOU", "Net Metering", "Prepaid", "Bulk / Check Meter"):
				err("Connections", r, _("Meter type {0} isn't one of ours.").format(r.meter_type))
		r.metered = metered
		if r.cnic:
			digits = re.sub(r"\D", "", str(r.cnic))
			if len(digits) != 13:
				err("Connections", r, _("CNIC {0} doesn't have 13 digits; it is left out.").format(r.cnic), severity="Warning")
				r.cnic = None
			else:
				r.cnic = f"{digits[:5]}-{digits[5:12]}-{digits[12]}"
				cnic_names[r.cnic].add(_key(r.consumer_name))
		if r.feeder and str(r.feeder) not in feeders and not frappe.db.exists("Feeder", f"{batch.society}-{r.feeder}"):
			err("Connections", r, _("Feeder {0} is neither in the Network sheet nor in the system.").format(r.feeder))
		if r.transformer and str(r.transformer) not in transformers and not frappe.db.exists("Transformer", f"{batch.society}-{r.transformer}"):
			err("Connections", r, _("Transformer {0} is neither in the Network sheet nor in the system.").format(r.transformer))
		if r.connection_date and getdate(r.connection_date) > as_of:
			err("Connections", r, _("Connected on {0}, after the cut-over day.").format(r.connection_date))
		if not r.plot_area:
			err("Connections", r, _("Plot size missing."), severity="Warning")
		accounts[ref] = r
	for cnic, names in cnic_names.items():
		if len(names) > 1:
			problems.append(dict(sheet="Connections", row_no=0, account="", severity="Warning",
			                     problem=_("CNIC {0} belongs to different names: {1}. They become one consumer.").format(cnic, ", ".join(sorted(names)))))
	by_meter = {str(r.meter_no): ref for ref, r in accounts.items() if r.meter_no}
	# readings
	for r in data["Readings"]:
		ref = str(r.old_account_no or "") or by_meter.get(str(r.meter_no or ""))
		a = accounts.get(ref)
		if not a:
			err("Readings", r, _("No account in the Connections sheet for this reading."), account=ref or str(r.meter_no or ""))
			continue
		if flt(r.last_reading) < 0 or flt(r.last_export_reading) < 0:
			err("Readings", r, _("A reading can't be negative."), account=ref)
		if r.reading_date and getdate(r.reading_date) > as_of:
			err("Readings", r, _("Read on {0}, after the cut-over day.").format(r.reading_date), account=ref)
		if a.meter_type == "Net Metering" and r.last_export_reading in (None, ""):
			err("Readings", r, _("A net-metering meter needs its last export reading."), account=ref)
		if a.meter_type != "Net Metering" and flt(r.last_export_reading):
			err("Readings", r, _("An export reading on a meter that isn't net metering; it is left out."), account=ref, severity="Warning")
		a.reading = r
	for ref, a in accounts.items():
		if a.metered and not a.get("reading"):
			err("Readings", None, _("No last reading: the meter will start from 0."), account=ref, severity="Warning")
	# balances
	balances = {}
	for r in data["Balances"]:
		ref = str(r.old_account_no or "")
		if ref not in accounts:
			err("Balances", r, _("No account in the Connections sheet for this balance."), account=ref)
			continue
		owes = flt(r.current_bill) + flt(r.arrears) + flt(r.surcharge) + flt(r.fees)
		if owes > 0 and flt(r.credit) > 0:
			err("Balances", r, _("An account either owes or is in credit, not both."), account=ref)
		if ref in balances:
			err("Balances", r, _("Two balance rows for the same account."), account=ref)
		balances[ref] = r
	# history
	history, seen = [], set()
	for r in data["History"]:
		ref = str(r.old_account_no or "")
		if ref not in accounts:
			err("History", r, _("No account in the Connections sheet for this month."), account=ref)
			continue
		if not r.month:
			err("History", r, _("The month is missing."), account=ref)
			continue
		r.month = getdate(r.month).replace(day=1)
		if r.month > as_of:
			err("History", r, _("Month {0} is after the cut-over day.").format(r.month.strftime("%b %Y")), account=ref)
		if (ref, r.month) in seen:
			err("History", r, _("{0} is in the history twice.").format(r.month.strftime("%b %Y")), account=ref)
		seen.add((ref, r.month))
		history.append(r)
	receivable = sum(flt(b.current_bill) + flt(b.arrears) + flt(b.surcharge) + flt(b.fees) - flt(b.credit) for b in balances.values())
	return frappe._dict(prof=prof, problems=problems, network=data["Network"], accounts=accounts, balances=balances, history=history,
	                    receivable=round(receivable, 2))


def _store_results(batch, a):
	batch.set("problems", [dict(p) for p in a.problems][:2000])
	batch.error_count = sum(1 for p in a.problems if p["severity"] == "Error")
	batch.warning_count = sum(1 for p in a.problems if p["severity"] == "Warning")
	batch.account_count, batch.receivable_total = len(a.accounts), a.receivable
	batch.accounts_difference = (batch.account_count - cint(batch.control_accounts)) if cint(batch.control_accounts) else 0
	batch.receivable_difference = round(a.receivable - flt(batch.control_receivable), 2) if flt(batch.control_receivable) else 0
	by = defaultdict(lambda: {"accounts": 0, "receivable": 0.0})
	for ref, r in a.accounts.items():
		b = a.balances.get(ref)
		k = (r.block or "", r.category or "")
		by[k]["accounts"] += 1
		if b:
			by[k]["receivable"] += flt(b.current_bill) + flt(b.arrears) + flt(b.surcharge) + flt(b.fees) - flt(b.credit)
	batch.reconciliation = json.dumps([{"block": k[0], "category": k[1], **v} for k, v in sorted(by.items())])


# ---------------------------------------------------------------- load
def _record(batch, doc):
	batch.append("records", {"record_type": doc.doctype, "record_name": doc.name})


def _consumer(batch, r):
	if r.old_customer_id:
		name = frappe.db.get_value("Consumer", {"legacy_customer_id": str(r.old_customer_id)})
		if name:
			return name
	if r.cnic:
		name = frappe.db.get_value("Consumer", {"id_type": "CNIC", "id_number": r.cnic})
		if name:
			return name
	c = frappe.get_doc({"doctype": "Consumer", "consumer_name": r.consumer_name, "consumer_type": "Individual",
	                    "legacy_customer_id": str(r.old_customer_id) if r.old_customer_id else None, "mobile_no": r.mobile or None,
	                    "address": r.address or None, "id_type": "CNIC" if r.cnic else None, "id_number": r.cnic or None,
	                    "tax_no": r.ntn or None, "tax_status": r.filer or None}).insert(ignore_permissions=True)
	_record(batch, c)
	return c.name


def _property(batch, r):
	no = property_no(r.house, r.block)
	name = f"{batch.society}-{no}"
	if frappe.db.exists("Property", name):
		return name
	p = frappe.get_doc({"doctype": "Property", "society": batch.society, "property_no": no, "block": r.block or None, "sector": r.sector or None,
	                    "property_type": r.property_type, "status": "Occupied", "area": flt(r.plot_area) or None, "area_unit": r.plot_unit}).insert(ignore_permissions=True)
	_record(batch, p)
	return p.name


def check(batch) -> int:
	"""The dry run: problems, counts and reconciliation on the batch; nothing else written. Returns the number of errors."""
	_store_results(batch, analyse(batch))
	batch.load_status = "Checked"
	return batch.error_count


def load(batch) -> bool:
	"""Check again and, with no errors, create everything. False (with the problems on the batch) if there are errors."""
	if batch.load_status == "Loaded":
		frappe.throw(_("This batch is already loaded."))
	a = analyse(batch)
	_store_results(batch, a)
	if batch.error_count:
		batch.load_status = "Checked"
		return False
	frappe.flags.in_migration = True
	try:
		for r in a.network:  # feeders first, then their transformers
			if r.kind == "Feeder" and not frappe.db.exists("Feeder", f"{batch.society}-{r.code}"):
				_record(batch, frappe.get_doc({"doctype": "Feeder", "society": batch.society, "feeder_code": str(r.code),
				                               "feeder_name": r.name or str(r.code)}).insert(ignore_permissions=True))
		for r in a.network:
			if r.kind == "Transformer" and not frappe.db.exists("Transformer", f"{batch.society}-{r.code}"):
				_record(batch, frappe.get_doc({"doctype": "Transformer", "society": batch.society, "transformer_code": str(r.code),
				                               "feeder": f"{batch.society}-{r.feeder}", "kva_rating": flt(r.kva) or None}).insert(ignore_permissions=True))
		made = {}
		for ref, r in a.accounts.items():
			reading = r.get("reading") or frappe._dict()
			meters = []
			if r.metered:
				m = frappe.get_doc({"doctype": "Meter", "serial_no": str(r.meter_no), "society": batch.society, "utility": r.utility,
				                    "meter_type": r.meter_type, "make": r.meter_make or None, "status": "In Store",
				                    "multiplier": flt(r.multiplier) or 1, "digits": cint(r.digits) or 5,
				                    "registers": 2 if r.meter_type == "Net Metering" else 1}).insert(ignore_permissions=True)
				_record(batch, m)
				meters = [{"meter": m.name, "installed_on": reading.reading_date or r.connection_date or batch.as_of_date,
				           "start_reading": flt(reading.last_reading),
				           "start_export_reading": flt(reading.last_export_reading) if r.meter_type == "Net Metering" else 0}]
			c = frappe.get_doc({"doctype": "Connection", "society": batch.society, "property": _property(batch, r), "consumer": _consumer(batch, r),
			                    "utility": r.utility, "consumer_category": r.category, "status": r.status, "connection_date": r.connection_date,
			                    "sanctioned_load_kw": flt(r.sanctioned_load_kw) or None, "phase": r.phase,
			                    "feeder": f"{batch.society}-{r.feeder}" if r.feeder else None,
			                    "transformer": f"{batch.society}-{r.transformer}" if r.transformer else None, "route": r.route or None,
			                    "legacy_account_no": ref, "legacy_collection_no": str(r.old_collection_no) if r.old_collection_no else None,
			                    "meters": meters}).insert(ignore_permissions=True)
			_record(batch, c)
			made[ref] = c.name
		for h in a.history:
			lb = frappe.get_doc({"doctype": "Legacy Bill", "connection": made[str(h.old_account_no)], "billing_month": h.month, "units": flt(h.units),
			                     "current_amount": flt(h.current_amount), "arrears": flt(h.arrears), "adjustment": flt(h.adjustment),
			                     "surcharge": flt(h.surcharge), "bill_amount": flt(h.bill_amount), "paid_on": h.paid_on, "paid_amount": flt(h.paid_amount),
			                     "balance": flt(h.balance), "migration_batch": batch.name}).insert(ignore_permissions=True)
			_record(batch, lb)
		if a.balances:
			rows = [{"reference_no": made[ref], "current_bill": flt(b.current_bill), "arrears": flt(b.arrears), "surcharge": flt(b.surcharge),
			         "fees": flt(b.fees), "credit": flt(b.credit), "security_deposit": flt(b.security_deposit), "plan_remaining": flt(b.plan_remaining),
			         "plan_installments": cint(b.plan_installments), "solar_credit_units": flt(b.solar_credit_units)} for ref, b in a.balances.items()]
			imp = frappe.get_doc({"doctype": "Opening Balance Import", "society": batch.society, "as_of_date": batch.as_of_date, "rows": rows}).insert(ignore_permissions=True)
			batch.opening_import = imp.name
			_record(batch, imp)
	finally:
		frappe.flags.in_migration = False
	batch.load_status = "Loaded"
	return True


# ---------------------------------------------------------------- undo
UNDO_ORDER = ("Opening Balance Import", "Legacy Bill", "Connection", "Meter", "Consumer", "Property", "Transformer", "Feeder")


def undo(batch):
	"""Remove what the batch created, as long as nothing has happened on those accounts since."""
	made = defaultdict(list)
	for r in batch.records:
		made[r.record_type].append(r.record_name)
	conns = made.get("Connection") or [""]
	for dt in ("Meter Reading", "Utility Bill", "Payment Receipt", "Customer Ledger Entry", "Service Request"):
		used = frappe.db.get_value(dt, {"connection": ["in", conns]})
		if used:
			frappe.throw(_("The loaded accounts already have a {0} ({1}): the batch can't be undone.").format(_(dt), used))
	if batch.opening_import and frappe.db.get_value("Opening Balance Import", batch.opening_import, "docstatus") != 0:
		frappe.throw(_("The opening balances {0} are posted: the batch can't be undone.").format(batch.opening_import))
	frappe.flags.in_migration_undo = True
	try:
		for dt in UNDO_ORDER:
			for name in reversed(made.get(dt, [])):
				if frappe.db.exists(dt, name):
					frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	finally:
		frappe.flags.in_migration_undo = False
	batch.set("records", [])
	batch.opening_import = None
	batch.load_status = "Undone"


# ---------------------------------------------------------------- the template workbook
def template_workbook() -> bytes:
	from openpyxl import Workbook  # noqa: PLC0415
	from openpyxl.styles import Font  # noqa: PLC0415

	wb = Workbook()
	wb.remove(wb.active)
	examples = {
		"Connections": ["7402006006", "PV-021177", "0000054902", "Pervaiz Hussain", "35202-1234567-1", "", "Filer", "03004917056",
		                "2413 Tulip Overseas Block", "2413", "Tulip Overseas", "", 5, "Marla", "House", "Electricity", "Residential", 5, "Single",
		                "2025-04-01", "Active", "173753", "", "Single Phase", 1, 5, "", "", ""],
		"Readings": ["7402006006", "173753", 1677, "", "2026-10-25"],
		"Balances": ["7402006006", 5355, 0, 0, 0, 0, 0, 0, 0, 0],
		"History": ["7402006006", "2026-09-01", 63, 3130, 0, 0, 0, 3130, "2026-10-10", 2570, 560],
		"Network": ["Feeder", "F1", "Feeder 1", "", ""],
		"Old Bills": ["7402006006", 70, 3400, 560, 0, 3960, 3960],
		"Payments": ["7402006006", "2026-11-12", 3960, "TXN-001", "Bank Transfer"],
	}
	for sheet, cols in TARGETS.items():
		ws = wb.create_sheet(sheet)
		ws.append(cols)
		for c in ws[1]:
			c.font = Font(bold=True)
		ws.append(examples[sheet])
	out = io.BytesIO()
	wb.save(out)
	return out.getvalue()


# ---------------------------------------------------------------- the parallel run
COMPONENTS = (("current", "current bill"), ("arrears", "arrears"), ("surcharge", "surcharge"))


def _parallel_sheets(doc, sheets):
	if not doc.workbook:
		frappe.throw(_("Attach the workbook first."))
	prof = profile_of(doc.profile)
	book, problems = read_workbook(doc.workbook), []
	missing = [s for s in sheets if s not in book]
	if missing:
		frappe.throw(_("The workbook has no {0} sheet.").format(_(" or ").join(missing)))
	data = {s: normalise(s, book[s], prof, problems) for s in sheets}
	bad = [p for p in problems if p["severity"] == "Error"]
	if bad:
		frappe.throw("<br>".join(_("{0} row {1}: {2}").format(p["sheet"], p["row_no"], p["problem"]) for p in bad[:20]))
	return prof, data


def _account(doc, ref):
	from utility_billing.utility_billing import account  # noqa: PLC0415

	conn = account.connection_of(str(ref or "").strip()) if ref else None
	return conn if conn and frappe.db.get_value("Connection", conn, "society") == doc.society else None


def load_payments(doc) -> dict:
	"""Record the payments the previous system took this month, once each: a payment already recorded (same reference, or with no
	reference the same account, day and amount) is skipped, so loading the same sheet twice is harmless."""
	prof, data = _parallel_sheets(doc, ("Payments",))
	have = {(p.reference or "", p.old_account_no, str(p.paid_on), flt(p.amount)) for p in doc.payments}
	for r in data["Payments"]:
		ref, amount, paid_on = str(r.reference or "").strip(), flt(r.amount), r.paid_on or doc.billing_month
		key = (ref, str(r.old_account_no or ""), str(getdate(paid_on)), amount)
		if key in have:
			continue
		have.add(key)
		row = {"old_account_no": str(r.old_account_no or ""), "paid_on": paid_on, "amount": amount, "reference": ref}
		conn = _account(doc, r.old_account_no)
		if amount <= 0:
			row["result"] = "Not a payment"
		elif not conn:
			row["result"] = "Unknown account"
		else:
			filters = {"connection": conn, "docstatus": ["<", 2]}
			filters.update({"channel_txn_id": ref} if ref else {"payment_date": paid_on, "amount": amount})
			existing = frappe.db.get_value("Payment Receipt", filters)
			if existing:
				row.update(result="Already recorded", receipt=existing)
			else:
				mode = mapped(prof, "mode", r.mode) or "Bank Transfer"
				rec = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": frappe.db.get_value("Connection", conn, "reference_no"),
				                      "payment_channel": "Bank / Channel", "payment_date": paid_on, "amount": amount,
				                      "mode": mode if mode in ("Cash", "Cheque", "Pay Order", "Card", "Bank Transfer", "1Link", "Kuickpay") else "Bank Transfer",
				                      "channel_txn_id": ref or None, "remarks": _("Taken by the previous system; parallel run {0}").format(doc.name)})
				rec.insert(ignore_permissions=True)
				rec.submit()
				row.update(result="Recorded", receipt=rec.name)
		doc.append("payments", row)
	recorded = [p for p in doc.payments if p.result == "Recorded"]
	doc.payments_recorded, doc.payments_amount = len(recorded), sum(flt(p.amount) for p in recorded)
	doc.payments_skipped = len(doc.payments) - len(recorded)
	return {"recorded": doc.payments_recorded, "skipped": doc.payments_skipped}


def compare(doc) -> dict:
	"""Account by account: their bill of the month against ours, with what explains each difference, and the closing balances."""
	if not doc.bill_run:
		frappe.throw(_("Choose this month's bill run first."))
	_prof, data = _parallel_sheets(doc, ("Old Bills",))
	tol = flt(doc.tolerance) if doc.tolerance not in (None, "") else 1
	month_end = frappe.utils.get_last_day(doc.billing_month)
	ours = {b.connection: b for b in frappe.get_all("Utility Bill", filters={"bill_run": doc.bill_run, "docstatus": ["<", 2]},
	                                                 fields=["name", "connection", "units", "current_amount", "arrears", "surcharge_amount",
	                                                         "payable_within_due", "superseded_by"]) if not b.superseded_by}
	doc.set("rows", [])
	seen = set()
	for r in data["Old Bills"]:
		conn = _account(doc, r.old_account_no)
		row = frappe._dict(old_account_no=str(r.old_account_no or ""), connection=conn, old_units=flt(r.units), old_current=flt(r.current_bill),
		                   old_arrears=flt(r.arrears), old_surcharge=flt(r.surcharge),
		                   old_payable=flt(r.payable) or flt(r.current_bill) + flt(r.arrears) + flt(r.surcharge), old_balance=flt(r.balance))
		b = ours.get(conn) if conn else None
		if conn:
			seen.add(conn)
			row.our_balance = account_balance(conn, month_end)
		if not b:
			row.result, row.difference = "Not in our run", row.old_payable
			row.explanation = _("No account here with this number.") if not conn else _("Not billed in our run (excluded, or no reading?).")
		else:
			row.update(bill=b.name, our_units=flt(b.units), our_current=flt(b.current_amount), our_arrears=flt(b.arrears),
			           our_surcharge=flt(b.surcharge_amount), our_payable=flt(b.payable_within_due))
			row.difference = round(row.our_payable - row.old_payable, 2)
			why = []
			if abs(row.our_units - row.old_units) > 0.001 and r.units not in (None, ""):
				why.append(_("units: theirs {0}, ours {1} (check the reading)").format(frappe.utils.fmt_money(row.old_units, 0),
				                                                                       frappe.utils.fmt_money(row.our_units, 0)))
			for f, label in COMPONENTS:
				d = round(row[f"our_{f}"] - row[f"old_{f}"], 2)
				if abs(d) > tol:
					why.append(_("{0}: ours is {1} {2}").format(_(label), frappe.utils.fmt_money(abs(d)), _("more") if d > 0 else _("less")))
			if r.balance not in (None, "") and abs(flt(row.our_balance) - row.old_balance) > tol:
				why.append(_("closing balance: theirs {0}, ours {1}").format(frappe.utils.fmt_money(row.old_balance), frappe.utils.fmt_money(row.our_balance)))
			row.result = "Different" if abs(row.difference) > tol or why else "Match"
			row.explanation = "; ".join(why)
		doc.append("rows", row)
	for conn, b in ours.items():
		if conn not in seen:
			doc.append("rows", {"connection": conn, "bill": b.name, "result": "Not in their bills", "our_units": flt(b.units),
			                    "our_current": flt(b.current_amount), "our_arrears": flt(b.arrears), "our_surcharge": flt(b.surcharge_amount),
			                    "our_payable": flt(b.payable_within_due), "difference": flt(b.payable_within_due),
			                    "explanation": _("Billed here, but the previous system has no bill for it.")})
	count = Counter(r.result for r in doc.rows)
	doc.accounts_compared = len(doc.rows)
	doc.accounts_matched, doc.accounts_different = count["Match"], count["Different"]
	doc.missing_here, doc.missing_there = count["Not in our run"], count["Not in their bills"]
	doc.total_difference = round(sum(flt(r.difference) for r in doc.rows), 2)
	doc.comparison_status = "Clean" if doc.rows and doc.accounts_matched == len(doc.rows) else "Compared"
	return {"compared": doc.accounts_compared, "matched": doc.accounts_matched, "different": len(doc.rows) - doc.accounts_matched}


def account_balance(connection, date) -> float:
	from utility_billing.utility_billing import account  # noqa: PLC0415

	return round(account.balance_as_of(connection, date), 2)
