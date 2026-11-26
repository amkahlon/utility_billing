# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Walk order (increment 60): routes hold the walk; it is built from reference numbers, changed by moving accounts or importing a
sheet, and bills print in it with a cover sheet per route and the route and place on each bill. Reference numbers never change."""

import io

import frappe
from frappe.utils import cint

from utility_billing.utility_billing import bill_print, readings, walk
from utility_billing.utility_billing.test_billing import SOC, BillingCase

ROUTE = "Reading Route"


class WalkCase(BillingCase):
	def account(self, ref, block="W Block"):
		"""An account of the test society with this reference number."""
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": f"W {ref}", "block": block}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": f"TST-WLK-{ref}", "society": SOC, "utility": "Electricity"}).insert().name
		consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		return frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumer, "utility": "Electricity",
		                       "consumer_category": self.cat, "status": "Active", "reference_no": ref,
		                       "meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 100}]}).insert().name

	def route(self, code, stops, **kw):
		return frappe.get_doc({"doctype": ROUTE, "society": SOC, "utility": "Electricity", "route_code": code,
		                       "stops": [{"connection": c} for c in stops], **kw}).insert()

	def walk_of(self, code):
		doc = frappe.get_doc(ROUTE, f"{SOC}-Electricity-{code}")
		return [(s.sequence, s.connection) for s in doc.stops]


class IntegrationTestWalkOrder(WalkCase):
	def test_the_rows_are_the_walk_numbered_on_save(self):
		a, b, c = (self.account(r) for r in ("0190000001", "0190000002", "0190000003"))
		r = self.route("W1", [a, b, c])
		self.assertEqual(self.walk_of("W1"), [(1, a), (2, b), (3, c)])
		self.assertEqual(frappe.db.get_value("Connection", c, "route"), "W1")
		# a row dragged to the top: the order follows the rows, numbered again
		r.stops[2].idx, r.stops[0].idx, r.stops[1].idx = 1, 2, 3
		r.save()
		self.assertEqual(self.walk_of("W1"), [(1, c), (2, a), (3, b)])
		self.assertEqual(walk.of(a).label, "Route W1 · 2")
		self.assertEqual(frappe.db.get_value("Connection", a, "reference_no"), "0190000001")  # the reference number stays

	def test_move_accounts_to_a_place_and_across_routes(self):
		a, b, c, d = (self.account(r) for r in ("0190000001", "0190000002", "0190000003", "0290000001"))
		r1 = self.route("W1", [a, b, c])
		self.route("W2", [d])
		out = walk.move(r1.name, f"{d}\n{c}", "after", a)
		self.assertEqual(self.walk_of("W1"), [(1, a), (2, d), (3, c), (4, b)])
		self.assertEqual(out["position"], {d: 2, c: 3})
		self.assertEqual(self.walk_of("W2"), [])
		self.assertEqual(frappe.db.get_value("Connection", d, "route"), "W1")
		walk.move(r1.name, b, "start")
		self.assertEqual([x[1] for x in self.walk_of("W1")], [b, a, d, c])
		for args in ((f"{a}, {b}", "after", a), ("9999999999", "end", None), (a, "after", "0390000009")):
			with self.assertRaises(frappe.ValidationError):
				walk.move(r1.name, *args)
		self.assertEqual([x[1] for x in self.walk_of("W1")], [b, a, d, c])  # a refused move changes nothing
		# changing the route on the account puts it at the end of that route, as before
		conn = frappe.get_doc("Connection", b)
		conn.route = "W2"
		conn.save()
		self.assertEqual((self.walk_of("W2"), [x[1] for x in self.walk_of("W1")]), ([(1, b)], [a, d, c]))

	def test_routes_built_from_reference_numbers(self):
		refs = ["0290000002", "0190000002", "0290000001", "0190000001", "0190000010"]
		for r in refs:
			self.account(r)
		mine = lambda p: [g for g in p["groups"] if g["code"] in ("01", "02")]  # the test society's own account has another number
		plan = walk.preview_build(SOC, "Electricity", 2)
		self.assertEqual(mine(plan), [{"code": "01", "accounts": 3, "existing": False, "in_use": False},
		                              {"code": "02", "accounts": 2, "existing": False, "in_use": False}])
		self.route("02", [])  # a route with that code already: the group goes at its end
		out = walk.build_from_references(SOC, "Electricity", 2)
		self.assertGreaterEqual(out["created"], 1)
		self.assertEqual(out["extended"], 1)
		self.assertEqual([x[1] for x in self.walk_of("01")], ["0190000001", "0190000002", "0190000010"])
		self.assertEqual([x[1] for x in self.walk_of("02")], ["0290000001", "0290000002"])
		self.assertEqual(frappe.db.get_value(ROUTE, f"{SOC}-Electricity-01", "description"), "Made from reference numbers starting 01")
		with self.assertRaises(frappe.ValidationError):  # nothing left to put on a route
			walk.build_from_references(SOC, "Electricity", 2)
		with self.assertRaises(frappe.ValidationError):
			walk.preview_build(SOC, "Electricity", 0)

	def sheet(self, text):
		return frappe.get_doc({"doctype": "File", "file_name": "walk.csv", "content": text.encode(), "is_private": 1}).insert().file_url

	def test_import_walk_order_from_a_sheet(self):
		a, b, c, d = (self.account(r) for r in ("0190000001", "0190000002", "0190000003", "0290000001"))
		self.route("W1", [a, b, c])
		rows = walk.sheet_rows(SOC, "Electricity")
		self.assertEqual(rows[0][:3], ["route", "order", "reference_no"])
		self.assertIn(["W1", 2, b], [x[:3] for x in rows])
		# the field staff: c before a on W1, d on a new route W3; b isn't listed, so it follows the listed ones
		out = walk.import_walk_order(SOC, "Electricity", self.sheet(f"Route,Reference No,Order\nW1,{a},2\nW1,{c},1\nW3,{d},\n"))
		self.assertEqual(out, {"routes": 2, "created": 1, "accounts": 3})
		self.assertEqual([x[1] for x in self.walk_of("W1")], [c, a, b])
		self.assertEqual(self.walk_of("W3"), [(1, d)])
		# a wrong row: nothing changes, every bad row is named
		for bad in (f"route,reference_no\nW1,{a}\nW3,{a}\n", "route,reference_no\nW1,9999999999\n", f"reference_no\n{a}\n",
		            f"route,reference_no,order\nW1,{a},first\n", f"route,reference_no\n,{a}\n"):
			with self.assertRaises(frappe.ValidationError):
				walk.import_walk_order(SOC, "Electricity", self.sheet(bad))
		self.assertEqual([x[1] for x in self.walk_of("W1")], [c, a, b])

	def test_staff_without_route_rights_cannot_change_the_walk(self):
		a = self.account("0190000001")
		r = self.route("W1", [a])
		frappe.set_user("Guest")
		try:
			for call in (lambda: walk.move(r.name, a, "end"), lambda: walk.preview_build(SOC, "Electricity", 2),
			             lambda: walk.walk_sheet(SOC, "Electricity")):
				with self.assertRaises(frappe.PermissionError):
					call()
		finally:
			frappe.set_user("Administrator")


class IntegrationTestWalkPrint(WalkCase):
	def test_bills_print_in_walk_order_with_a_cover_sheet_per_route(self):
		from pypdf import PdfReader

		first = self.account("0190000001", block="Z Block")
		self.route("W1", [first, self.conn])  # the walk, not the block, decides the order
		cycle = self.month_reading("2026-10-01", 3905, close=False)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": first})
		r.present_reading, r.reading_date = 400, "2026-10-05"
		r.save()
		if r.status == "Flagged":
			readings.approve_reading(r.name, "Checked")
		readings.close_cycle(cycle)
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01")
		bills = bill_print.run_bills(run.name)
		conn_of = dict(frappe.get_all("Utility Bill", filters={"bill_run": run.name}, fields=["name", "connection"], as_list=True))
		self.assertEqual([(conn_of[b.name], b.route, cint(b.position)) for b in bills], [(first, "W1", 1), (self.conn, "W1", 2)])
		self.assertEqual(bill_print.run_print_groups(run.name)["routed"], 2)
		with bill_print.walk_print():  # a run's bulk print shows the walk place
			self.assertIn("Route W1 · 2", bill_print.bill_html(bills[1].name))
		self.assertNotIn("Route W1 · 2", bill_print.bill_html(bills[1].name))  # a bill on its own prints as issued (QA-90)

		covers = bill_print.route_covers(run, bills, 0, len(bills))
		self.assertEqual(list(covers), [0])
		self.assertIn("Bills on this route", covers[0])
		frappe.db.set_value(ROUTE, f"{SOC}-Electricity-W1", "distributor", "Aslam")
		out = bill_print.print_run_bills_to_file(run.name)
		pdf = frappe.get_doc("File", {"file_url": out["file_url"]}).get_content()
		self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 3)  # the cover sheet, then a page per bill
		plain = bill_print.print_run_bills_to_file(run.name, covers=0)
		pdf = frappe.get_doc("File", {"file_url": plain["file_url"]}).get_content()
		self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 2)
		# a part that starts inside a route gets a "continued" sheet
		later = bill_print.route_covers(run, bills, 1, 1)
		self.assertIn("Continued", later[0])
