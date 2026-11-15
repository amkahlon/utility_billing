# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 23 part B: the meter store (gap 13): received, issued to an installer, installed, returned, scrapped; the stock report."""

import frappe

from utility_billing.utility_billing import mis
from utility_billing.utility_billing.test_billing import SOC, BillingCase


class IntegrationTestMeterStore(BillingCase):
	def move(self, kind, rows, **kw):
		doc = frappe.get_doc({"doctype": "Meter Store Movement", "society": SOC, "utility": "Electricity", "movement_type": kind,
		                      "items": rows, **kw}).insert()
		doc.submit()
		return doc

	def test_meters_from_receipt_to_installation_return_and_scrap(self):
		got = self.move("Received", [{"serial_no": f"TST-STORE-{n}", "make": "Acme", "meter_type": "Single Phase"} for n in (1, 2, 3)],
		                supplier="Acme Meters", reference="PO-77")
		meters = [r.meter for r in got.items]
		self.assertEqual([frappe.db.get_value("Meter", m, ["status", "last_movement"]) for m in meters], [("In Store", got.name)] * 3)
		with self.assertRaises((frappe.ValidationError, frappe.DuplicateEntryError)):
			self.move("Received", [{"serial_no": "tst-store-1"}])  # the number is already in use, however spelt
		issued = self.move("Issued", [{"meter": meters[0]}, {"meter": meters[1]}], issued_to="Installer A")
		self.assertEqual(frappe.db.get_value("Meter", meters[0], ["status", "issued_to"]), ("Issued", "Installer A"))
		with self.assertRaises(frappe.ValidationError):
			self.move("Issued", [{"meter": meters[0]}], issued_to="Installer B")  # already out
		# the installer fits one on a new account
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "STORE-1"}).insert().name
		cons = frappe.db.get_value("Connection", self.conn, "consumer")
		frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": cons, "utility": "Electricity",
		                "consumer_category": self.cat, "status": "Active",
		                "meters": [{"meter": meters[0], "installed_on": "2026-11-01", "start_reading": 0}]}).insert()
		self.assertEqual(frappe.db.get_value("Meter", meters[0], ["status", "issued_to"]), ("Installed", None))
		with self.assertRaises(frappe.ValidationError):
			issued.cancel()  # a meter has moved on since
		# the other comes back faulty and is scrapped
		self.move("Returned", [{"meter": meters[1], "condition": "Defective"}], reason="Display dead")
		self.assertEqual(frappe.db.get_value("Meter", meters[1], "status"), "Defective")
		scrap = self.move("Scrapped", [{"meter": meters[1]}], reason="Beyond repair")
		self.assertEqual(frappe.db.get_value("Meter", meters[1], "status"), "Scrapped")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": cons, "utility": "Electricity",
			                "consumer_category": self.cat, "status": "Active",
			                "meters": [{"meter": meters[1], "installed_on": "2026-11-01", "start_reading": 0}]}).insert()
		scrap.cancel()
		self.assertEqual(frappe.db.get_value("Meter", meters[1], "status"), "Defective")
		# stock on hand
		_c, data, _m, _ch, summary = mis.run("meter_stock", {"society": SOC, "utility": "Electricity"})
		row = next(r for r in data if r["meter_type"] == "Single Phase")
		self.assertEqual((row["In Store"] >= 1, row["Issued"], row["Defective"] >= 1), (True, 0, True))
		_c, moves, *_x = mis.run("meter_movements", {"society": SOC, "from_date": "2026-01-01", "to_date": "2099-12-31"})
		self.assertEqual(sorted({m.movement_type for m in moves}), ["Issued", "Received", "Returned"])  # the scrap was cancelled

	def test_a_receipt_is_undone_while_its_meters_are_still_in_the_store(self):
		got = self.move("Received", [{"serial_no": "TST-STORE-9"}])
		meter = got.items[0].meter
		got.cancel()
		self.assertFalse(frappe.db.exists("Meter", meter))
