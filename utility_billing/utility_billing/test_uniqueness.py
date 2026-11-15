# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Duplicate numbers and codes are refused at entry (user, 2026-11-15), however they are spelt."""

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from utility_billing import hooks
from utility_billing.install import create_default_utilities
from utility_billing.utility_billing import uniqueness

SOC, SOC2 = "TST-UNQ", "TST-UNQ2"


class UnitTestUniquenessRules(UnitTestCase):
	def test_values_are_compared_as_people_mistype_them(self):
		self.assertEqual(uniqueness.norm("TST-RA-M1"), uniqueness.norm("tst ra m1"))
		self.assertEqual(uniqueness.norm("N-2308"), uniqueness.norm(" n/2308 "))
		self.assertNotEqual(uniqueness.norm("00123A"), uniqueness.norm("123A"))  # a code keeps its zeros
		self.assertEqual(uniqueness.norm("00123-A", "serial"), uniqueness.norm("123A", "serial"))  # a meter number doesn't
		self.assertEqual((uniqueness.norm(""), uniqueness.norm(None), uniqueness.norm("000", "serial")), ("", "", "0"))

	def test_every_checked_doctype_is_hooked(self):
		self.assertEqual(sorted(hooks.UNIQUE_CHECKED), uniqueness.DOCTYPES)
		for dt in uniqueness.DOCTYPES:
			self.assertEqual(hooks.doc_events[dt]["validate"], "utility_billing.utility_billing.uniqueness.validate", dt)


class IntegrationTestUniqueness(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		for s in (SOC, SOC2):
			if not frappe.db.exists("Society", s):
				frappe.get_doc({"doctype": "Society", "society_name": f"Uniqueness Test {s}", "short_name": s}).insert()

	def tearDown(self):
		frappe.db.rollback()

	def meter(self, serial):
		return frappe.get_doc({"doctype": "Meter", "serial_no": serial, "society": SOC, "utility": "Electricity"}).insert()

	def test_a_meter_number_is_refused_however_it_is_spelt(self):
		m = self.meter("TST-UNQ-M1")
		for again in ("tst unq m1", "TSTUNQM1", "tst-unq-m1", "0TST UNQ M1"):
			with self.assertRaises(frappe.DuplicateEntryError, msg=again) as e:
				self.meter(again)
			self.assertIn("TST-UNQ-M1", str(e.exception))
		self.meter("TST-UNQ-M2")
		m.seal_no = "S-100"
		m.save()
		other = self.meter("TST-UNQ-M3")
		other.seal_no = "s100"
		self.assertRaises(frappe.DuplicateEntryError, other.save)

	def test_check_meters_share_the_meter_numbers(self):
		self.meter("TST-UNQ-CM1")
		f = frappe.get_doc({"doctype": "Feeder", "society": SOC, "feeder_code": "UF1", "feeder_name": "Feeder 1", "check_meter_no": "tst unq cm1"})
		self.assertRaises(frappe.DuplicateEntryError, f.insert)
		f.check_meter_no = "TST-UNQ-CM2"
		f.insert()
		self.assertRaises(frappe.DuplicateEntryError, self.meter, "TSTUNQCM2")

	def test_codes_are_unique_within_their_society_only(self):
		frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N-2308"}).insert()
		self.assertRaises(frappe.DuplicateEntryError, frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "n 2308"}).insert)
		frappe.get_doc({"doctype": "Property", "society": SOC2, "property_no": "N 2308"}).insert()  # another society may use it
		frappe.get_doc({"doctype": "Feeder", "society": SOC, "feeder_code": "F-9", "feeder_name": "Nine"}).insert()
		self.assertRaises(frappe.DuplicateEntryError, frappe.get_doc({"doctype": "Feeder", "society": SOC, "feeder_code": "f9", "feeder_name": "Nine again"}).insert)

	def test_consumer_numbers_and_society_names(self):
		frappe.get_doc({"doctype": "Consumer", "consumer_name": "Unq Co", "consumer_type": "Company", "tax_no": "1234567-8"}).insert()
		c = frappe.get_doc({"doctype": "Consumer", "consumer_name": "Unq Co Again", "consumer_type": "Company", "tax_no": "12345678"})
		self.assertRaises(frappe.DuplicateEntryError, c.insert)
		self.assertRaises(frappe.DuplicateEntryError,
		                  frappe.get_doc({"doctype": "Society", "society_name": "uniqueness test tst-unq", "short_name": "TST-UNQ3"}).insert)

	def test_an_old_clash_doesnt_block_other_edits_and_is_reported(self):
		a = self.meter("TST-UNQ-OLD")
		b = self.meter("TST-UNQ-OTHER")
		frappe.db.set_value("Meter", b.name, "seal_no", "SEAL-7")
		frappe.db.set_value("Meter", a.name, "seal_no", "seal 7")  # entered before the check existed
		b.reload()
		b.make = "Another make"
		b.save()  # the seal number didn't change: the old clash doesn't block the edit
		self.assertIn({"label": "Seal number", "value": "SEAL7", "records": sorted([f"Meter {a.name}", f"Meter {b.name}"])},
		              [dict(r, records=sorted(r["records"])) for r in uniqueness.report()])
		b.seal_no = "SEAL 7"
		b.save()  # the same value, spelt differently: still not a change
		b.seal_no = "SEAL-8"
		b.save()

	def test_the_check_is_one_lookup_in_unique_key_and_keeps_up(self):
		m = self.meter("TST-UNQ-K1")
		keys = frappe.get_all("Unique Key", filters={"ref_doctype": "Meter", "ref_name": m.name}, fields=["key_group", "key_value"])
		self.assertEqual(keys, [{"key_group": "Meter number", "key_value": "TSTUNQK1"}])
		m.seal_no = "SEAL-K1"
		m.save()
		m.seal_no = "SEAL-K2"
		m.save()
		other = self.meter("TST-UNQ-K9")
		other.seal_no = "seal k1"
		other.save()  # the first seal number is free again
		other.seal_no = "SEALK2"
		self.assertRaises(frappe.DuplicateEntryError, other.save)
		frappe.rename_doc("Meter", m.name, "TST-UNQ-K2", force=True)  # a meter number is changed by renaming the meter
		self.meter("tst unq k1")  # the old number is free again
		self.assertRaises(frappe.DuplicateEntryError, self.meter, "tstunqk2")
		frappe.delete_doc("Meter", "TST-UNQ-K2")
		self.meter("TST-UNQ-K2 ")  # deleted: free again
		# a record removed outside the app leaves a stale key, which doesn't block anyone and is cleared
		gone = self.meter("TST-UNQ-K3")
		frappe.db.sql("delete from `tabMeter` where name = %s", gone.name)
		self.meter("TST-UNQ-K3 ")
		uniqueness.rebuild_keys()
		self.assertTrue(frappe.db.exists("Unique Key", {"key_group": "Meter number", "key_value": "TSTUNQK3"}))
