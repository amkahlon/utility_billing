# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for consumers, properties, network, meters and connections (increment 2)."""

import frappe
from frappe.tests import IntegrationTestCase

from utility_billing.install import create_default_utilities
from utility_billing.utility_billing.reference import is_valid_reference

SOC = "TST-MST"


def ensure(doctype, name, data):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **data}).insert()
	return name


def consumer(name="Test Consumer", cnic=None):
	return frappe.get_doc({"doctype": "Consumer", "consumer_name": name, "id_type": "CNIC", "id_number": cnic}).insert()


def meter(serial, status="In Store"):
	return frappe.get_doc(
		{"doctype": "Meter", "serial_no": serial, "society": SOC, "utility": "Electricity", "status": status}
	).insert()


class IntegrationTestMasters(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		ensure("Society", SOC, {"society_name": "Masters Test Society", "short_name": SOC})
		self.cat = ensure(
			"Consumer Category", "Electricity-TST Domestic", {"category_name": "TST Domestic", "utility": "Electricity"}
		)
		self.owner = consumer("Owner One", "35202-1234567-1")
		self.prop = frappe.get_doc(
			{
				"doctype": "Property",
				"society": SOC,
				"property_no": " N  2308 ",
				"sector": "N",
				"occupants": [
					{"consumer": self.owner.name, "occupant_role": "Owner", "from_date": "2020-01-01",
					 "share_percent": 100, "is_billed": 1, "liable_for_arrears": 1},
				],
			}
		).insert()

	def tearDown(self):
		frappe.db.rollback()

	def connection(self, meters=(), status="Active", **extra):
		return frappe.get_doc(
			{
				"doctype": "Connection",
				"society": SOC,
				"property": self.prop.name,
				"consumer": self.owner.name,
				"utility": "Electricity",
				"consumer_category": self.cat,
				"status": status,
				"meters": list(meters),
				**extra,
			}
		).insert()

	# ----- consumer -----
	def test_cnic_formatted_and_duplicate_refused(self):
		c = consumer("Formatted", "3520298765431")
		self.assertEqual(c.id_number, "35202-9876543-1")
		with self.assertRaises(frappe.ValidationError):
			consumer("Same Person Again", "35202 9876543 1")
		with self.assertRaises(frappe.ValidationError):
			consumer("Too Short", "12345")

	# ----- property -----
	def test_property_id_and_billed_occupant(self):
		self.assertEqual(self.prop.property_no, "N 2308")
		self.assertEqual(self.prop.name, f"{SOC}-N 2308")
		self.assertEqual(self.prop.get_billed_occupant("2026-11-01").consumer, self.owner.name)

	def test_two_billed_occupants_refused(self):
		tenant = consumer("Tenant One")
		self.prop.append("occupants", {"consumer": tenant.name, "occupant_role": "Tenant",
		                               "from_date": "2026-01-01", "is_billed": 1})
		with self.assertRaises(frappe.ValidationError):
			self.prop.save()

	def test_tenant_takes_over_billing(self):
		tenant = consumer("Tenant Two")
		self.prop.occupants[0].to_date = None
		self.prop.occupants[0].is_billed = 0  # owner stays owner, tenant is billed from 2026
		self.prop.append("occupants", {"consumer": tenant.name, "occupant_role": "Tenant",
		                               "from_date": "2026-01-01", "is_billed": 1})
		self.prop.save()
		self.assertEqual(self.prop.get_billed_occupant("2026-06-01").consumer, tenant.name)

	def test_owner_shares_over_100_refused(self):
		co_owner = consumer("Co Owner")
		self.prop.append("occupants", {"consumer": co_owner.name, "occupant_role": "Owner",
		                               "from_date": "2024-01-01", "share_percent": 50})
		with self.assertRaises(frappe.ValidationError):
			self.prop.save()

	# ----- connection -----
	def test_reference_number_is_id_and_valid(self):
		m = meter("TST-M-001")
		c = self.connection([{"meter": m.name, "installed_on": "2026-07-01", "start_reading": 3641}])
		self.assertEqual(c.name, c.reference_no)
		self.assertTrue(is_valid_reference(c.reference_no))
		self.assertTrue(c.reference_no.startswith("1"))
		self.assertEqual(c.current_meter, m.name)
		self.assertEqual(c.connection_title, "N 2308 · Electricity")
		c.reference_no = str(int(c.reference_no) + 10)  # any other number
		with self.assertRaises(frappe.ValidationError):
			c.save()

	def test_meter_status_follows_history(self):
		m = meter("TST-M-002")
		c = self.connection([{"meter": m.name, "installed_on": "2026-07-01", "start_reading": 0}])
		self.assertEqual(frappe.db.get_value("Meter", m.name, ["status", "current_connection"]), ("Installed", c.name))

	def test_meter_change(self):
		old, new = meter("TST-M-003"), meter("TST-M-004")
		c = self.connection([{"meter": old.name, "installed_on": "2026-07-01", "start_reading": 100}])
		c.meters[0].removed_on = "2026-10-15"
		c.meters[0].end_reading = 450
		c.append("meters", {"meter": new.name, "installed_on": "2026-10-15", "start_reading": 0})
		c.save()
		self.assertEqual(c.current_meter, new.name)
		self.assertEqual(frappe.db.get_value("Meter", old.name, ["status", "current_connection"]), ("Removed", None))
		self.assertEqual(frappe.db.get_value("Meter", new.name, "status"), "Installed")

	def test_meter_on_two_connections_refused(self):
		m = meter("TST-M-005")
		self.connection([{"meter": m.name, "installed_on": "2026-07-01"}])
		with self.assertRaises(frappe.ValidationError):
			self.connection([{"meter": m.name, "installed_on": "2026-08-01"}])

	def test_two_open_meters_refused(self):
		a, b = meter("TST-M-006"), meter("TST-M-007")
		with self.assertRaises(frappe.ValidationError):
			self.connection([{"meter": a.name, "installed_on": "2026-07-01"},
			                 {"meter": b.name, "installed_on": "2026-08-01"}])

	def test_active_needs_meter(self):
		with self.assertRaises(frappe.ValidationError):
			self.connection([], status="Active")
		self.assertEqual(self.connection([], status="Applied").current_meter, None)

	def test_category_must_match_utility(self):
		water = ensure("Consumer Category", "Water-TST Domestic", {"category_name": "TST Domestic", "utility": "Water"})
		with self.assertRaises(frappe.ValidationError):
			self.connection([], status="Applied", consumer_category=water)

	def test_transformer_must_be_same_society(self):
		ensure("Society", "TST-OTHER", {"society_name": "Masters Test Other Society", "short_name": "TST-OTHER"})  # society names are unique
		ensure("Feeder", "TST-OTHER-F1", {"society": "TST-OTHER", "feeder_code": "F1", "feeder_name": "Feeder 1"})
		ensure("Transformer", "TST-OTHER-T1", {"society": "TST-OTHER", "transformer_code": "T1", "feeder": "TST-OTHER-F1"})
		with self.assertRaises(frappe.ValidationError):
			self.connection([], status="Applied", transformer="TST-OTHER-T1")

	def test_installed_status_only_through_connection(self):
		with self.assertRaises(frappe.ValidationError):
			meter("TST-M-008", status="Installed")
