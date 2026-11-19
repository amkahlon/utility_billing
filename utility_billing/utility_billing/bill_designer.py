# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill Designer (increment 27): each society's bill format built from blocks, with a live preview.

A Bill Template's layout is sections -> rows -> cells -> blocks:

    {"sections": [{"label", "copy_label", "cut_line_before", "cut_text", "new_page",
                   "rows": [{"cells": [{"width": 58, "blocks": [{"type": "details", "options": {...}}]}]}]}]}

Every block type and its options are listed in BLOCKS; the Bill Designer page builds its options panel from that list, so a new
block needs no screen change. Layouts hold only choices and text, never HTML or template code: free text is escaped and its
placeholders are filled from FIELDS. A bill keeps the name of the template it was issued with in its print snapshot, and an
approved template can't change, so a reprint is exact (FS-RUN-09). Bills without a template print with the fixed layout."""

import copy
import functools
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from utility_billing.utility_billing import access

MAX_SECTIONS, MAX_ROWS, MAX_BLOCKS, MAX_TEXT = 6, 40, 120, 2000
PAPER_MM = {"A4": (210, 297), "A5": (148, 210), "Letter": (215.9, 279.4), "Legal": (215.9, 355.6)}
FONTS = {"Helvetica / Arial": '"Helvetica Neue", Arial, sans-serif', "Verdana": "Verdana, Geneva, sans-serif",
         "Times": '"Times New Roman", Times, serif', "DejaVu Sans": '"DejaVu Sans", sans-serif'}
STARTERS = {"standard": "Standard", "two_copies": "Two tear-off copies", "compact_grid": "Compact grid", "classic_boxed": "Classic boxed"}

# ---------------------------------------------------------------- fields (details grid, free-text placeholders)
# key: (label, how to read it from the print context). Everything comes from the bill and its print snapshot, never from today's
# records, so a reprint shows what was issued.
FIELDS = {
	"reference_no": ("Reference No", lambda c: c["con"].get("reference_no")),
	"bill_no": ("Bill No", lambda c: c["bill"].name),
	"old_account_no": ("Old account no", lambda c: c["con"].get("legacy_account_no")),
	"consumer_name": ("Name", lambda c: c["consumer"].get("name") or c["bill"].get("consumer_name")),
	"property": ("Property", lambda c: ", ".join(x for x in (c["con"].get("property_no"), c["con"].get("block"), c["con"].get("sector")) if x)),
	"property_no": ("House No", lambda c: c["con"].get("property_no")),
	"block": ("Block", lambda c: c["con"].get("block")),
	"sector": ("Sector", lambda c: c["con"].get("sector")),
	"address": ("Address", lambda c: c["consumer"].get("address")),
	"mobile": ("Mobile", lambda c: c["consumer"].get("mobile")),
	"cnic": ("CNIC", lambda c: c["consumer"].get("id_number")),
	"ntn": ("NTN", lambda c: c["consumer"].get("tax_no")),
	"tax_status": ("Tax status", lambda c: c["consumer"].get("tax_status")),
	"connection_date": ("Connection date", lambda c: c["date"](c["con"].get("connection_date")) if c["con"].get("connection_date") else ""),
	"category": ("Category", lambda c: c["con"].get("category")),
	"sanctioned_load": ("Sanctioned load", lambda c: f'{c["qty"](c["con"].get("sanctioned_load_kw"))} kW' if c["con"].get("sanctioned_load_kw") else ""),
	"phase": ("Phase", lambda c: c["con"].get("phase")),
	"meter_no": ("Meter No", lambda c: (c.get("reading") or {}).get("meter")),
	"route": ("Route", lambda c: c["con"].get("route")),
	"plot_size": ("Plot size", lambda c: f'{c["qty"](c["con"].get("area"))} {c["con"].get("area_unit") or ""}'.strip() if c["con"].get("area") else ""),
	"billing_month": ("Bill month", lambda c: c["month"]),
	"billing_month_short": ("Bill month", lambda c: c["month_name"](c["bill"].billing_month, True)),
	"issue_date": ("Issue date", lambda c: c["issue_date"]),
	"due_date": ("Due date", lambda c: c["due_date"]),
	"reading_date": ("Reading date", lambda c: c["date"]((c.get("reading") or {}).get("reading_date"))),
	"previous_reading": ("Previous reading", lambda c: c["qty"]((c.get("reading") or {}).get("previous_reading")) if c.get("reading") else ""),
	"present_reading": ("Present reading", lambda c: c["qty"]((c.get("reading") or {}).get("present_reading")) if c.get("reading") else ""),
	"units": ("Units", lambda c: c["qty"](c["bill"].units)),
	"imported_units": ("Imported units", lambda c: c["qty"](c["bill"].get("import_units")) if c["bill"].get("nm_method") else ""),
	"exported_units": ("Exported units", lambda c: c["qty"](c["bill"].get("export_units")) if c["bill"].get("nm_method") else ""),
	"credited_units": ("Units credited", lambda c: c["qty"](c["bill"].get("credited_units")) if c["bill"].get("nm_method") else ""),
	"solar_carried_forward": ("Solar units carried forward", lambda c: c["qty"](c["bill"].get("units_carried_out")) if c["bill"].get("nm_method") else ""),
	"solar_notice": ("Solar rebate notice", lambda c: c["snap"].get("solar_notice") or ""),
	"current_bill": ("Current bill", lambda c: c["money"](c["bill"].current_amount)),
	"arrears": ("Arrears", lambda c: c["money"](c["bill"].arrears)),
	"payable_within_due": ("Payable within due date", lambda c: c["money"](c["bill"].payable_within_due)),
	"surcharge": ("Late payment surcharge", lambda c: c["money"](c["bill"].surcharge_amount)),
	"payable_after_due": ("Payable after due date", lambda c: c["money"](c["bill"].payable_after_due)),
	"title": ("Title", lambda c: c["title"]),
	"society_name": ("Society", lambda c: c["soc"].get("name")),
	"society_ntn": ("Society NTN", lambda c: c["soc"].get("ntn")),
	"society_strn": ("Society STRN", lambda c: c["soc"].get("strn")),
}
AMOUNT_FIELDS = {"current_bill", "arrears", "payable_within_due", "surcharge", "payable_after_due"}


def _opt(key, label, kind, default=None, **more):
	return {"key": key, "label": label, "type": kind, "default": default, **more}


ALIGN = ["Left", "Center", "Right"]
BLOCKS = {
	"header": {"label": "Header", "description": "Logo, society name, letterhead, title and the copy label.", "options": [
		_opt("logo", "Logo", "select", "Left", choices=["Left", "Right", "None"]),
		_opt("second_image", "Second image (e.g. operator or FBR logo)", "image", ""),
		_opt("second_image_position", "Second image at", "select", "Right", choices=["Left", "Right"]),
		_opt("align", "Text alignment", "select", "Left", choices=ALIGN),
		_opt("show_name", "Society name", "check", 1),
		_opt("show_letterhead", "Letterhead lines", "check", 1),
		_opt("show_tax_ids", "Society NTN and STRN", "check", 1),
		_opt("title", "Title", "text", "{title}"),
		_opt("show_copy_label", "Copy label (from the section)", "check", 1),
	]},
	"title_band": {"label": "Title band", "description": "A coloured band with the title, month and bill number.", "options": [
		_opt("text", "Text", "text", "{title} — {billing_month}"),
		_opt("show_bill_no", "Bill number", "check", 1),
		_opt("filled", "Accent background", "check", 1),
	]},
	"copy_label": {"label": "Copy label", "description": "The section's copy label in a box, such as Customer Copy or Bank Copy.", "options": [
		_opt("align", "Alignment", "select", "Right", choices=ALIGN),
		_opt("boxed", "Boxed", "check", 1),
	]},
	"details": {"label": "Details grid", "description": "Any bill, consumer, connection or amount fields as label and value pairs.", "options": [
		_opt("heading", "Heading", "text", ""),
		_opt("fields", "Fields", "fields", [{"key": "consumer_name"}, {"key": "property"}, {"key": "category"}, {"key": "meter_no"}, {"key": "mobile"}]),
		_opt("columns", "Pairs per row", "int", 1, min=1, max=4),
		_opt("boxed", "Boxed", "check", 1),
		_opt("bold_values", "Bold values", "check", 0),
	]},
	"reference": {"label": "Reference and barcode", "description": "The consumer reference number, its barcode and the key dates.", "options": [
		_opt("large", "Large number", "check", 1),
		_opt("barcode", "Barcode", "check", 1),
		_opt("dates", "Bill month, issue and due date", "check", 1),
		_opt("boxed", "Boxed", "check", 1),
	]},
	"qr": {"label": "QR code", "description": "The reference number or the society's payment link, as the society chose.", "options": [
		_opt("size", "Size (mm)", "int", 23, min=12, max=45),
		_opt("caption", "Caption", "text", ""),
		_opt("align", "Alignment", "select", "Center", choices=ALIGN),
	]},
	"reading": {"label": "Meter reading", "description": "Previous and present readings and units.", "options": [
		_opt("style", "Style", "select", "Table", choices=["Table", "List"]),
		_opt("columns", "Show", "multi", ["previous", "present", "dates", "multiplier", "units", "type"],
		     choices=[["previous", "Previous reading"], ["present", "Present reading"], ["dates", "Reading dates"], ["multiplier", "Multiplier"],
		              ["units", "Units"], ["type", "Reading type"]]),
	]},
	"solar": {"label": "Solar / net metering", "description": "Exported units and the solar credit (net-metering accounts only).", "options": [
		_opt("style", "Style", "select", "Summary line", choices=["Summary line", "Table"]),
		_opt("heading", "Heading", "text", "Solar"),
		_opt("notice", "The month's solar rebate notice", "check", 1),
		_opt("boxed", "Boxed", "check", 0),
	]},
	"meter_photo": {"label": "Meter photo", "description": "The photo taken with the reading.", "options": [
		_opt("width", "Width (mm)", "int", 30, min=15, max=90),
		_opt("caption", "Caption", "check", 1),
	]},
	"charges": {"label": "Charges", "description": "The bill's charge lines.", "options": [
		_opt("heading", "Heading", "text", "Charges"),
		_opt("style", "Style", "select", "Table", choices=["Table", "List"]),
		_opt("units", "Units column", "check", 1),
		_opt("rate", "Rate column", "check", 1),
		_opt("group", "Group by charge group", "check", 0),
		_opt("group_totals", "Subtotal for each group", "check", 0),
		_opt("subtotal", "Current bill total", "check", 1),
	]},
	"totals": {"label": "Totals", "description": "Current bill, arrears and the amounts payable.", "options": [
		_opt("rows", "Rows", "multi", ["current", "arrears", "within", "surcharge", "after"],
		     choices=[["current", "Current bill"], ["arrears", "Arrears"], ["within", "Payable within due date"], ["surcharge", "Late payment surcharge"],
		              ["after", "Payable after due date"]]),
		_opt("style", "Style", "select", "Bands", choices=["Bands", "List"]),
		_opt("show_due_date", "Due date beside the amount", "check", 1),
	]},
	"history": {"label": "Bill history", "description": "Earlier bills and what was paid.", "options": [
		_opt("heading", "Heading", "text", ""),
		_opt("months", "Months", "int", 12, min=1, max=24),
		_opt("columns", "Columns", "multi", ["units", "billed", "paid_on", "paid"],
		     choices=[["units", "Units"], ["billed", "Bill"], ["current", "Current bill"], ["paid_on", "Paid on"], ["paid", "Paid"]]),
		_opt("split", "Split into two tables side by side", "check", 0),
	]},
	"notices": {"label": "Notices", "description": "The society's bill notices.", "options": [
		_opt("heading", "Heading", "text", "Notices"),
		_opt("languages", "Languages", "select", "Both", choices=["Both", "English", "Second language"]),
		_opt("boxed", "Boxed", "check", 1),
	]},
	"payment": {"label": "How to pay", "description": "Payment instructions and the society's bank accounts.", "options": [
		_opt("heading", "Heading", "text", "How to pay"),
		_opt("instructions", "Instructions", "check", 1),
		_opt("banks", "Bank accounts", "select", "List", choices=["List", "Table", "None"]),
		_opt("boxed", "Boxed", "check", 1),
	]},
	"text": {"label": "Free text", "description": "Your own text. Placeholders such as {consumer_name} or {due_date} are filled in.", "options": [
		_opt("text", "Text", "textarea", ""),
		_opt("size", "Text size (pt)", "int", 9, min=6, max=24),
		_opt("bold", "Bold", "check", 0),
		_opt("align", "Alignment", "select", "Left", choices=ALIGN),
		_opt("rtl", "Right-to-left (Urdu, Arabic)", "check", 0),
		_opt("boxed", "Boxed", "check", 0),
	]},
	"image": {"label": "Image", "description": "An uploaded image, such as a second logo.", "options": [
		_opt("image", "Image", "image", ""),
		_opt("width", "Width (mm)", "int", 30, min=5, max=190),
		_opt("align", "Alignment", "select", "Left", choices=ALIGN),
	]},
	"signatory": {"label": "Signatory", "description": "The society's bill signatory and signature.", "options": [
		_opt("align", "Alignment", "select", "Right", choices=ALIGN),
	]},
	"stamp": {"label": "Stamp box", "description": "An empty box for the bank or cashier stamp.", "options": [
		_opt("label", "Label", "text", "Bank / cashier stamp"),
		_opt("height", "Height (mm)", "int", 20, min=8, max=60),
	]},
	"footer": {"label": "Contacts and footer", "description": "The society's footer line, phone, email, website and office hours.", "options": [
		_opt("footer", "Footer line", "check", 1),
		_opt("contacts", "Contacts", "check", 1),
		_opt("align", "Alignment", "select", "Center", choices=ALIGN),
	]},
	"spacer": {"label": "Spacer", "description": "Empty space.", "options": [
		_opt("height", "Height (mm)", "int", 3, min=1, max=60),
	]},
}


# every block can be made taller than its content (the designer's bottom grip), e.g. to reserve room on a tear-off copy
for _spec in BLOCKS.values():
	_spec["options"].append(_opt("min_height", "Minimum height (mm, 0 = as tall as its content)", "int", 0, min=0, max=200))
	# own wording for the block's fixed labels ("Payable within due date" -> "Amount due"), set by double-clicking them on the bill
	_spec["options"].append(_opt("labels", "Own wording for labels", "labels", {}))


# ---------------------------------------------------------------- layout checks
def _as_dict(layout):
	if not layout:
		return {"sections": []}
	if isinstance(layout, str):
		try:
			layout = json.loads(layout)
		except ValueError:
			frappe.throw(_("The layout is not valid."))
	if not isinstance(layout, dict):
		frappe.throw(_("The layout is not valid."))
	return layout


def _text(v, limit=MAX_TEXT):
	return str(v if v is not None else "")[:limit]


def clean_option(spec, value):
	"""One option value, coerced to its kind; anything unusable falls back to the default."""
	kind, default = spec["type"], spec.get("default")
	if value is None:
		return copy.deepcopy(default)
	if kind == "check":
		return 1 if cint(value) else 0
	if kind == "int":
		return max(spec.get("min", 0), min(spec.get("max", 999), cint(value)))
	if kind == "select":
		return value if value in spec["choices"] else default
	if kind == "multi":
		allowed = [c[0] for c in spec["choices"]]
		return [x for x in allowed if x in (value if isinstance(value, list) else [])]
	if kind == "fields":
		out = []
		for f in value if isinstance(value, list) else []:
			key = f.get("key") if isinstance(f, dict) else f
			if key in FIELDS and len(out) < 40:
				out.append({"key": key, "label": _text(f.get("label") if isinstance(f, dict) else "", 80).strip()})
		return out
	if kind == "labels":
		out = {}
		for k, v in (value.items() if isinstance(value, dict) else []):
			k, v = _text(k, 60).strip(), _text(v, 80).strip()
			if k and v and len(out) < 40:
				out[k] = v
		return out
	if kind == "image":
		v = _text(value, 300).strip()
		return v if not v or v.startswith(("/files/", "/private/files/")) else ""
	return _text(value)  # text, textarea


def clean_block(block):
	if not isinstance(block, dict) or block.get("type") not in BLOCKS:
		frappe.throw(_("Unknown block type: {0}").format(frappe.as_unicode((block or {}).get("type") if isinstance(block, dict) else block)))
	given = block.get("options") if isinstance(block.get("options"), dict) else {}
	return {"type": block["type"], "options": {s["key"]: clean_option(s, given.get(s["key"])) for s in BLOCKS[block["type"]]["options"]}}


def clean_layout(layout) -> str:
	"""The layout checked and normalised (known blocks and options only), as JSON. Raises on anything that can't be printed."""
	layout = _as_dict(layout)
	sections, rows, blocks = [], 0, 0
	for s in layout.get("sections") or []:
		if not isinstance(s, dict):
			frappe.throw(_("The layout is not valid."))
		out_rows = []
		for r in s.get("rows") or []:
			rows += 1
			cells = []
			for c in (r or {}).get("cells") or []:
				cell_blocks = [clean_block(b) for b in (c or {}).get("blocks") or []]
				blocks += len(cell_blocks)
				cells.append({"width": max(5, min(100, cint((c or {}).get("width")) or 100)), "blocks": cell_blocks})
			if not cells:
				continue
			if sum(c["width"] for c in cells) > 100:
				frappe.throw(_("Section {0}, row {1}: the cells add up to more than 100% of the width.").format(len(sections) + 1, len(out_rows) + 1))
			out_rows.append({"cells": cells})
		sections.append({"label": _text(s.get("label"), 80), "copy_label": _text(s.get("copy_label"), 60),
		                 "english_only": 1 if cint(s.get("english_only")) else 0,
		                 "cut_line_before": 1 if cint(s.get("cut_line_before")) else 0, "cut_text": _text(s.get("cut_text"), 120),
		                 "new_page": 1 if cint(s.get("new_page")) else 0, "rows": out_rows})
	if len(sections) > MAX_SECTIONS or rows > MAX_ROWS or blocks > MAX_BLOCKS:
		frappe.throw(_("A template can have at most {0} sections, {1} rows and {2} blocks.").format(MAX_SECTIONS, MAX_ROWS, MAX_BLOCKS))
	return json.dumps({"sections": sections}, ensure_ascii=False)


def check_page(doc):
	if doc.paper_size not in PAPER_MM:
		frappe.throw(_("Choose a paper size."))
	doc.font_size = flt(doc.font_size) or 9
	if not 6 <= doc.font_size <= 14:
		frappe.throw(_("The text size must be from 6 to 14 pt."))
	for f in ("margin_top", "margin_side"):
		if not 0 <= flt(doc.get(f)) <= 30:
			frappe.throw(_("Margins must be from 0 to 30 mm."))
	if not json.loads(doc.layout).get("sections"):
		frappe.throw(_("Add at least one section with a block before saving. Start from a starter layout if you like."))


# ---------------------------------------------------------------- which template prints a bill
def choose_template(society, utility=None, category=None, on=None):
	"""The approved template for a bill: the society's, in force on the issue date, the most specific first (same utility and
	category, then same utility, then same category, then any), the latest effective date among equals. None: the fixed layout."""
	if not society or not frappe.db.table_exists("Bill Template"):
		return None
	rows = frappe.get_all("Bill Template", filters={"society": society, "docstatus": 1, "effective_from": ["<=", getdate(on or nowdate())]},
	                      fields=["name", "utility", "consumer_category", "effective_from", "approved_on", "modified"])
	fits = [r for r in rows if (not r.utility or r.utility == utility) and (not r.consumer_category or r.consumer_category == category)]
	if not fits:
		return None
	fits.sort(key=lambda r: ((2 if r.utility else 0) + (1 if r.consumer_category else 0), getdate(r.effective_from), str(r.approved_on or r.modified)),
	          reverse=True)
	return fits[0].name


def template_settings(name_or_doc) -> dict:
	doc = frappe.get_cached_doc("Bill Template", name_or_doc) if isinstance(name_or_doc, str) else name_or_doc
	return {"name": doc.get("name"), "paper_size": doc.paper_size or "A4", "font_family": doc.font_family or "Helvetica / Arial",
	        "font_size": flt(doc.font_size) or 9, "margin_top": flt(doc.margin_top), "margin_side": flt(doc.margin_side),
	        "accent_colour": doc.accent_colour, "pages_per_bill": pages_limit(doc), "layout": _as_dict(doc.layout)}


def pdf_options(settings) -> dict:
	return {"page-size": settings["paper_size"], "margin-top": f'{settings["margin_top"]}mm', "margin-bottom": f'{settings["margin_top"]}mm',
	        "margin-left": f'{settings["margin_side"]}mm', "margin-right": f'{settings["margin_side"]}mm', "encoding": "UTF-8"}


# ---------------------------------------------------------------- rendering
def _has_block(layout, kind):
	return any(b["type"] == kind for s in layout.get("sections") or [] for r in s.get("rows") or [] for c in r.get("cells") or [] for b in c.get("blocks") or [])


def english_labels():
	"""Labels without the second language, for a section set to English only (e.g. a narrow tear-off copy)."""
	from markupsafe import Markup, escape  # noqa: PLC0415

	return lambda label: Markup(escape(_(label)))


def fill_plain(text, ctx) -> str:
	"""Free text with its {placeholders} filled from FIELDS, not yet escaped. Unknown {words} stay as they are."""
	out = str(text or "")
	for key, (_label, getter) in FIELDS.items():
		token = "{" + key + "}"
		if token in out:
			try:
				value = getter(ctx)
			except Exception:  # noqa: BLE001 - a field a sample bill doesn't have prints empty
				value = ""
			out = out.replace(token, "" if value is None else str(value))
	return out


def fill(text, ctx):
	"""fill_plain, escaped, with line breaks kept."""
	from markupsafe import Markup, escape  # noqa: PLC0415

	return Markup(str(escape(fill_plain(text, ctx))).replace("\n", "<br>"))


def render_bill(bill, settings, duplicate=0, ctx=None, designer=False) -> str:
	"""HTML of one bill in a designed template (the body only; pdf_of adds the page). `designer`: tag every place in the layout
	for the Bill Designer's on-page editing (preview only, never printed)."""
	from utility_billing.utility_billing import bill_print  # noqa: PLC0415

	ctx = ctx or bill_print.bill_context(bill, duplicate)
	layout, soc, ref = settings["layout"], ctx["soc"], ctx["con"].get("reference_no") or ""
	# the template decides what is shown: a QR, barcode or photo block shows it even if the society's old switches are off
	if _has_block(layout, "qr") and not ctx.get("qr") and ref:
		ctx["qr"] = bill_print.qr_data_uri(bill_print.qr_text(soc, bill, ref))
	if (_has_block(layout, "reference") or _has_block(layout, "header")) and not ctx.get("barcode") and ref:
		from utility_billing.utility_billing import barcode  # noqa: PLC0415

		ctx["barcode"] = barcode.png_data_uri(ref)
	if bill.get("name") == SAMPLE_BILL and _has_block(layout, "solar") and not bill.get("nm_method"):
		make_solar(bill, ctx)
	if _has_block(layout, "meter_photo") and not ctx.get("meter_photo"):
		if bill.get("name") == SAMPLE_BILL:  # previews: a stand-in photo, so the space the photo takes is seen
			ctx["meter_photo"] = sample_meter_photo()
		elif bill.get("meter_reading") is not None:
			ctx["meter_photo"] = bill_print.meter_photo_of(bill, ctx["snap"])

	def field(key):
		try:
			v = FIELDS[key][1](ctx)
		except Exception:  # noqa: BLE001
			v = ""
		return "" if v is None else str(v)

	images = {}

	def image(url):
		if url and url not in images:
			images[url] = bill_print.logo_data_uri(url)
		return images.get(url)

	ctx.update({
		"layout": layout, "page": settings, "accent": settings.get("accent_colour") or soc.get("colour") or "#1F5F8B",
		"font": FONTS.get(settings.get("font_family"), FONTS["Helvetica / Arial"]), "base_size": settings.get("font_size") or 9,
		"field": field, "field_label": lambda key: FIELDS[key][0] if key in FIELDS else key, "amount_field": lambda key: key in AMOUNT_FIELDS,
		"designer": bool(designer), "fill": lambda text: fill(text, ctx), "t_en": english_labels(), "fill_plain": lambda text: fill_plain(text, ctx), "image": image,
	})
	return frappe.render_template("utility_billing/templates/bill/designed_bill.html", ctx)


# ---------------------------------------------------------------- the sample bill (previews before a society has bills)
SAMPLE_BILL = "SAMPLE-BILL"


def make_solar(bill, ctx=None):
	"""A sample bill as a solar consumer's on the society rebate pool: 364 units imported, 100 exported, 100 credited, 264 billed."""
	bill.update({"nm_method": "Society rebate pool", "import_units": 364, "export_units": 300, "credited_units": 100, "nm_credit_factor": 0.3333,
	             "units_carried_in": 0, "units_set_off": 100, "units_carried_out": 0})
	reading = (bill.print_snapshot or {}).get("reading") or {}
	# 364 imported, 100 of the 300 exported credited: the 264 billed units the sample's charges are worked out on
	reading.update({"net_metering": 1, "present_reading": (reading.get("previous_reading") or 0) + 364,
	                "previous_export_reading": 20111, "present_export_reading": 20411, "export_units": 300})
	bill.print_snapshot["solar_notice"] = ("Solar rebate (sample): residents' meters recorded 913,289 units against 787,200 billed by the DISCO; "
	                                       "the 126,089 units are credited to solar producers in proportion to their exports.")
	if ctx is not None:
		ctx["reading"] = reading
		ctx["snap"] = bill.print_snapshot


@functools.cache
def sample_meter_photo() -> str:
	"""A drawn stand-in for a meter photo (a meter's display reading 03905 kWh), for previews of the sample bill."""
	import base64  # noqa: PLC0415
	import io  # noqa: PLC0415

	from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

	img = Image.new("RGB", (480, 320), (58, 64, 72))
	draw = ImageDraw.Draw(img)
	draw.rounded_rectangle((30, 30, 450, 290), radius=28, fill=(226, 230, 234), outline=(30, 34, 40), width=6)
	draw.rectangle((70, 95, 410, 205), fill=(150, 176, 138), outline=(40, 50, 40), width=4)
	try:
		font, small = ImageFont.load_default(size=72), ImageFont.load_default(size=28)
	except TypeError:  # older Pillow
		font = small = ImageFont.load_default()
	draw.text((92, 110), "03905", fill=(28, 36, 28), font=font)
	draw.text((330, 220), "kWh", fill=(30, 34, 40), font=small)
	draw.text((70, 225), "SAMPLE", fill=(120, 126, 132), font=small)
	out = io.BytesIO()
	img.save(out, "JPEG", quality=75)
	return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
def sample_bill(society):
	from utility_billing.utility_billing import bill_print  # noqa: PLC0415

	soc = frappe.get_doc("Society", society)
	today = getdate(nowdate())
	month = today.replace(day=1)
	snap_soc = bill_print.society_snapshot(soc, today)
	history = []
	for i in range(12):
		m = getdate(frappe.utils.add_months(month, -(i + 1)))
		units = 180 + (i * 37) % 160
		history.append({"month": str(m), "units": units, "billed": units * 70 + 2700, "current": units * 70 + 2700,
		                "paid_on": str(m.replace(day=12)), "paid": units * 70 + 2700})
	lines = [frappe._dict(label="Energy charge", basis="Per Unit", quantity=264, rate=70, amount=18480, note="", line_order=1, idx=1, charge_group="Electricity"),
	         frappe._dict(label="Fuel price adjustment", basis="Per Unit", quantity=264, rate=1.3, amount=343, note="", line_order=2, idx=2, charge_group="Electricity"),
	         frappe._dict(label="Service charges", basis="Fixed", quantity=0, rate=0, amount=1700, note="", line_order=3, idx=3, charge_group="Society charges"),
	         frappe._dict(label="Aquifer charges", basis="Fixed", quantity=0, rate=0, amount=1000, note="", line_order=4, idx=4, charge_group="Society charges")]
	current = sum(flt(l.amount) for l in lines)
	snapshot = {"version": bill_print.SNAPSHOT_VERSION, "captured_on": str(today), "society": snap_soc,
	            "consumer": {"name": "Muhammad Ashiq", "mobile": "0300-1234567", "address": "House 12, Block C", "id_number": "35202-1234567-1",
	                         "tax_no": ""},
	            "connection": {"reference_no": "1217030008", "property_no": "12", "block": "C", "sector": "", "area": 10, "area_unit": "Marla",
	                           "category": "Residential", "sanctioned_load_kw": 5, "phase": "Single", "route": "R-03"},
	            "reading": {"meter": "4708508", "previous_reading": 3641, "previous_reading_date": str(month.replace(day=1)), "present_reading": 3905,
	                        "reading_date": str(today), "multiplier": 1, "consumption_type": "Actual", "status": "Approved", "photo": None},
	            "history": history}
	return frappe._dict(name=SAMPLE_BILL, doctype="Utility Bill", docstatus=1, utility="Electricity", society=society, billing_month=month,
	                    issue_date=today, due_date=frappe.utils.add_days(today, 14), final_bill=0, period_note="", revision_no=0,
	                    consumer_name="Muhammad Ashiq", units=264, consumption_type="Actual", current_amount=current, installment_due=0,
	                    arrears=1104, payable_within_due=current + 1104, surcharge_amount=round(current * 0.1),
	                    payable_after_due=current + 1104 + round(current * 0.1), meter_reading=None, lines=lines, print_snapshot=snapshot)


def long_bill(society):
	"""The sample bill made as long as a real one is likely to get: 8 charge lines (2 with notes), 24 months of history (a block shows
	the months it is set to), an installment plan, a period note, a long name and address. A template must print it within its
	pages per bill (user's rule, 2026-11-17)."""
	bill = sample_bill(society)
	snap = bill.print_snapshot
	snap["consumer"].update({"name": "Muhammad Abdul Rehman Khan Niazi (Late) through Legal Heirs",
	                         "address": "House 1234-B, Street 56, Overseas Block Extension, Phase 2, Near Jamia Masjid"})
	months = [getdate(frappe.utils.add_months(bill.billing_month, -(i + 1))) for i in range(24)]
	snap["history"] = [{"month": str(m), "units": 1234, "billed": 123456, "current": 123456, "paid_on": str(m.replace(day=12)), "paid": 123456}
	                   for m in months]
	names = ["Energy charge", "Fuel price adjustment", "Quarterly tariff adjustment", "Fixed charges", "Maintenance charges",
	         "Security charges", "Water and sewerage", "Electricity duty"]
	bill.lines = [frappe._dict(label=n, basis="Per Unit" if i < 3 else "Fixed", quantity=1234 if i < 3 else 0, rate=70.5 if i < 3 else 0,
	                           amount=12345, note="August 2026 units 831" if i in (1, 2) else "", line_order=i, idx=i + 1,
	                           charge_group="Electricity" if i < 4 else "Society charges") for i, n in enumerate(names)]
	bill.update({"installment_due": 5000, "installment_no": 3, "period_note": "Includes a pro-rata period after a meter change"})
	make_solar(bill)  # a solar consumer's bill, the longest kind (export register, solar block and the month's notice)
	return bill


def pages_of(settings, bill) -> int:
	from io import BytesIO  # noqa: PLC0415

	from utility_billing import pdf as ubpdf  # noqa: PLC0415
	from pypdf import PdfReader  # noqa: PLC0415

	from utility_billing.utility_billing import bill_print  # noqa: PLC0415

	pdf = ubpdf.render(bill_print.wrap_pages([render_bill(bill, settings)]), options=pdf_options(settings))
	return len(PdfReader(BytesIO(pdf)).pages)


def pages_limit(doc) -> int:
	"""Pages per bill the template allows: 1 unless the society chose 2 (user's rule, 2026-11-17)."""
	return 2 if cint(doc.get("pages_per_bill")) == 2 else 1


def check_fit(doc):
	"""The long bill must print within the template's pages per bill; checked when it goes for approval and when it is approved."""
	limit = pages_limit(doc)
	pages = pages_of(template_settings(doc), long_bill(doc.society))
	if pages > limit:
		frappe.throw(_("This template runs to {0} pages on a long bill (many charges and a full history) but allows {1}. Shorten it or use a "
		               "smaller text size{2}.").format(pages, limit, _(", or set Pages per Bill to 2") if limit == 1 else ""),
		             title=_("Doesn't fit in {0} page(s)").format(limit))


# ---------------------------------------------------------------- the designer page's endpoints
def _can_design(society=None):
	if not frappe.has_permission("Bill Template", "create") and not frappe.has_permission("Bill Template", "write"):
		raise frappe.PermissionError(_("You can't design bill templates."))
	if society:
		access.check_society(society)


def _settings_from(page, layout):
	page = frappe.parse_json(page or {})
	doc = frappe._dict(paper_size=page.get("paper_size") or "A4", font_family=page.get("font_family"), font_size=page.get("font_size"),
	                   margin_top=page.get("margin_top", 6), margin_side=page.get("margin_side", 8), accent_colour=page.get("accent_colour"),
	                   pages_per_bill=page.get("pages_per_bill"),
	                   layout=clean_layout(layout))
	if doc.paper_size not in PAPER_MM:
		doc.paper_size = "A4"
	doc.font_size = min(14, max(6, flt(doc.font_size) or 9))
	doc.margin_top, doc.margin_side = (min(30, max(0, flt(x))) for x in (doc.margin_top, doc.margin_side))
	return template_settings(doc)


def _preview_bill(society, bill=None):
	if bill:
		doc = frappe.get_doc("Utility Bill", bill)
		if doc.society != society:
			frappe.throw(_("That bill belongs to another society."))
		doc.check_permission("read")
		return doc
	return sample_bill(society)


@frappe.whitelist()
def designer_data(template: str | None = None):
	"""Everything the Bill Designer page needs: blocks and their options, fields, starters, the user's societies and the template."""
	_can_design()
	doc = None
	if template:
		d = frappe.get_doc("Bill Template", template)
		d.check_permission("read")
		access.check_society(d.society)
		doc = {k: d.get(k) for k in ("name", "template_name", "society", "utility", "consumer_category", "effective_from", "description", "paper_size",
		                             "font_family", "font_size", "margin_top", "margin_side", "accent_colour", "pages_per_bill", "docstatus",
		                             "workflow_state")}
		doc["layout"] = _as_dict(d.layout)
		doc["can_write"] = d.docstatus == 0 and d.has_permission("write")
	return {
		"blocks": [{"type": k, "label": _(v["label"]), "description": _(v["description"]),
		            "options": [dict(o, label=_(o["label"]), choices=[[c[0], _(c[1])] if isinstance(c, list) else c for c in o.get("choices", [])])
		                        for o in v["options"]]} for k, v in BLOCKS.items()],
		"fields": [{"key": k, "label": _(v[0])} for k, v in FIELDS.items()],
		"starters": [{"key": k, "label": _(v)} for k, v in STARTERS.items()],
		"paper_sizes": list(PAPER_MM), "paper_mm": PAPER_MM, "fonts": list(FONTS),
		"societies": access.visible_societies(),
		"template": doc,
	}


@frappe.whitelist()
def society_bills(society: str, txt: str | None = None):
	"""Recent bills of a society to preview with."""
	_can_design(society)
	filters = {"society": society, "docstatus": ["<", 2]}
	if txt:
		filters["name"] = ["like", f"%{txt}%"]
	return frappe.get_list("Utility Bill", filters=filters, fields=["name", "consumer_name", "billing_month"], order_by="creation desc", limit=20)


@frappe.whitelist()
def starter(key: str):
	"""A starter layout shipped with the app, with the page settings it was made for."""
	if key not in STARTERS:
		frappe.throw(_("Unknown starter layout."))
	import os  # noqa: PLC0415

	path = os.path.join(os.path.dirname(__file__), "..", "setup_data", "bill_starters", f"{key}.json")
	with open(path, encoding="utf-8") as f:
		data = json.load(f)
	page = {k: v for k, v in (data.get("page") or {}).items() if k in ("paper_size", "font_size", "margin_top", "margin_side")}
	return {"layout": json.loads(clean_layout(data)), "page": page}


@frappe.whitelist(methods=["POST"])
def preview(society: str, layout: str, page: str | None = None, bill: str | None = None, designer: int = 0):
	"""The bill rendered with an unsaved layout, as a whole HTML page at paper width, for the designer's live preview
	(`designer`: with every block tagged for on-page editing)."""
	_can_design(society)
	settings = _settings_from(page, layout)
	doc = _preview_bill(society, bill)
	body = render_bill(doc, settings, designer=cint(designer))
	w, _h = PAPER_MM[settings["paper_size"]]
	return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>html,body{{margin:0;background:#e9edf1}}'
	        f'.sheet{{width:{w}mm;min-height:{_h}mm;box-sizing:border-box;padding:{settings["margin_top"]}mm {settings["margin_side"]}mm;'
	        f'margin:6mm auto;background:#fff;box-shadow:0 1px 6px rgba(0,0,0,.18)}}</style></head><body><div class="sheet">{body}</div></body></html>')


@frappe.whitelist(methods=["POST"])
def fit_check(society: str, layout: str, page: str | None = None, bill: str | None = None):
	"""Pages the layout takes for the sample (or chosen) bill and for the long bill, as the PDF engine prints them."""
	_can_design(society)
	settings = _settings_from(page, layout)
	return {"pages": pages_of(settings, _preview_bill(society, bill)), "long": pages_of(settings, long_bill(society)),
	        "limit": settings["pages_per_bill"]}


@frappe.whitelist(methods=["POST"])
def preview_pdf(society: str, layout: str, page: str | None = None, bill: str | None = None):
	"""The exact PDF of the bill with an unsaved layout."""
	from utility_billing import pdf as ubpdf  # noqa: PLC0415

	from utility_billing.utility_billing import bill_print  # noqa: PLC0415

	_can_design(society)
	settings = _settings_from(page, layout)
	doc = _preview_bill(society, bill)
	html = bill_print.wrap_pages([render_bill(doc, settings)])
	bill_print.send_pdf(ubpdf.render(html, options=pdf_options(settings)), "bill-template-preview.pdf")


@frappe.whitelist(methods=["POST"])
def save_template(values: str):
	"""Create or update a draft template from the designer."""
	values = frappe.parse_json(values)
	fields = ("template_name", "society", "utility", "consumer_category", "effective_from", "description", "paper_size", "font_family", "font_size",
	          "margin_top", "margin_side", "accent_colour", "pages_per_bill")
	if values.get("name"):
		doc = frappe.get_doc("Bill Template", values["name"])
		if doc.docstatus != 0:
			frappe.throw(_("An approved template can't change. Amend it to make a new version."))
	else:
		doc = frappe.new_doc("Bill Template")
	_can_design(values.get("society"))
	doc.update({k: values.get(k) for k in fields if k in values})
	doc.layout = json.dumps(values.get("layout") or {})
	doc.save()
	return doc.name


@frappe.whitelist()
def export_template(name: str):
	"""A template as a file, to load on another client's site (one site per client)."""
	doc = frappe.get_doc("Bill Template", name)
	doc.check_permission("read")
	access.check_society(doc.society)
	data = {"kind": "utility_billing.bill_template", "version": 1, "template_name": doc.template_name, "description": doc.description,
	        "pages_per_bill": pages_limit(doc),
	        "paper_size": doc.paper_size, "font_family": doc.font_family, "font_size": doc.font_size, "margin_top": doc.margin_top,
	        "margin_side": doc.margin_side, "accent_colour": doc.accent_colour, "layout": _as_dict(doc.layout)}
	frappe.local.response.filename = f"{frappe.scrub(doc.template_name)}.bill-template.json"
	frappe.local.response.filecontent = json.dumps(data, indent=1, ensure_ascii=False)
	frappe.local.response.type = "download"


@frappe.whitelist(methods=["POST"])
def import_template(data: str, society: str):
	"""A new draft template for a society from an exported file. Images aren't carried across sites: their links are dropped."""
	_can_design(society)
	data = frappe.parse_json(data)
	if not isinstance(data, dict) or data.get("kind") != "utility_billing.bill_template":
		frappe.throw(_("This is not a bill template file."))
	layout = _as_dict(data.get("layout"))
	for s in layout.get("sections") or []:
		for r in s.get("rows") or []:
			for c in r.get("cells") or []:
				for b in c.get("blocks") or []:
					for o in BLOCKS.get(b.get("type"), {}).get("options", []):
						if o["type"] == "image" and isinstance(b.get("options"), dict):
							b["options"][o["key"]] = ""
	doc = frappe.new_doc("Bill Template")
	doc.update({k: data.get(k) for k in ("template_name", "description", "paper_size", "font_family", "font_size", "margin_top", "margin_side",
	                                      "accent_colour", "pages_per_bill")})
	doc.update({"society": society, "effective_from": nowdate(), "layout": json.dumps(layout)})
	doc.template_name = _text(doc.template_name or _("Imported template"), 140)
	doc.insert()
	return doc.name
