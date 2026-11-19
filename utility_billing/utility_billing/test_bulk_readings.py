# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""The performance step, part 2: a cycle's reading sheet and a readings file are handled a group of accounts at a time. These tests
hold the grouped way to the one-document-at-a-time way: the same sheet and the same file, done both ways, must leave the same
readings. The migration load in the background is tested here too."""

from unittest.mock import patch

import frappe

from utility_billing.utility_billing import bulk, migration, readcache, readings
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.test_migration import workbook

SKIP = {"name", "creation", "modified", "modified_by", "owner"}


def plain(row):
	return {k: (str(v) if v is not None and not isinstance(v, (int, float, str)) else v) for k, v in row.items() if k not in SKIP}


def csv_file(lines) -> str:
	f = frappe.get_doc({"doctype": "File", "file_name": f"readings-{frappe.generate_hash(length=6)}.csv", "content": "\n".join(lines),
	                    "is_private": 1}).insert()
	return f.file_url


class IntegrationTestReadingsInGroups(BillingCase):
	def setUp(self):
		super().setUp()
		self.conns = [self.conn]
		for i in range(1, 6):
			consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": f"Sheet Consumer {i}"}).insert().name
			prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": f"S {i}", "block": "S"}).insert().name
			net = i == 5  # one solar account: an export register
			meter = frappe.get_doc({"doctype": "Meter", "serial_no": f"TST-SHT-M{i}", "society": SOC, "utility": "Electricity",
			                        "meter_type": "Net Metering" if net else "Single Phase", "registers": 2 if net else 1}).insert().name
			self.conns.append(frappe.get_doc({
				"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumer, "utility": "Electricity",
				"consumer_category": self.cat, "status": "Active", "sanctioned_load_kw": 5,
				"meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 1000 * i, "start_export_reading": 200 if net else 0}]}).insert().name)

	def cycle(self, month):
		name = f"{SOC}-Electricity-{month[:7]}"
		if not frappe.db.exists("Reading Cycle", name):
			frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": month}).insert()
		return name

	def sheet(self, month):
		cycle = self.cycle(month)
		readings.create_reading_sheet(cycle)
		return cycle

	def state(self, cycle):
		return {r.connection: plain(r) for r in frappe.db.sql("select * from `tabMeter Reading` where reading_cycle = %s", cycle, as_dict=True)}

	def lines(self, month, add, extra=()):
		out = ["Reference No,Present Reading,Reading Date,Exception Code,MDI,Export Reading"]
		for i, conn in enumerate(self.conns):
			start = 1999 if i == 0 else 1000 * i
			export = 200 + add // 2 if i == 5 else ""
			out.append(f"{conn},{start + add + i * 11},{month[:8]}05,,{3 + i if i < 3 else ''},{export}")
		return [*out, *extra]

	def both_ways(self, fn):
		frappe.db.savepoint("grouped")
		fast = fn()
		frappe.db.rollback(save_point="grouped")
		with patch.object(bulk, "plain", return_value=False):
			slow = fn()
		return fast, slow

	def test_the_sheet_made_in_groups_is_the_sheet_made_one_by_one(self):
		def make():
			cycle = self.sheet("2026-08-01")
			return self.state(cycle)

		fast, slow = self.both_ways(make)
		self.maxDiff = None
		self.assertEqual(len(fast), 6)
		self.assertEqual(fast, slow)
		first, solar = fast[self.conn], fast[self.conns[5]]
		self.assertEqual((first["status"], first["previous_reading"], first["consumer_name"], first["society"]), ("Pending", 1999, "House N 2308 Owner", SOC))
		self.assertEqual((solar["net_metering"], solar["previous_export_reading"]), (1, 200))

	def test_a_second_month_takes_its_previous_readings_from_the_first(self):
		readings.upload_readings(self.sheet("2026-08-01"), csv_file(self.lines("2026-08-01", 300)))
		readings.close_cycle(self.cycle("2026-08-01"))

		def make():
			return self.state(self.sheet("2026-09-01"))

		fast, slow = self.both_ways(make)
		self.assertEqual(fast, slow)
		self.assertEqual(fast[self.conn]["previous_reading"], 1999 + 300)
		self.assertEqual(fast[self.conns[5]]["previous_export_reading"], 350)

	def test_a_file_applied_in_groups_is_the_file_applied_one_by_one(self):
		# two earlier months give the checks a history; September's file then has a normal row, a high one (flagged), an
		# exception code, an unknown account, an empty reading and an export reading
		for month, add in (("2026-07-01", 300), ("2026-08-01", 600)):
			readings.upload_readings(self.sheet(month), csv_file(self.lines(month, add)))
			readings.close_cycle(self.cycle(month))
		code = frappe.db.get_value("Reading Exception Code", {}, "name")
		file_lines = self.lines("2026-09-01", 900)
		file_lines[2] = f"{self.conns[1]},{1000 + 900 + 11 + 5000},2026-09-05,,,"  # far above its history
		file_lines[3] = f"{self.conns[2]},,2026-09-05,{code},,"  # not read
		file_lines[4] = f"{self.conns[3]},,2026-09-05,,,"  # nothing entered
		file_lines.append("9999999999,5000,2026-09-05,,,")  # not in the cycle

		def apply():
			cycle = self.sheet("2026-09-01")
			result = readings.upload_readings(cycle, csv_file(file_lines))
			c = frappe.get_doc("Reading Cycle", cycle)
			return {"result": {"updated": result["updated"], "errors": [(e["row"], e["reference_no"], e["error"]) for e in result["errors"]]},
			        "readings": self.state(cycle), "counts": (c.total_readings, c.pending_readings, c.flagged_readings, c.valid_readings),
			        "mdi": [frappe.db.get_value("Connection", x, "last_mdi_kw") for x in self.conns[:3]]}

		fast, slow = self.both_ways(apply)
		self.maxDiff = None
		self.assertEqual(fast, slow)
		rows = fast["readings"]
		self.assertEqual(fast["result"]["updated"], 5)
		self.assertEqual([e[:2] for e in fast["result"]["errors"]], [(5, self.conns[3]), (8, "9999999999")])
		self.assertEqual([rows[c]["status"] for c in self.conns], ["Valid", "Flagged", "Pending", "Pending", "Valid", "Valid"])
		self.assertEqual((rows[self.conn]["units"], rows[self.conn]["source"]), (300, "Import"))
		self.assertEqual(rows[self.conns[5]]["export_units"], 150)
		self.assertEqual(fast["mdi"], [3, 4, 5])  # the connected load noted from the files (the third account's from August: it wasn't read in September)

	def test_a_correction_keeps_its_change_history(self):
		cycle = self.sheet("2026-08-01")
		readings.upload_readings(cycle, csv_file(self.lines("2026-08-01", 300)))
		name = frappe.db.get_value("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		# A reading that already has a value is saved as a document, which is what writes its change history; a first entry is
		# written without the save. Frappe switches version rows off inside tests, so the saves themselves are counted here.
		from utility_billing.utility_billing.doctype.meter_reading.meter_reading import MeterReading  # noqa: PLC0415

		saved, real = [], MeterReading.save

		def save(doc, *a, **kw):
			saved.append(doc.name)
			return real(doc, *a, **kw)

		with patch.object(MeterReading, "save", save):
			result = readings.upload_readings(cycle, csv_file([f"Reference No,Present Reading,Reading Date\n{self.conn},2350,2026-08-06"]))
		self.assertEqual(result["updated"], 1)
		self.assertEqual(frappe.db.get_value("Meter Reading", name, ["present_reading", "units"]), (2350, 351))
		self.assertEqual(saved, [name])
		with patch.object(MeterReading, "save", save):  # and the first upload of a month saves no document at all
			readings.upload_readings(self.sheet("2026-09-01"), csv_file(self.lines("2026-09-01", 700)))
		self.assertEqual(saved, [name])

	def test_a_row_given_twice_and_a_date_before_the_last_reading(self):
		cycle = self.sheet("2026-08-01")
		result = readings.upload_readings(cycle, csv_file([
			"Reference No,Present Reading,Reading Date",
			f"{self.conn},2300,2026-08-05", f"{self.conn},2310,2026-08-06",  # the later row of the same account wins
			f"{self.conns[1]},1300,2026-06-01"]))  # before the meter was installed
		self.assertEqual(result["updated"], 2)
		self.assertEqual([(e["row"], e["reference_no"]) for e in result["errors"]], [(4, self.conns[1])])
		self.assertEqual(frappe.db.get_value("Meter Reading", {"reading_cycle": cycle, "connection": self.conn}, "present_reading"), 2310)
		self.assertEqual(frappe.db.get_value("Meter Reading", {"reading_cycle": cycle, "connection": self.conns[1]}, "status"), "Pending")

	def test_outside_a_cycle_nothing_is_cached(self):
		self.assertIsNone(readcache.active())
		self.sheet("2026-08-01")
		self.assertIsNone(readcache.active())
		self.assertEqual(readcache.last_actual(self.conn, "2026-08-01"), (False, None))


class IntegrationTestMigrationInBackground(BillingCase):
	"""A large workbook loads in a background job, a group of accounts at a time, with a commit after each. The commits are held back
	here so the test leaves nothing behind."""

	def batch(self, bad=False):
		for name in ("Residential",):
			if not frappe.db.exists("Consumer Category", f"Electricity-{name}"):
				frappe.get_doc({"doctype": "Consumer Category", "category_name": name, "utility": "Electricity"}).insert()
		profile = frappe.get_doc({"doctype": "Migration Profile", "profile_name": "BG source", "society": SOC, "default_utility": "Electricity"}).insert()
		rows = [["old_account_no", "consumer_name", "house", "block", "category", "meter_no"]]
		rows += [[f"BG-{i}", f"BG Consumer {i}", str(i + 1), "B", "Residential", f"TST-BG-M{i}"] for i in range(7)]
		history = [["old_account_no", "month", "units", "bill_amount"], *[[f"BG-{i}", "2026-05-01", 100 + i, 5000] for i in range(7)]]
		if bad:
			history.append(["BG-0", "2026-04-01", "abc", 1])
		url = workbook({"Connections": rows, "History": history, "Balances": [["old_account_no", "arrears"], ["BG-1", 1500]]})
		return frappe.get_doc({"doctype": "Migration Batch", "society": SOC, "profile": profile.name, "as_of_date": "2026-05-31", "workbook": url}).insert()

	def test_a_background_load_keeps_each_group_and_counts_what_it_made(self):
		b = self.batch()
		b.db_set("load_status", "Loading")
		progress = []
		with patch.object(migration, "CHUNK", 3), patch.object(frappe.db, "commit"), \
				patch.object(migration, "_progress", side_effect=lambda batch, text: progress.append(text)):
			migration.load_in_background(b.name)
		b.reload()
		self.assertEqual(b.load_status, "Loaded")
		self.assertEqual(progress[:3], ["3 of 7 accounts", "6 of 7 accounts", "7 of 7 accounts"])
		self.assertEqual({r.record_type: r.record_count for r in b.records},
		                 {"Meter": 7, "Property": 7, "Consumer": 7, "Connection": 7, "Legacy Bill": 7, "Opening Balance Import": 1})
		self.assertEqual(frappe.db.get_value("Legacy Bill", {"migration_batch": b.name, "units": 103}, ["consumer_name", "society"]), ("BG Consumer 3", SOC))
		migration.undo(b)
		self.assertEqual(sum(frappe.db.count(dt, {"migration_batch": b.name}) for dt in (*migration.STAMPED, "Legacy Bill")), 0)

	def test_a_load_that_stops_part_way_says_so_and_can_be_undone(self):
		b = self.batch()
		b.db_set("load_status", "Loading")
		real = migration._new_account
		calls = []

		def fails_on_the_fifth(batch, ref, r, consumers, properties):
			calls.append(ref)
			if len(calls) == 5:
				raise RuntimeError("the database went away")
			return real(batch, ref, r, consumers, properties)

		with patch.object(migration, "CHUNK", 3), patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"), \
				patch.object(migration, "_new_account", side_effect=fails_on_the_fifth):
			self.assertRaises(RuntimeError, migration.load_in_background, b.name)
		b.reload()
		self.assertEqual(b.load_status, "Load failed")
		self.assertIn("the database went away", b.load_error)
		self.assertEqual(frappe.db.count("Connection", {"migration_batch": b.name}), 4)  # what was made before it stopped
		self.assertRaisesRegex(frappe.ValidationError, "stopped part-way", migration.load, b)
		migration.undo(b)
		self.assertEqual((b.load_status, frappe.db.count("Connection", {"migration_batch": b.name})), ("Undone", 0))

	def test_a_workbook_with_errors_is_not_loaded_in_the_background(self):
		b = self.batch(bad=True)
		b.db_set("load_status", "Loading")
		with patch.object(frappe.db, "commit"):
			migration.load_in_background(b.name)
		b.reload()
		self.assertEqual((b.load_status, frappe.db.count("Connection", {"migration_batch": b.name})), ("Checked", 0))
		self.assertTrue(b.error_count)

	def test_only_a_large_workbook_goes_to_the_background(self):
		b = self.batch()
		self.assertFalse(migration.is_large(b))
		with patch.object(migration, "BACKGROUND_OVER", 5):
			self.assertTrue(migration.is_large(b))
