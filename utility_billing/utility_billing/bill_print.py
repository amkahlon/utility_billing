# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill print (FS-BIL-08, 16, 17; FS-RUN-08, 09; FS-CLT-03; FS-CFG-19).

Every bill carries a print snapshot: the society's branding, notices and bank details, the consumer
and connection details, the meter reading and the bill history, as they were when the bill was
calculated and again when it was posted. Printing reads only the snapshot and the bill's own stored
lines and totals, so a past bill reprints exactly as issued even after the society changes its logo,
notices or bank accounts, or the consumer's details change (FS-RUN-09).
"""

import base64
import json
import mimetypes
import re
import urllib.parse

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate, getdate, now_datetime

SNAPSHOT_VERSION = 2  # 2: barcode, QR content and second language (bills with version 1 print as before)
DEFAULT_HISTORY_MONTHS = 12
MAX_HISTORY_MONTHS = 24
MAX_BILLS_PER_PDF = 500  # bulk print: larger runs are printed block by block or route by route
PDF_OPTIONS = {"page-size": "A4", "margin-top": "6mm", "margin-bottom": "6mm", "margin-left": "8mm",
               "margin-right": "8mm", "encoding": "UTF-8"}
MEASURED = ("Per Unit", "Per Area")  # lines printed as quantity x rate
TITLES = {"Electricity": "Electricity Bill", "Water": "Water Bill", "Gas": "Gas Bill"}


# ---------------------------------------------------------------- snapshot
def history_months(society) -> int:
	months = cint(society.get("bill_history_months"))
	return min(months, MAX_HISTORY_MONTHS) if months > 0 else DEFAULT_HISTORY_MONTHS


def bill_history(connection, month, months, until=None):
	"""The connection's earlier posted bills, newest first, each with what was paid against it: the
	payments received from its issue date up to the next bill's issue date (the last one up to
	`until`, this bill's issue date). Reversed payments don't count."""
	from utility_billing.utility_billing import runcache  # noqa: PLC0415

	# a bill run reads the history of a group of accounts together (the performance step); an account with fewer bills in the group's
	# window than asked for, and older ones behind it, is read on its own as before
	group = runcache.primed(connection)
	if group and (group.month != getdate(month) or group.months != months or (
			len(group.bills.get(connection, ())) < months and connection in group.older)):
		group = None
	if group:
		rows = group.bills.get(connection, [])[:months]
		payments = group.payments.get(connection, []) if rows else []
	else:
		rows = frappe.get_all(
			"Utility Bill",
			filters={"connection": connection, "docstatus": 1, "billing_month": ["<", getdate(month)]},
			fields=["billing_month", "issue_date", "units", "current_amount", "payable_within_due"],
			order_by="billing_month desc", limit=months,
		)
		payments = frappe.get_all(
			"Payment Receipt", filters={"connection": connection, "docstatus": 1},
			fields=["payment_date", "amount"], order_by="payment_date",
		) if rows else []
	out, end = [], getdate(until) if until else None
	for r in rows:
		start = getdate(r.issue_date)
		paid = [p for p in payments if getdate(p.payment_date) >= start and (end is None or getdate(p.payment_date) < end)]
		out.append({"month": str(r.billing_month), "units": flt(r.units), "billed": flt(r.payable_within_due),
		            "current": flt(r.current_amount),
		            "paid_on": str(max(p.payment_date for p in paid)) if paid else None,
		            "paid": sum(flt(p.amount) for p in paid) if paid else None})
		end = start
	if len(out) < months:  # the months before the first bill here, as the previous system billed them (migration, increment 29)
		before = getdate(out[-1]["month"]) if out else getdate(month)
		legacy = [r for r in group.legacy.get(connection, []) if getdate(r.billing_month) < before][:months - len(out)] if group else frappe.get_all(
			"Legacy Bill", filters={"connection": connection, "billing_month": ["<", before]},
			fields=["billing_month", "units", "current_amount", "arrears", "bill_amount", "paid_on", "paid_amount"],
			order_by="billing_month desc", limit=months - len(out))
		for r in legacy:
			out.append({"month": str(r.billing_month), "units": flt(r.units),
			            "billed": flt(r.bill_amount) or flt(r.current_amount) + flt(r.arrears), "current": flt(r.current_amount),
			            "paid_on": str(r.paid_on) if r.paid_on else None, "paid": flt(r.paid_amount) if r.paid_on or flt(r.paid_amount) else None,
			            "legacy": 1})
	return out


def build_snapshot(bill) -> dict:
	"""Snapshot for a bill (a Utility Bill document or the dict build_bill returns)."""
	g = bill.get
	society = frappe.get_cached_doc("Society", g("society"))
	from utility_billing.utility_billing import runcache  # noqa: PLC0415

	group = runcache.primed(g("connection"))  # a bill run reads its accounts and their consumers a group at a time (the performance step)
	conn = group.connections[g("connection")] if group else frappe.get_cached_doc("Connection", g("connection"))

	prop_fields = ["property_no", "block", "sector", "area", "area_unit"]
	props = runcache.bulk(("snapshot properties", g("society")), lambda: {  # a bill run reads them once (FS-RUN-07)
		p.name: p for p in frappe.get_all("Property", filters={"society": g("society")}, fields=["name", *prop_fields])})
	prop = (props.get(conn.property) if props is not None else None) or frappe.db.get_value("Property", conn.property, prop_fields,
	                                                                                        as_dict=True) or {}
	# the bill's own consumer: on a final bill the outgoing holder, not the account's holder now (increment 22)
	consumer = (group.consumers.get(g("consumer") or conn.consumer) if group else None) or frappe.db.get_value(
		"Consumer", g("consumer") or conn.consumer, ["consumer_name", "mobile_no", "address", "id_number", "tax_no"], as_dict=True) or {}
	reading_fields = ["meter", "previous_reading", "previous_reading_date", "present_reading", "reading_date", "multiplier",
	                  "consumption_type", "status", "photo", "net_metering", "previous_export_reading", "present_export_reading", "export_units"]
	cycle = runcache.tariff(("cycle", g("bill_run")), lambda: frappe.db.get_value("Bill Run", g("bill_run"), "reading_cycle")) \
		if g("bill_run") and runcache.active() else None
	run_readings = runcache.bulk(("snapshot readings", cycle), lambda: {
		r.name: r for r in frappe.get_all("Meter Reading", filters={"reading_cycle": cycle}, fields=["name", *reading_fields])}) if cycle else None
	reading = None
	if g("meter_reading"):
		reading = (run_readings or {}).get(g("meter_reading")) or frappe.db.get_value("Meter Reading", g("meter_reading"), reading_fields,
		                                                                              as_dict=True)
	category = frappe.get_cached_value("Consumer Category", g("consumer_category"), "category_name") if g("consumer_category") else ""
	return {
		"version": SNAPSHOT_VERSION,
		"captured_on": str(now_datetime()),
		"society": society_snapshot(society, g("issue_date")),
		"consumer": {"name": consumer.get("consumer_name") or g("consumer_name"), "mobile": consumer.get("mobile_no"),
		             "address": consumer.get("address"), "id_number": consumer.get("id_number"), "tax_no": consumer.get("tax_no")},
		"connection": {
			"reference_no": conn.reference_no, "property_no": prop.get("property_no"), "block": prop.get("block"),
			"sector": prop.get("sector"), "area": flt(prop.get("area")), "area_unit": prop.get("area_unit"),
			"category": category, "sanctioned_load_kw": flt(conn.sanctioned_load_kw), "phase": conn.phase,
			"route": conn.route,
			# the previous system's account number, printed while the society still accepts old numbers on the issue date (increment 29)
			"legacy_account_no": conn.get("legacy_account_no") if conn.get("legacy_account_no") and legacy_accepted(g("society"), g("issue_date")) else None,
		},
		"reading": dict(reading, previous_reading_date=str(reading.previous_reading_date or ""),
		                reading_date=str(reading.reading_date or "")) if reading else None,
		"history": bill_history(g("connection"), g("billing_month"), history_months(society), g("issue_date")),
		# the society's approved Bill Template in force on the issue date (increment 27); none: the fixed layout
		"template": template_for(g),
		# the month's solar rebate notice, as approved with the pool (increment 28)
		"solar_notice": frappe.db.get_value("Solar Rebate Pool", g("solar_pool"), "notice") if g("solar_pool") else None,
	}


def legacy_accepted(society, on):
	from utility_billing.utility_billing import legacy  # noqa: PLC0415

	return legacy.accepted(society, on)


def template_for(g):
	"""The approved Bill Template a bill prints with (increment 27), looked up once per society, utility, category and date in a run."""
	from utility_billing.utility_billing import bill_designer, runcache  # noqa: PLC0415

	key = (g("society"), g("utility"), g("consumer_category"), str(g("issue_date")))
	find = lambda: bill_designer.choose_template(*key)  # noqa: E731
	return runcache.tariff(("bill template", *key), find) if runcache.active() else find()


def society_snapshot(society, on=None) -> dict:
	"""The society part of a print snapshot: branding in force on the issue date (FS-CLT-09), notices, banks and print switches."""
	from utility_billing.utility_billing import branding  # noqa: PLC0415

	brand = branding.effective(society, on)
	return {
		"name": society.society_name, "short_name": society.short_name, "address": society.address,
		"phone": society.phone, "email": society.email, "website": society.website,
		"office_hours": society.office_hours, "ntn": society.ntn, "strn": society.strn_vat,
		"logo": brand["logo"], "colour": brand["colour"],
		"letterhead": brand["letterhead"], "footer": brand["footer"],
		"signatory": branding.signatory(society, "Bill", on),  # FS-CLT-07
		"notices": [n.strip() for n in (society.bill_notices or "").splitlines() if n.strip()],
		"payment_instructions": society.payment_instructions,
		"banks": [{"bank": b.bank_name, "title": b.account_title, "account": b.account_number, "iban": b.iban,
		           "channel": b.biller_channel, "biller_code": b.biller_code}
		          for b in society.bank_accounts if b.print_on_bill],
		"office_copy": bool(cint(society.print_office_copy)),
		"show_qr": bool(cint(society.show_qr_code)),
		# increment 21 (gap 12, FS-LOC): barcode and QR each on or off, what the QR holds, a second language
		"show_barcode": bool(cint(society.get("show_barcode"))),
		"show_meter_photo": bool(cint(society.get("show_meter_photo"))),  # user's request 2026-11-16
		"qr_content": society.get("qr_content") or "Reference number",
		"qr_link_template": society.get("qr_link_template"),
		"second_language": society.get("second_language") or None,
		"notices_second": [n.strip() for n in (society.get("bill_notices_second") or "").splitlines() if n.strip()],
		"payment_instructions_second": society.get("payment_instructions_second"),
	}


def snapshot_of(bill) -> dict:
	if bill.get("print_snapshot"):
		snap = bill.print_snapshot
		return json.loads(snap) if isinstance(snap, str) else snap
	return build_snapshot(bill)  # bills calculated before increment 6


# ---------------------------------------------------------------- rendering helpers
def money(v):
	v = flt(v)
	text = f"{abs(v):,.0f}"
	return f"-{text}" if v < 0 else text


def qty(v):
	v = flt(v)
	return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"


def rate(v):
	"""Whole rates as they are (70); others with at least 2 decimals (1.30, 1.305)."""
	v = flt(v)
	if not v:
		return ""
	if v == int(v):
		return f"{v:,.0f}"
	text = f"{v:,.4f}".rstrip("0")
	return text if len(text.split(".")[1]) >= 2 else text + "0"


def date(v):
	return formatdate(v) if v else ""


def month_name(v, short=False):
	return getdate(v).strftime("%b %Y" if short else "%B %Y") if v else ""


def qr_data_uri(text: str) -> str:
	import pyqrcode

	return "data:image/png;base64," + pyqrcode.create(text, error="M").png_as_base64_str(scale=4, quiet_zone=2)


def logo_data_uri(file_url):
	"""Embed the logo, so the PDF engine never has to fetch it over HTTP."""
	if not file_url:
		return None
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		return None
	try:
		content = frappe.get_doc("File", name).get_content()
	except Exception:
		return None
	if isinstance(content, str):
		content = content.encode()
	mime = mimetypes.guess_type(file_url)[0] or "image/png"
	return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def meter_photo_of(bill, snap):
	"""The reading's photo, scaled down and embedded (a phone photo of several MB would swell every bill PDF). Bills issued before
	the photo was kept in the snapshot take it from their reading."""
	url = (snap.get("reading") or {}).get("photo")
	if not url and bill.get("meter_reading"):
		url = frappe.db.get_value("Meter Reading", bill.meter_reading, "photo")
	return image_data_uri(url, max_px=480) if url else None


def image_data_uri(file_url, max_px=480):
	"""An attached image as a small JPEG data URI, or None if it can't be read."""
	import io  # noqa: PLC0415

	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		return None
	try:
		content = frappe.get_doc("File", name).get_content()
		from PIL import Image, ImageOps  # noqa: PLC0415

		img = ImageOps.exif_transpose(Image.open(io.BytesIO(content if isinstance(content, bytes) else content.encode())))
		img.thumbnail((max_px, max_px))
		out = io.BytesIO()
		img.convert("RGB").save(out, "JPEG", quality=72, optimize=True)
	except Exception:  # noqa: BLE001 - a missing or broken photo leaves the bill without it
		return None
	return f"data:image/jpeg;base64,{base64.b64encode(out.getvalue()).decode()}"


def watermark_of(bill, duplicate):
	if bill.docstatus == 2:
		return _("CANCELLED")
	if bill.docstatus == 0:
		return _("DRAFT")
	return _("DUPLICATE") if duplicate else None


def qr_text(soc, bill, ref) -> str:
	"""What the QR code holds: the reference number, or the society's payment link with the bill's values filled in."""
	template = (soc.get("qr_link_template") or "").strip()
	if soc.get("qr_content") != "Payment link" or not template:
		return ref
	values = {"{reference_no}": ref, "{amount}": f"{flt(bill.payable_within_due):.0f}", "{due_date}": str(bill.due_date or ""), "{bill}": bill.name}
	for k, v in values.items():
		template = template.replace(k, urllib.parse.quote(str(v), safe=""))
	return template


def labeller(language):
	"""t("Due date") -> the English label, and the second language's beside it when the society prints one."""
	from markupsafe import Markup, escape  # noqa: PLC0415

	from utility_billing.utility_billing import bill_labels  # noqa: PLC0415

	code = bill_labels.LANG_CODE.get(language or "")

	def t(label):
		english = escape(_(label))
		other = bill_labels.second(label, language)
		if not other:
			return Markup(english)
		# left-to-right marks around the right-to-left words keep what follows (a date in brackets) in its place (found in the PDF check)
		return Markup(f'{english} ‎<span class="l2" dir="rtl" lang="{code}">{escape(other)}</span>‎')

	return t


LATIN_RUN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.,:/+\-]*")


def rtl_text(text):
	"""Second-language text for the PDF: escaped, with each run of digits or Latin letters in a left-to-right span. wkhtmltopdf
	otherwise lays out a right-to-left line that mixes them too wide for its box (found in the PDF check, 2026-11-15)."""
	from markupsafe import Markup, escape  # noqa: PLC0415

	text, out, pos = str(text or ""), [], 0
	for m in LATIN_RUN.finditer(text):  # escape each piece on its own, so an entity such as &lt; is never split
		out.append(str(escape(text[pos:m.start()])))
		out.append(f'<span dir="ltr">{escape(m.group(0))}</span>')
		pos = m.end()
	out.append(str(escape(text[pos:])))
	return Markup("".join(out))


def bill_context(bill, duplicate=0) -> dict:
	snap = snapshot_of(bill)
	soc = snap["society"]
	lines = sorted(bill.lines, key=lambda l: (cint(l.line_order), l.idx))
	ref = snap["connection"].get("reference_no") or ""
	duplicate = cint(duplicate) if bill.docstatus == 1 else 0
	from utility_billing.utility_billing import barcode, bill_labels  # noqa: PLC0415

	language = soc.get("second_language")
	return {
		"bill": bill, "snap": snap, "soc": soc, "con": snap["connection"], "consumer": snap["consumer"],
		"reading": snap.get("reading"), "history": snap.get("history") or [],
		"lines": [{"label": l.label, "quantity": qty(l.quantity) if l.basis in MEASURED else "",
		           "rate": rate(l.rate) if l.basis in MEASURED else "", "amount": money(l.amount), "note": l.note,
		           "group": l.get("charge_group") or ""}
		          for l in lines],
		"title": _(TITLES.get(bill.utility, f"{bill.utility} Bill" if bill.utility else "Utility Bill")),
		"month": month_name(bill.billing_month), "issue_date": date(bill.issue_date), "due_date": date(bill.due_date),
		"logo": logo_data_uri(soc.get("logo")),
		"signatory": soc.get("signatory"), "signature": logo_data_uri((soc.get("signatory") or {}).get("signature")),
		"qr": qr_data_uri(qr_text(soc, bill, ref)) if soc.get("show_qr") and ref else None,
		"barcode": barcode.png_data_uri(ref) if soc.get("show_barcode") and ref else None,
		"meter_photo": meter_photo_of(bill, snap) if soc.get("show_meter_photo", True) else None,
		"t": labeller(language), "lang2": bill_labels.LANG_CODE.get(language or ""), "rtl": rtl_text,
		"notices_second": soc.get("notices_second") or [], "payment_instructions_second": soc.get("payment_instructions_second"),
		"watermark": watermark_of(bill, duplicate),
		"duplicate": duplicate,
		"money": money, "qty": qty, "date": date, "month_name": month_name,
	}


def bill_html(name, duplicate=0) -> str:
	"""HTML of one bill: in the Bill Template its snapshot names (increment 27), otherwise the fixed layout (consumer copy, and the
	office copy if the society prints one)."""
	bill = frappe.get_doc("Utility Bill", name)
	settings = template_of(bill)
	if settings:
		from utility_billing.utility_billing import bill_designer  # noqa: PLC0415

		return bill_designer.render_bill(bill, settings, duplicate)
	return frappe.render_template("utility_billing/templates/bill/utility_bill.html", bill_context(bill, duplicate))


def template_of(bill):
	"""The page settings and layout of the template a bill was issued with, or None for the fixed layout."""
	name = snapshot_of(bill).get("template")
	if not name or not frappe.db.exists("Bill Template", name):
		return None
	from utility_billing.utility_billing import bill_designer  # noqa: PLC0415

	return bill_designer.template_settings(name)


def utility_bill_html(name):
	"""Jinja method for the "Utility Bill" print format (the desk's Print and PDF)."""
	return bill_html(name)


def wrap_pages(bodies) -> str:
	pages = "".join(f'<div class="ub-page">{b}</div>' for b in bodies)
	return ('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Bills</title>'
	        "<style>.ub-page{page-break-after:always}.ub-page:last-child{page-break-after:auto}</style>"
	        f"</head><body>{pages}</body></html>")


def document_html(names, duplicate=0) -> str:
	return wrap_pages(bill_html(n, duplicate) for n in names)


def pdf_options_for(names) -> dict:
	"""Paper size and margins of the first bill's template (a run's bills share one; one PDF has one paper size)."""
	settings = template_of(frappe.get_doc("Utility Bill", names[0])) if names else None
	if settings:
		from utility_billing.utility_billing import bill_designer  # noqa: PLC0415

		return bill_designer.pdf_options(settings)
	return dict(PDF_OPTIONS)


def pdf_of(names, duplicate=0) -> bytes:
	from utility_billing import pdf as ubpdf  # noqa: PLC0415

	return ubpdf.render(document_html(names, duplicate), options=pdf_options_for(names))


def send_pdf(content: bytes, filename: str):
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = content
	frappe.local.response.type = "pdf"


# ---------------------------------------------------------------- endpoints
def readable_bill(name):
	bill = frappe.get_doc("Utility Bill", name)
	bill.check_permission("print")
	return bill


@frappe.whitelist()
def download_bill(name: str, duplicate: int = 0):
	"""PDF of one bill, for print, WhatsApp, email and the apps (FS-BIL-16). A reprint of a posted
	bill is identical to the original (FS-RUN-09); a duplicate is marked DUPLICATE (FS-BIL-17) and
	must have been issued first with issue_duplicate, which records it."""
	bill = readable_bill(name)
	duplicate = cint(duplicate)
	if duplicate:
		if bill.docstatus != 1:
			frappe.throw(_("Only a posted bill can have a duplicate."))
		if duplicate > cint(bill.duplicate_count):
			frappe.throw(_("Issue the duplicate first."))
	send_pdf(pdf_of([bill.name], duplicate), f"{bill.name}{'-duplicate' if duplicate else ''}.pdf")


@frappe.whitelist(methods=["POST"])
def issue_duplicate(name: str):
	"""Record a duplicate bill for a consumer who lost theirs (FS-BIL-17), and charge the society's
	duplicate bill fee to the account if it has one (FS-CFG-24)."""
	bill = readable_bill(name)
	if bill.docstatus != 1:
		frappe.throw(_("Only a posted bill can have a duplicate."))
	count = cint(bill.duplicate_count) + 1
	frappe.db.set_value("Utility Bill", bill.name, {"duplicate_count": count, "last_duplicate_on": now_datetime()},
	                    update_modified=False)
	fee = charge_duplicate_fee(bill)
	note = _(" Fee {0} charged to the account.").format(fee) if fee else ""
	bill.add_comment("Info", _("Duplicate bill {0} issued by {1}.").format(count, frappe.session.user) + note)
	return count


def charge_duplicate_fee(bill):
	"""The society's duplicate bill fee, if it set one (FS-BIL-17, FS-CFG-24)."""
	fee_type = frappe.db.get_value("Society", bill.society, "duplicate_bill_fee")
	if not fee_type:
		return None
	fee = frappe.get_doc({"doctype": "Fee Charge", "connection": bill.connection, "fee_type": fee_type,
	                      "source_type": "Utility Bill", "source_name": bill.name,
	                      "remarks": _("Duplicate of bill {0}").format(bill.name)})
	fee.flags.ignore_permissions = True
	fee.insert()
	fee.submit()
	return fee.name


def run_bills(run, block=None, route=None):
	"""A run's bills in delivery order: block, route, property number (FS-RUN-08)."""
	conditions, values = ["b.bill_run = %(run)s", "b.docstatus < 2"], {"run": run}
	if block:
		conditions.append("ifnull(p.block, '') = %(block)s")
		values["block"] = "" if block == "(none)" else block
	if route:
		conditions.append("ifnull(c.route, '') = %(route)s")
		values["route"] = "" if route == "(none)" else route
	return frappe.db.sql(
		f"""select b.name, ifnull(p.block, '') as block, ifnull(c.route, '') as route, p.property_no
		from `tabUtility Bill` b
		left join `tabProperty` p on p.name = b.property
		left join `tabConnection` c on c.name = b.connection
		where {' and '.join(conditions)}
		order by ifnull(p.block, ''), ifnull(c.route, ''), length(p.property_no), p.property_no, b.name""",
		values, as_dict=True,
	)


def readable_run(run):
	doc = frappe.get_doc("Bill Run", run)
	doc.check_permission("read")
	if not frappe.has_permission("Utility Bill", "print"):
		raise frappe.PermissionError(_("You can't print bills."))
	return doc


@frappe.whitelist()
def run_print_groups(run: str):
	"""Blocks and routes in a run, with bill counts, for the bulk print dialog."""
	readable_run(run)
	blocks, routes, pairs = {}, {}, {}
	for b in run_bills(run):
		block, route = b.block or "(none)", b.route or "(none)"
		blocks[block] = blocks.get(block, 0) + 1
		routes[route] = routes.get(route, 0) + 1
		pairs.setdefault(block, {})[route] = pairs.get(block, {}).get(route, 0) + 1
	# pairs: the routes of each block, so choosing a block offers only its routes (user's request 2026-11-16)
	return {"total": sum(blocks.values()), "blocks": blocks, "routes": routes, "pairs": pairs, "limit": MAX_BILLS_PER_PDF}


@frappe.whitelist()
def print_run_bills(run: str, block: str | None = None, route: str | None = None):
	"""One PDF of a run's bills in delivery order, optionally for one block or route (FS-RUN-08)."""
	doc = readable_run(run)
	bills = run_bills(doc.name, block or None, route or None)
	if not bills:
		frappe.throw(_("No bills to print."))
	if len(bills) > MAX_BILLS_PER_PDF:
		frappe.throw(_("{0} bills is too many for one PDF; print one block or route at a time (up to {1}).").format(
			len(bills), MAX_BILLS_PER_PDF))
	suffix = "".join(f"-{'no-' + kind if x == '(none)' else frappe.scrub(x)}"
	                 for kind, x in (("block", block), ("route", route)) if x)
	send_pdf(pdf_of([b.name for b in bills]), f"{doc.name}{suffix}.pdf")
