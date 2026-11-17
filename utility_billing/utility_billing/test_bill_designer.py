# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill Designer (increment 27): layouts are checked, every block and starter renders, a bill prints with its society's approved
template and reprints exactly after the template is amended, the most specific template wins, maker-checker, society isolation,
export and import."""

import json

import frappe

from utility_billing.utility_billing import bill_designer, bill_print
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, GENERAL_MANAGER, act

OTHER = "TST-ISO"


def layout_of(*types):
	return {"sections": [{"label": "Main", "copy_label": "Consumer Copy", "rows": [{"cells": [{"width": 100, "blocks": [{"type": t} for t in types]}]}]}]}


class IntegrationTestBillDesignerLayout(BillingCase):
	def test_layout_is_checked_and_normalised(self):
		with self.assertRaises(frappe.ValidationError):
			bill_designer.clean_layout(layout_of("nonsense"))
		too_wide = {"sections": [{"rows": [{"cells": [{"width": 60, "blocks": []}, {"width": 50, "blocks": []}]}]}]}
		with self.assertRaises(frappe.ValidationError):
			bill_designer.clean_layout(too_wide)
		with self.assertRaises(frappe.ValidationError):
			bill_designer.clean_layout({"sections": [{"rows": []}] * (bill_designer.MAX_SECTIONS + 1)})
		raw = {"sections": [{"label": "S", "rows": [{"cells": [{"width": 100, "blocks": [
			{"type": "history", "options": {"months": 99, "columns": ["units", "evil"], "split": "1", "junk": 1}},
			{"type": "totals", "options": {"style": "Sideways"}},
			{"type": "details", "options": {"fields": [{"key": "consumer_name", "label": "Member"}, {"key": "password"}, "due_date"]}},
			{"type": "image", "options": {"image": "https://evil.example/x.png"}},
		]}]}]}]}
		out = json.loads(bill_designer.clean_layout(raw))
		h, t, d, i = out["sections"][0]["rows"][0]["cells"][0]["blocks"]
		self.assertEqual((h["options"]["months"], h["options"]["columns"], h["options"]["split"]), (24, ["units"], 1))
		self.assertNotIn("junk", h["options"])
		self.assertEqual(t["options"]["style"], "Bands")  # unknown choice: the default
		self.assertEqual(d["options"]["fields"], [{"key": "consumer_name", "label": "Member"}, {"key": "due_date", "label": ""}])
		self.assertEqual(i["options"]["image"], "")  # only the site's own files

	def test_free_text_is_escaped_and_placeholders_filled(self):
		bill = bill_designer.sample_bill(SOC)
		settings = bill_designer._settings_from({}, {"sections": [{"rows": [{"cells": [{"width": 100, "blocks": [
			{"type": "text", "options": {"text": "Dear {consumer_name}, pay {payable_within_due} by {due_date}. <script>x</script> {unknown}"}}]}]}]}]})
		html = bill_designer.render_bill(bill, settings)
		self.assertIn("Dear Muhammad Ashiq, pay", html)
		self.assertIn("&lt;script&gt;x&lt;/script&gt; {unknown}", html)
		self.assertNotIn("<script>x", html)

	def test_designer_tags_only_in_the_preview_and_minimum_height(self):
		bill = bill_designer.sample_bill(SOC)
		layout = layout_of("header", "details", "text")
		layout["sections"][0]["rows"][0]["cells"][0]["blocks"][1]["options"] = {"min_height": 40, "heading": "Member"}
		settings = bill_designer._settings_from({}, layout)
		printed, preview = bill_designer.render_bill(bill, settings), bill_designer.render_bill(bill, settings, designer=True)
		for tag in ("data-edit=", "data-p=", "data-c=", "Double-click to type"):
			self.assertNotIn(tag, printed, tag)  # a printed bill carries none of the designer's tags
			self.assertIn(tag, preview, tag)
		self.assertIn('data-edit="field:0"', preview)
		self.assertIn("min-height: 40mm", printed)
		self.assertEqual(json.loads(bill_designer.clean_layout(layout))["sections"][0]["rows"][0]["cells"][0]["blocks"][1]["options"]["min_height"], 40)

	def test_every_block_and_starter_renders(self):
		bill = bill_designer.sample_bill(SOC)
		settings = bill_designer._settings_from({}, layout_of(*bill_designer.BLOCKS))
		html = bill_designer.render_bill(bill, settings)
		for text in ("Muhammad Ashiq", "1217030008", "Energy charge", "data:image/png;base64,", "Consumer Copy"):
			self.assertIn(text, html, text)
		for key in bill_designer.STARTERS:
			st = bill_designer.starter(key)
			html = bill_designer.render_bill(bill, bill_designer._settings_from(st["page"], st["layout"]))
			# every ready-made layout prints what a consumer needs to pay: reference, bill number, due date and both amounts
			for text in ("1217030008", "SAMPLE-BILL", frappe.utils.formatdate(bill.due_date), "22,627", "24,779"):
				self.assertIn(text, html, f"{key}: {text}")
			# the meter photo is printed on every layout (user's request 2026-11-16); previews show a stand-in photo
			self.assertIn('class="meter-photo" src="data:image/jpeg;base64,', html, key)
			self.assertGreater(len(st["layout"]["sections"]), 1, key)  # every starter has a tear-off copy


class IntegrationTestBillDesigner(BillingCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()
		# the society limit is rolled back, but the user's permissions stay cached; later tests would see it (found 2026-11-17)
		frappe.clear_cache(user=BILLING_MANAGER)

	def template(self, starter="two_copies", approve=True, effective="2026-10-01", **kw):
		st = bill_designer.starter(starter)
		doc = frappe.get_doc(dict({"doctype": "Bill Template", "template_name": f"Test {starter}", "society": SOC, "effective_from": effective,
		                           "layout": json.dumps(st["layout"])}, **st["page"], **kw)).insert()
		if approve:
			act(doc, "Submit for Approval", BILLING_MANAGER)
			act(doc, "Approve", GENERAL_MANAGER)
			doc.reload()
		return doc

	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.post(self.make_run("2026-10-01", "2026-11-01"))
		return self.bill(run)

	def test_bill_prints_with_approved_template_and_reprints_exactly(self):
		tpl = self.template()
		self.assertEqual((tpl.docstatus, tpl.workflow_state), (1, "Approved"))
		bill = self.october_posted()
		self.assertEqual(bill_print.snapshot_of(bill)["template"], tpl.name)
		html = bill_print.bill_html(bill.name)
		for text in ("Customer Copy", "Bank Copy", "Accounts Copy", "22,260", "23,364", "25,145", bill.name):
			self.assertIn(text, html, text)
		self.assertNotIn("cut here — office / bank copy", html)  # not the fixed layout
		self.assertTrue(bill_print.pdf_of([bill.name]).startswith(b"%PDF"))
		# a new version replaces it for later bills; this bill still prints with the version it was issued with
		act(tpl, "Cancel", GENERAL_MANAGER)
		frappe.set_user("Administrator")
		self.assertEqual(bill_print.bill_html(bill.name), html)

	def test_bill_without_template_prints_fixed_layout(self):
		bill = self.october_posted()
		self.assertIsNone(bill_print.snapshot_of(bill).get("template"))
		html = bill_print.bill_html(bill.name)
		self.assertIn("Consumer copy", html)  # the fixed layout's label
		self.assertNotIn("blk-", html)

	def test_draft_template_is_not_used(self):
		self.template(approve=False)
		self.assertIsNone(bill_designer.choose_template(SOC, None, None, "2026-11-01"))

	def test_most_specific_template_wins(self):
		utility = frappe.get_all("Utility Type", pluck="name", limit=1)[0]
		category = frappe.get_all("Consumer Category", pluck="name", limit=1)[0]
		general = self.template("standard")
		self.assertEqual(bill_designer.choose_template(SOC, utility, category, "2026-11-01"), general.name)
		self.assertIsNone(bill_designer.choose_template(SOC, utility, category, "2026-09-01"))  # before it is in force
		for_cat = self.template("compact_grid", consumer_category=category)
		self.assertEqual(bill_designer.choose_template(SOC, utility, category, "2026-11-01"), for_cat.name)
		for_util = self.template("classic_boxed", utility=utility)
		self.assertEqual(bill_designer.choose_template(SOC, utility, category, "2026-11-01"), for_util.name)  # utility beats category
		self.assertEqual(bill_designer.choose_template(SOC, "Some Other Utility", category, "2026-11-01"), for_cat.name)
		later = self.template("two_copies", effective="2026-10-20")
		self.assertEqual(bill_designer.choose_template(SOC, "Some Other Utility", "Some Other Category", "2026-11-01"), later.name)
		self.assertEqual(bill_designer.choose_template(SOC, "Some Other Utility", "Some Other Category", "2026-10-10"), general.name)

	def test_maker_cannot_approve(self):
		doc = self.template(approve=False)
		act(doc, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises(frappe.PermissionError):
			act(doc, "Approve", BILLING_MANAGER)

	def test_approved_template_cannot_change(self):
		doc = self.template()
		with self.assertRaises(frappe.ValidationError):
			bill_designer.save_template(json.dumps({"name": doc.name, "template_name": "Changed", "society": SOC, "layout": layout_of("spacer")}))

	def test_designer_endpoints_and_isolation(self):
		if not frappe.db.exists("Society", OTHER):
			frappe.get_doc({"doctype": "Society", "society_name": "Isolation Test Society", "short_name": OTHER}).insert()
		name = frappe.db.get_value("User Access", {"user": BILLING_MANAGER})
		ua = frappe.get_doc("User Access", name) if name else frappe.new_doc("User Access")
		ua.user, ua.all_societies = BILLING_MANAGER, 0
		ua.set("societies", [{"society": SOC}])
		ua.save()
		frappe.set_user(BILLING_MANAGER)
		layout = json.dumps(layout_of("header", "details", "totals"))
		self.assertIn('class="sheet"', bill_designer.preview(SOC, layout))
		data = bill_designer.designer_data()
		self.assertEqual({s.name for s in data["societies"]}, {SOC})
		self.assertEqual(len(data["starters"]), 4)
		name = bill_designer.save_template(json.dumps({"template_name": "Mine", "society": SOC, "effective_from": "2026-11-01", "layout": layout_of("header")}))
		self.assertEqual(frappe.db.get_value("Bill Template", name, "society"), SOC)
		for call in (lambda: bill_designer.preview(OTHER, layout),
		             lambda: bill_designer.save_template(json.dumps({"template_name": "Theirs", "society": OTHER, "effective_from": "2026-11-01",
		                                                             "layout": layout_of("header")})),
		             lambda: bill_designer.society_bills(OTHER)):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_export_and_import(self):
		doc = self.template("compact_grid")
		layout = json.loads(doc.layout)
		layout["sections"][0]["rows"][0]["cells"][0]["blocks"].append({"type": "image", "options": {"image": "/files/logo.png"}})
		frappe.db.set_value("Bill Template", doc.name, "layout", json.dumps(layout))
		bill_designer.export_template(doc.name)
		data = frappe.local.response.filecontent
		self.assertIn('"kind": "utility_billing.bill_template"', data)
		new = frappe.get_doc("Bill Template", bill_designer.import_template(data, SOC))
		self.assertEqual((new.docstatus, new.template_name, new.paper_size), (0, doc.template_name, doc.paper_size))
		self.assertNotIn("/files/logo.png", new.layout)  # images don't travel between sites
		with self.assertRaises(frappe.ValidationError):
			bill_designer.import_template(json.dumps({"kind": "something else"}), SOC)

	def test_pages_per_bill_is_enforced(self):
		"""User's rule (2026-11-17): a template prints on one page unless the society chose 2; it must fit a long bill."""
		long = bill_designer.long_bill(SOC)
		for key in bill_designer.STARTERS:  # every ready-made layout fits a long bill on one page
			st = bill_designer.starter(key)
			self.assertEqual(bill_designer.pages_of(bill_designer._settings_from(st["page"], st["layout"]), long), 1, key)
		tall = {"sections": [{"label": "Tall", "rows": [{"cells": [{"width": 100, "blocks": [{"type": "spacer", "options": {"height": 60}}] * 6
		                                                             + [{"type": "charges"}]}]}]}]}
		doc = frappe.get_doc({"doctype": "Bill Template", "template_name": "Tall", "society": SOC, "effective_from": "2026-10-01",
		                      "layout": json.dumps(tall)}).insert()  # a draft may overflow while it is designed
		self.assertEqual(doc.pages_per_bill, "1")
		with self.assertRaises(frappe.ValidationError):
			act(doc, "Submit for Approval", BILLING_MANAGER)
		doc.reload()
		doc.pages_per_bill = "2"
		doc.save()
		act(doc, "Submit for Approval", BILLING_MANAGER)  # two pages allowed: it fits
		act(doc, "Approve", GENERAL_MANAGER)
		self.assertEqual(bill_designer.template_settings(doc.name)["pages_per_bill"], 2)
		taller = {"sections": [{"label": "Taller", "rows": [{"cells": [{"width": 100, "blocks": [{"type": "spacer", "options": {"height": 60}}] * 12}]}]}]}
		doc = frappe.get_doc({"doctype": "Bill Template", "template_name": "Taller", "society": SOC, "effective_from": "2026-10-01",
		                      "pages_per_bill": "2", "layout": json.dumps(taller)}).insert()
		with self.assertRaises(frappe.ValidationError):
			act(doc, "Submit for Approval", BILLING_MANAGER)
		fit = bill_designer.fit_check(SOC, json.dumps(tall), json.dumps({"pages_per_bill": "1"}))
		self.assertEqual((fit["limit"], fit["long"] > 1), (1, True))

	def test_preview_pdf(self):
		bill_designer.preview_pdf(SOC, json.dumps(layout_of("header", "charges", "totals")))
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
