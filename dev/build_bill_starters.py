"""Writes the starter bill layouts shipped with the app (utility_billing/setup_data/bill_starters/*.json). Run: python3 dev/build_bill_starters.py"""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utility_billing", "setup_data", "bill_starters")
os.makedirs(OUT, exist_ok=True)


def b(kind, **options):
	return {"type": kind, "options": options}


def F(*keys):
	return [{"key": k} for k in keys]


def row(*cells):
	return {"cells": [{"width": w, "blocks": list(blocks)} for w, blocks in cells]}


def section(label, rows, copy_label="", cut=0, cut_text=""):
	return {"label": label, "copy_label": copy_label, "cut_line_before": cut, "cut_text": cut_text, "new_page": 0, "rows": rows}


GENERATED = "This document is system generated and does not need a signature or stamp."

standard = {"sections": [
	section("Consumer copy", [
		row((100, [b("header", logo="Left", title="", show_copy_label=1)])),
		row((100, [b("title_band")])),
		row((52, [b("details", heading="Consumer", fields=F("consumer_name", "property", "category", "sanctioned_load", "meter_no", "mobile"))]),
		    (32, [b("reference", dates=1)]), (16, [b("qr", size=22), b("meter_photo", width=26, caption=1)])),
		row((100, [b("reading")])),
		row((58, [b("charges"), b("totals", rows=["arrears", "within", "surcharge", "after"])]), (42, [b("history")])),
		row((58, [b("notices"), b("footer", align="Left", footer=0)]), (42, [b("payment"), b("signatory")])),
	], copy_label="Consumer copy"),
	section("Office copy", [
		row((64, [b("details", heading="", columns=2, boxed=0, fields=F("reference_no", "consumer_name", "property", "billing_month",
		                                                                  "payable_within_due", "due_date", "payable_after_due"))]),
		    (20, [b("reference", large=0, dates=0, boxed=0)]), (16, [b("stamp", height=16)])),
	], copy_label="Office copy", cut=1, cut_text="cut here — office / bank copy"),
]}

tear_copy = lambda label: section(label, [  # noqa: E731
	row((60, [b("details", boxed=0, fields=F("reference_no", "consumer_name", "address", "mobile"))]),
	    (40, [b("copy_label", align="Right"), b("reference", large=0, dates=0, boxed=0), b("text", text="Billing month: {billing_month_short}", bold=1, align="Right")])),
	row((100, [b("details", columns=2, fields=F("payable_within_due", "payable_after_due"))])),
], copy_label=label, cut=1, cut_text="cut here")

two_copies = {"sections": [
	section("Customer copy", [
		row((62, [b("header", logo="Left", title="{title}", show_copy_label=0, show_letterhead=0, show_tax_ids=0),
		          b("details", boxed=0, fields=F("consumer_name", "address", "mobile"))]),
		    (38, [b("copy_label", align="Right"), b("reference", large=0, dates=0, boxed=0)])),
		row((100, [b("details", columns=4, fields=F("meter_no", "reading_date", "plot_size", "billing_month_short", "issue_date", "due_date", "category",
		                                              "bill_no"))])),
		row((50, [b("reading", style="List", columns=["previous", "present", "units"]), b("charges", style="List", heading="", units=0, rate=0, subtotal=1),
		          b("totals", style="List", rows=["arrears", "within", "surcharge", "after"])]),
		    (50, [b("payment", heading="How to pay", banks="List"), b("notices", heading="", boxed=0)])),
		row((100, [b("history", split=1, columns=["units", "billed", "paid_on", "paid"])])),
	], copy_label="Customer Copy"),
	tear_copy("Bank Copy"),
	tear_copy("Accounts Copy"),
]}
two_copies["sections"][-1]["rows"].append(row((100, [b("text", text=GENERATED, size=7, align="Left")])))

grid_copy = lambda label: section(label, [  # noqa: E731
	row((66, [b("details", columns=2, fields=F("reference_no", "consumer_name", "property_no", "meter_no", "billing_month", "due_date")),
	          b("payment", heading="", instructions=0, banks="Table", boxed=1)]),
	    (34, [b("copy_label", align="Center"), b("qr", size=18), b("totals", style="List", rows=["current", "arrears", "within", "after"], show_due_date=0)])),
], copy_label=label, cut=1, cut_text="cut here")

compact_grid = {"sections": [
	section("Customer copy", [
		row((16, [b("qr", size=20, align="Left")]), (84, [b("header", logo="Right", align="Center", title="{title}", show_copy_label=1)])),
		row((100, [b("details", columns=3, fields=F("reference_no", "consumer_name", "property_no", "cnic", "ntn", "category", "meter_no", "billing_month", "bill_no",
		                                              "reading_date", "issue_date", "due_date", "sanctioned_load"))])),
		row((62, [b("payment", heading="Bank accounts (for online payment)", instructions=0, banks="Table")]),
		    (38, [b("totals", style="List", rows=["current", "arrears", "within", "after"])])),
		row((40, [b("reading", columns=["previous", "present", "units"])]), (60, [b("notices")])),
		row((100, [b("footer")])),
	], copy_label="Customer Copy"),
	grid_copy("Bank Copy"),
	grid_copy("Office Copy"),
]}

classic_boxed = {"sections": [
	section("Consumer copy", [
		row((100, [b("header", logo="Left", align="Center", title="Service Invoice", show_copy_label=1)])),
		row((30, [b("text", text="{property_no}", size=14, bold=1, boxed=1)]), (70, [b("text", text="{consumer_name}", size=14, bold=1, boxed=1)])),
		row((100, [b("details", columns=3, fields=F("reference_no", "meter_no", "billing_month_short", "issue_date", "due_date", "bill_no"))])),
		row((100, [b("charges")])),
		row((45, [b("history", months=6, columns=["units", "billed", "paid_on", "paid"])]),
		    (55, [b("totals", style="List", rows=["current", "arrears", "within", "surcharge", "after"]), b("notices"),
		          b("text", text="Amount paid: ______________\nSignature: ______________", align="Right")])),
		row((100, [b("payment", heading="", banks="None")])),
	], copy_label="Consumer Copy"),
	section("Office copy", [
		row((100, [b("header", logo="Left", align="Center", title="Collection Department", show_name=0, show_letterhead=0, show_tax_ids=0)])),
		row((100, [b("details", columns=3, fields=F("property_no", "consumer_name", "reference_no", "billing_month_short", "issue_date", "due_date",
		                                              "payable_within_due", "surcharge", "payable_after_due"))])),
	], copy_label="Office Copy", cut=1, cut_text="cut here"),
]}

PAGES = {"standard": {"font_size": 8, "margin_top": 5}, "two_copies": {"font_size": 8, "margin_top": 5}, "compact_grid": {"font_size": 8}, "classic_boxed": {"font_size": 9}}
for name, layout in (("standard", standard), ("two_copies", two_copies), ("compact_grid", compact_grid), ("classic_boxed", classic_boxed)):
	layout["page"] = {"paper_size": "A4", "margin_top": 6, "margin_side": 8, **PAGES[name]}
	with open(os.path.join(OUT, f"{name}.json"), "w", encoding="utf-8") as f:
		json.dump(layout, f, indent=1, ensure_ascii=False)
		f.write("\n")
print("written", sorted(os.listdir(OUT)))
