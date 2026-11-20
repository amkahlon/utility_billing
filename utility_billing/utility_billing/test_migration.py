# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""The Migration Workbench (increment 29): mapping, check, load, undo, approval, old numbers, history, and the parallel run."""

import datetime
import io

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, nowdate

from utility_billing.install import create_default_utilities
from utility_billing.utility_billing import account, bill_print, consumer360, legacy, migration
from utility_billing.utility_billing.testing import ACCOUNTS_OFFICER, BILLING_MANAGER, GENERAL_MANAGER, act, approve, make_users

SOC = "TST-MIG"
AS_OF = "2026-10-31"
CONN_HEAD = ["Account No", "Customer ID", "consumer_name", "cnic", "mobile", "House No", "utility", "Tariff", "sanctioned_load_kw", "Meter No",
             "meter_type", "plot_area", "plot_unit"]


def workbook(sheets: dict) -> str:
	"""An .xlsx File from {sheet: [header, row, ...]}; returns its file_url."""
	from openpyxl import Workbook  # noqa: PLC0415

	wb = Workbook()
	wb.remove(wb.active)
	for name, rows in sheets.items():
		ws = wb.create_sheet(name)
		for r in rows:
			ws.append(r)
	out = io.BytesIO()
	wb.save(out)
	f = frappe.get_doc({"doctype": "File", "file_name": f"mig-{frappe.generate_hash(length=6)}.xlsx", "content": out.getvalue(), "is_private": 1}).insert()
	return f.file_url


def prof(**kw):
	base = dict(name=None, society=None, columns={}, valuemap={}, pattern=None, dayfirst=True, yearfirst=False, utility=None, category=None)
	return frappe._dict({**base, **kw})


class UnitTestMigrationMapping(UnitTestCase):
	def test_house_numbers_split_by_the_profile_pattern(self):
		r = frappe._dict(house="59-C")
		self.assertEqual(migration.split_house(r, prof(pattern=r"(?P<house>\d+)-?(?P<block>[A-Z][A-Z0-9]*)")), ("59", "C", ""))
		self.assertEqual(migration.split_house(frappe._dict(house="N 2308"), prof(pattern=r"(?P<block>[A-Z]+)\s*(?P<house>\d+)")), ("2308", "N", ""))
		self.assertEqual(migration.split_house(frappe._dict(house="2413 Tulip Overseas Block"), prof(pattern=r"(?P<house>\d+)\s+(?P<block>.+)")),
		                 ("2413", "Tulip Overseas Block", ""))
		self.assertIsNone(migration.split_house(frappe._dict(house="Plot X"), prof(pattern=r"(?P<house>\d+)")))
		self.assertEqual(migration.split_house(frappe._dict(house="12", block="B"), prof()), ("12", "B", ""))
		self.assertEqual(migration.property_no("59", "C"), "C-59")
		self.assertEqual(migration.property_no("59", ""), "59")

	def test_columns_values_numbers_and_dates_are_normalised(self):
		p = prof(columns={"Connections": {"account no": "old_account_no", "tariff": "category"}}, valuemap={"category": {"res": "Residential"}})
		problems = []
		rows = migration.normalise("Connections", [["Account No", "Tariff", "sanctioned_load_kw", "connection_date", "Notes"],
		                                           [7402006006.0, "RES", "1,250", "31-01-2025", "x"],
		                                           [None, None, None, None, None],
		                                           ["7402006007", "Com", "abc", "2025-13-45", ""]], p, problems)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0].old_account_no, "7402006006")
		self.assertEqual(rows[0].sanctioned_load_kw, 1250)
		self.assertEqual(rows[0].connection_date, datetime.date(2025, 1, 31))
		self.assertEqual(migration.mapped(p, "category", rows[0].category), "Residential")
		self.assertEqual(migration.mapped(p, "category", "Com"), "Com")
		self.assertEqual([x["severity"] for x in problems], ["Warning", "Error", "Error"])  # Notes unused; abc; the bad date
		self.assertIn("Notes", problems[0]["problem"])

	def test_mobile_numbers_in_the_usual_forms_pass(self):
		for ok in ("03004917056", "0300-4917056", "3004917056", "+92 300 4917056"):
			self.assertTrue(migration.phone_ok(ok), ok)
		for bad in ("12345", "042-35761234", "0300491705"):
			self.assertFalse(migration.phone_ok(bad), bad)

	def test_dates_follow_the_profile_order(self):
		self.assertEqual(migration.parse_date("03/04/2026", prof(dayfirst=True)), datetime.date(2026, 4, 3))
		self.assertEqual(migration.parse_date("03/04/2026", prof(dayfirst=False)), datetime.date(2026, 3, 4))
		self.assertEqual(migration.parse_date(datetime.datetime(2026, 1, 2, 5), prof()), datetime.date(2026, 1, 2))
		# year first with dashes is year-month-day in every profile (read day first, 2026-09-01 silently became 9 January)
		for p in (prof(dayfirst=True), prof(dayfirst=False)):
			self.assertEqual(migration.parse_date("2026-09-01", p), datetime.date(2026, 9, 1))
			self.assertEqual(migration.parse_date("2026-10-25 00:00:00", p), datetime.date(2026, 10, 25))
		self.assertEqual(migration.parse_date("01-09-2026", prof(dayfirst=True)), datetime.date(2026, 9, 1))


class IntegrationTestMigration(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		make_users()
		if not frappe.db.exists("Society", SOC):
			frappe.get_doc({"doctype": "Society", "society_name": "Migration Test Society", "short_name": SOC}).insert()
		frappe.db.set_value("Society", SOC, {"go_live_date": None, "old_numbers_until": None})
		frappe.clear_document_cache("Society", SOC)
		for name in ("Residential", "Commercial"):
			if not frappe.db.exists("Consumer Category", f"Electricity-{name}"):
				frappe.get_doc({"doctype": "Consumer Category", "category_name": name, "utility": "Electricity"}).insert()
		self.profile = frappe.get_doc({
			"doctype": "Migration Profile", "profile_name": "Test source", "society": SOC, "default_utility": "Electricity",
			"house_pattern": r"(?P<house>\d+)-?(?P<block>[A-Z][A-Z0-9]*)",
			"columns": [{"sheet": "Connections", "source_column": "Account No", "target": "old_account_no"},
			            {"sheet": "Connections", "source_column": "Customer ID", "target": "old_customer_id"},
			            {"sheet": "Connections", "source_column": "House No", "target": "house"},
			            {"sheet": "Connections", "source_column": "Tariff", "target": "category"},
			            {"sheet": "Connections", "source_column": "Meter No", "target": "meter_no"}],
			"value_maps": [{"target": "category", "source_value": "RES", "our_value": "Residential"},
			               {"target": "category", "source_value": "COM", "our_value": "Commercial"},
			               {"target": "meter_type", "source_value": "NM", "our_value": "Net Metering"}],
		}).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def good_book(self):
		return {
			"Connections": [CONN_HEAD,
			                ["MIG-1001", "C-1", "Pervaiz Hussain", "3520212345671", "03004917056", "59-C", "", "RES", 5, "TST-MIG-M1", "", 5, "Marla"],
			                ["MIG-1002", "C-1", "Pervaiz Hussain", "35202-1234567-1", "", "60-C", "", "COM", 10, "TST-MIG-M2", "", 10, "Marla"],
			                ["MIG-1003", "C-3", "Sana Ahmed", "", "", "12-B", "", "RES", 5, "TST-MIG-M3", "NM", "", ""]],
			"Readings": [["old_account_no", "meter_no", "last_reading", "last_export_reading", "reading_date"],
			             ["MIG-1001", "", 1677, "", "25-10-2026"],
			             ["", "TST-MIG-M2", 5320, "", "25-10-2026"],
			             ["MIG-1003", "", 900, 410, "25-10-2026"]],
			"Balances": [["old_account_no", "current_bill", "arrears", "surcharge", "fees", "credit", "security_deposit"],
			             ["MIG-1001", 5355, 1200, 0, 0, 0, 0],
			             ["MIG-1003", 0, 0, 0, 0, 750, 0]],
			"History": [["old_account_no", "month", "units", "current_amount", "arrears", "adjustment", "surcharge", "bill_amount", "paid_on",
			             "paid_amount", "balance"],
			            ["MIG-1001", "01-09-2026", 63, 3130, 0, 0, 0, 3130, "10-10-2026", 2570, 560],
			            ["MIG-1001", "01-10-2026", 70, 4795, 560, 0, 0, 5355, "", 0, 5355],
			            ["MIG-1003", "01-10-2026", 20, 900, 0, 0, 0, 900, "05-10-2026", 900, 0]],
			"Feeders": [["code", "name"], ["MF1", "Main feeder"]],
			"Transformers": [["code", "feeder", "kva", "location"], ["MT1", "MF1", 200, "C Block park"]],
		}

	# ---- increment 32: feeders and transformers on sheets of their own; a plot size without a unit
	def test_feeders_and_transformers_have_sheets_of_their_own(self):
		b = self.loaded()
		self.assertEqual(frappe.db.get_value("Transformer", f"{SOC}-MT1", ["feeder", "kva_rating", "location"]), (f"{SOC}-MF1", 200, "C Block park"))
		self.assertEqual(frappe.db.get_value("Feeder", f"{SOC}-MF1", "feeder_name"), "Main feeder")
		self.assertEqual(frappe.db.count("Transformer", {"migration_batch": b.name}), 1)
		# problems are reported on the sheet they are on: a code twice, a transformer without its feeder or on an unknown one
		book = self.good_book()
		book["Feeders"] += [["MF1", "Again"]]
		book["Transformers"] += [["MT2", "", 100, ""], ["MT3", "NOPE", 100, ""], [None, "MF1", 50, ""]]
		b2 = self.batch(book)
		migration.check(b2)
		errors = {(p.sheet, p.row_no, p.problem) for p in b2.problems if p.severity == "Error"}
		self.assertIn(("Feeders", 3, "Feeder MF1 is in the file twice."), errors)
		self.assertIn(("Transformers", 3, "Transformer MT2 needs its feeder."), errors)
		self.assertIn(("Transformers", 4, "Feeder NOPE of transformer MT3 is neither in the Feeders sheet nor in the system."), errors)
		self.assertIn(("Transformers", 5, "The transformer code is missing."), errors)

	def test_an_older_network_sheet_is_still_read(self):
		book = self.good_book()
		del book["Feeders"], book["Transformers"]
		book["Network"] = [["kind", "code", "name", "feeder", "kva"], ["Feeder", "MF1", "Main feeder", "", ""], ["Transformer", "MT1", "", "MF1", 200]]
		b = self.batch(book)
		self.assertTrue(migration.load(b), [p.problem for p in b.problems if p.severity == "Error"])
		self.assertEqual(frappe.db.get_value("Transformer", f"{SOC}-MT1", "feeder"), f"{SOC}-MF1")

	def test_a_plot_size_without_a_unit_takes_the_profile_unit(self):
		book = self.good_book()
		book["Connections"][1][12] = ""  # MIG-1001: 5, no unit (LESCO has none)
		b = self.batch(book)
		migration.check(b)
		self.assertIn("Plot size 5 has no unit: set a Default Plot Unit in the profile.", [p.problem for p in b.problems if p.severity == "Warning"])
		self.profile.default_plot_unit = "Marla"
		self.profile.save()
		b = self.batch(book)
		self.assertTrue(migration.load(b), [p.problem for p in b.problems if p.severity == "Error"])
		prop = frappe.db.get_value("Connection", self.conn("MIG-1001"), "property")
		self.assertEqual(frappe.db.get_value("Property", prop, ["area", "area_unit"]), (5, "Marla"))

	def batch(self, book, **kw):
		return frappe.get_doc({"doctype": "Migration Batch", "society": SOC, "profile": self.profile.name, "as_of_date": AS_OF,
		                       "workbook": workbook(book), **kw}).insert()

	def loaded(self):
		b = self.batch(self.good_book(), control_accounts=3, control_receivable=5805)
		self.assertTrue(migration.load(b), [p.problem for p in b.problems if p.severity == "Error"])
		b.save()
		return b

	def conn(self, old):
		return frappe.db.get_value("Connection", {"legacy_account_no": old})

	# ---- increment 31: data the previous system doesn't have, its numbers kept, accounts found by consumer ID
	def test_a_missing_house_and_block_take_the_profile_defaults(self):
		book = self.good_book()
		book["Connections"][1][5] = ""  # MIG-1001 without a house number
		book["Connections"][2][5] = "60"  # MIG-1002 with a house but no block
		self.profile.house_pattern = r"(?P<house>\d+)-?(?P<block>[A-Z][A-Z0-9]*)?"
		self.profile.save()
		b = self.batch(book)
		migration.check(b)
		self.assertIn("House number (none) doesn't match the profile's pattern", "\n".join(p.problem for p in b.problems if p.severity == "Error"))
		self.profile.update({"missing_house": "Use the old account number", "default_block": "Gulberg"})
		self.profile.save()
		b = self.batch(book)
		self.assertTrue(migration.load(b), [p.problem for p in b.problems if p.severity == "Error"])
		prop = lambda old: frappe.db.get_value("Property", frappe.db.get_value("Connection", self.conn(old), "property"), ["property_no", "block"])  # noqa: E731
		self.assertEqual(prop("MIG-1001"), ("Gulberg-MIG-1001", "Gulberg"))  # a property of its own, numbered by the old account number
		self.assertEqual(prop("MIG-1002"), ("Gulberg-60", "Gulberg"))  # its own house, the default block
		self.assertEqual(prop("MIG-1003"), ("B-12", "B"))  # a row that has both keeps them

	def test_the_old_number_can_stay_the_reference_number(self):
		self.profile.keep_old_reference = 1
		self.profile.save()
		book = self.good_book()
		lesco = {"MIG-1001": "07111231234567", "MIG-1002": "07111231234568", "MIG-1003": "07111231234569"}  # 14 digits, as on a DISCO bill
		for sheet in book.values():
			for row in sheet[1:]:
				row[0] = lesco.get(row[0], row[0])
		b = self.batch(book, control_accounts=3, control_receivable=5805)
		self.assertTrue(migration.load(b), [p.problem for p in b.problems if p.severity == "Error"])
		c1 = frappe.get_doc("Connection", "07111231234567")  # the old number is the account's ID and reference number
		self.assertEqual((c1.reference_no, c1.legacy_account_no), ("07111231234567", "07111231234567"))
		self.assertEqual(frappe.get_doc("Opening Balance Import", b.opening_import).rows[0].reference_no, "07111231234567")
		bill = frappe._dict(society=SOC, connection=c1.name, billing_month="2026-11-01", issue_date="2026-11-05", consumer_name="Pervaiz Hussain")
		snap = bill_print.build_snapshot(bill)["connection"]
		self.assertEqual((snap["reference_no"], snap["legacy_account_no"]), ("07111231234567", None))  # not printed twice
		legacy.set_window(SOC, add_days(nowdate(), -200))  # the six months are over: the number still works, it is the reference now
		self.assertEqual(account.connection_of("07111231234567"), c1.name)
		# a number that can't serve as a reference, and one already taken on the site, are refused before anything is loaded
		bad = self.good_book()
		bad["Connections"] = [CONN_HEAD, ["A/1", "", "Slash", "", "", "70-C", "", "RES", 5, "TST-MIG-M7", "", 5, "Marla"],
		                      ["07111231234568", "", "Taken", "", "", "71-C", "", "RES", 5, "TST-MIG-M8", "", 5, "Marla"]]
		bad["Readings"], bad["Balances"], bad["History"] = bad["Readings"][:1], bad["Balances"][:1], bad["History"][:1]
		frappe.db.set_value("Connection", "07111231234568", "legacy_account_no", None)  # as if it came from another society
		b2 = self.batch(bad)
		migration.check(b2)
		text = "\n".join(p.problem for p in b2.problems if p.severity == "Error")
		self.assertIn("A/1 can't be kept as the reference number", text)
		self.assertIn("07111231234568 is already the reference number of an account", text)

	def test_our_own_numbers_skip_a_kept_old_number(self):
		from utility_billing.utility_billing.reference import make_reference  # noqa: PLC0415

		b = self.loaded()
		c1 = frappe.get_doc("Connection", self.conn("MIG-1001"))
		code = int(frappe.db.get_value("Utility Type", "Electricity", "reference_code"))
		current = frappe.db.sql("select current from `tabSeries` where name = %s", f"UB-REF-{code}")[0][0]
		coming = make_reference(code, int(current) + 1)  # the number our series would hand out next
		frappe.rename_doc("Connection", self.conn("MIG-1003"), coming, force=True)  # as if a migrated account had kept it
		self.assertTrue(frappe.db.exists("Connection", coming))
		new = c1.new_reference_no()
		self.assertNotEqual(new, coming)
		self.assertEqual(new, make_reference(code, int(current) + 2))
		self.assertEqual(b.load_status, "Loaded")

	def test_an_account_is_found_by_its_consumer_id(self):
		from utility_billing.utility_billing import cashdesk  # noqa: PLC0415

		self.loaded()
		c1, c2, c3 = (self.conn(x) for x in ("MIG-1001", "MIG-1002", "MIG-1003"))
		one, two = (frappe.db.get_value("Connection", c, "consumer") for c in (c3, c1))
		# a consumer with one account: our consumer ID and the previous system's customer ID both find it
		self.assertEqual(account.connection_of(one), c3)
		self.assertEqual(account.connection_of("C-3"), c3)
		self.assertEqual(account.enquiry("C-3")["connection"], c3)
		self.assertEqual(cashdesk.lookup("C-3")["connection"], c3)
		# a consumer with two accounts: not guessed; the counter offers both, a channel is told to use the reference number
		self.assertIsNone(account.connection_of("C-1"))
		with self.assertRaises(frappe.ValidationError) as cm:
			account.enquiry("C-1")
		self.assertIn("has 2 accounts", str(cm.exception))
		pick = cashdesk.lookup(two)
		self.assertEqual([r["reference"] for r in pick["choose"]], sorted([c1, c2]))
		self.assertEqual(pick["consumer_name"], "Pervaiz Hussain")
		self.assertEqual(cashdesk.lookup(pick["choose"][0]["reference"])["connection"], pick["choose"][0]["reference"])
		# a closed account doesn't count: the consumer then has one open account
		frappe.db.set_value("Connection", c2, "status", "Closed")
		self.assertEqual(account.connection_of("C-1"), c1)
		# the previous system's customer ID stops with the old numbers; our own consumer ID always works
		legacy.set_window(SOC, add_days(nowdate(), -200))
		self.assertIsNone(account.connection_of("C-3"))
		self.assertEqual(account.connection_of(one), c3)
		with self.assertRaises(frappe.ValidationError) as cm:
			account.enquiry("NO-SUCH-ID")
		self.assertIn("No account with reference number or consumer ID", str(cm.exception))

	# ---- check
	def test_check_lists_every_problem_and_writes_nothing(self):
		book = self.good_book()
		c = book["Connections"]
		c.append(["MIG-1001", "", "Duplicate", "", "", "61-C", "", "RES", 5, "TST-MIG-M9", "", 5, "Marla"])
		c.append(["MIG-1004", "", "", "", "", "62-C", "", "XYZ", 5, "TST-MIG-M1", "", 5, "Marla"])
		c.append(["MIG-1005", "C-5", "Other Name", "3520212345671", "12345", "Plot", "", "RES", 5, "", "", 5, "Marla"])
		book["Readings"].append(["MIG-1003", "", 950, "", "05-11-2026"])
		book["Readings"][3][3] = ""  # the net-metering meter without its export reading
		book["Balances"].append(["MIG-9999", 10, 0, 0, 0, 0, 0])
		book["Balances"].append(["MIG-1002", 100, 0, 0, 0, 50, 0])
		book["History"].append(["MIG-1002", "01-11-2026", 1, 1, 0, 0, 0, 1, "", 0, 0])
		before = frappe.db.count("Connection")
		b = self.batch(book)
		errors = migration.check(b)
		text = "\n".join(p.problem for p in b.problems if p.severity == "Error")
		for expected in ("MIG-1001 is in the file twice", "name is missing", "Category XYZ", "Meter TST-MIG-M1 is on more than one account",
		                 "doesn't match the profile's pattern", "needs a meter number", "after the cut-over day", "needs its last export reading",
		                 "No account in the Connections sheet for this balance", "either owes or is in credit", "Nov 2026 is after the cut-over day"):
			self.assertIn(expected, text)
		self.assertEqual(errors, b.error_count)
		warnings = "\n".join(p.problem for p in b.problems if p.severity == "Warning")
		self.assertIn("belongs to different names", warnings)
		self.assertIn("Mobile number 12345 doesn't look like one", warnings)
		self.assertNotIn("03004917056", warnings)
		self.assertEqual(b.load_status, "Checked")
		self.assertEqual(frappe.db.count("Connection"), before)
		self.assertFalse(migration.load(b))  # errors stop the load
		self.assertEqual(frappe.db.count("Connection"), before)

	def test_reconciliation_against_the_control_figures(self):
		b = self.batch(self.good_book(), control_accounts=4, control_receivable=6000)
		self.assertEqual(migration.check(b), 0)
		self.assertEqual((b.account_count, b.receivable_total), (3, 5805))  # 5,355 + 1,200 − 750
		self.assertEqual((b.accounts_difference, b.receivable_difference), (-1, -195))
		blocks = {(r["block"], r["category"]): r for r in frappe.parse_json(b.reconciliation)}
		self.assertEqual(blocks[("C", "Electricity-Residential")]["receivable"], 6555)

	# ---- load
	def test_load_creates_the_accounts_meters_history_and_opening_balances(self):
		b = self.loaded()
		self.assertEqual(b.load_status, "Loaded")
		c1, c2, c3 = (frappe.get_doc("Connection", self.conn(x)) for x in ("MIG-1001", "MIG-1002", "MIG-1003"))
		self.assertEqual(c1.consumer, c2.consumer)  # same customer ID and CNIC: one consumer with two accounts
		self.assertEqual(frappe.db.get_value("Consumer", c1.consumer, ["id_number", "legacy_customer_id"]), ("35202-1234567-1", "C-1"))
		self.assertEqual(frappe.db.get_value("Property", c1.property, ["property_no", "block", "area", "area_unit"]), ("C-59", "C", 5, "Marla"))
		self.assertEqual((c1.meters[0].start_reading, str(c1.meters[0].installed_on)), (1677, "2026-10-25"))
		self.assertEqual(c2.meters[0].start_reading, 5320)  # matched by meter number
		self.assertEqual((c3.meters[0].start_reading, c3.meters[0].start_export_reading), (900, 410))
		self.assertEqual(frappe.db.get_value("Meter", "TST-MIG-M3", "meter_type"), "Net Metering")
		self.assertEqual(c2.consumer_category, "Electricity-Commercial")
		self.assertEqual(frappe.db.count("Legacy Bill", {"migration_batch": b.name}), 3)
		self.assertTrue(frappe.db.exists("Transformer", f"{SOC}-MT1"))
		imp = frappe.get_doc("Opening Balance Import", b.opening_import)
		self.assertEqual((imp.row_count, imp.error_count, imp.total_receivable), (2, 0, 5805))
		# what the batch made, counted from the records that carry it (they are no longer logged one by one on the batch)
		self.assertEqual({r.record_type: r.record_count for r in b.records}, {
			"Feeder": 1, "Transformer": 1, "Meter": 3, "Property": 3, "Consumer": 2, "Connection": 3, "Legacy Bill": 3, "Opening Balance Import": 1})
		self.assertEqual(frappe.db.count("Connection", {"migration_batch": b.name}), 3)

	def test_old_numbers_work_until_the_window_closes(self):
		self.loaded()
		c1 = self.conn("MIG-1001")
		self.assertEqual(account.connection_of("MIG-1001"), c1)
		self.assertIn(c1, [r["account"] for r in consumer360.search("MIG-1001")])
		legacy.set_window(SOC, add_days(nowdate(), -200))  # went live 200 days ago: the six months are over
		self.assertIsNone(account.connection_of("MIG-1001"))
		self.assertEqual(account.connection_of(c1), c1)  # our own number always works

	def test_the_bill_prints_the_old_number_while_it_is_accepted(self):
		self.loaded()
		c1 = self.conn("MIG-1001")
		bill = frappe._dict(society=SOC, connection=c1, billing_month="2026-11-01", issue_date="2026-11-05", consumer_name="Pervaiz Hussain")
		self.assertEqual(bill_print.build_snapshot(bill)["connection"]["legacy_account_no"], "MIG-1001")
		legacy.set_window(SOC, "2026-01-01")  # accepted until 1 July 2026: a November bill no longer shows it
		self.assertIsNone(bill_print.build_snapshot(bill)["connection"]["legacy_account_no"])

	def test_the_first_bill_shows_the_months_from_the_previous_system(self):
		self.loaded()
		rows = bill_print.bill_history(self.conn("MIG-1001"), "2026-11-01", 12)
		self.assertEqual([(r["month"][:7], r["units"]) for r in rows], [("2026-10", 70), ("2026-09", 63)])

	def test_a_loaded_workbook_can_be_undone(self):
		before = {dt: frappe.db.count(dt) for dt in ("Connection", "Meter", "Consumer", "Property", "Legacy Bill", "Feeder", "Opening Balance Import")}
		b = self.loaded()
		migration.undo(b)
		b.save()
		self.assertEqual(b.load_status, "Undone")
		self.assertEqual({dt: frappe.db.count(dt) for dt in before}, before)
		self.assertFalse(b.records)

	def test_undo_is_refused_once_the_opening_balances_are_posted(self):
		b = self.loaded()
		frappe.db.set_value("Opening Balance Import", b.opening_import, "docstatus", 1)
		self.assertRaisesRegex(frappe.ValidationError, "posted", migration.undo, b)

	def test_a_second_load_of_the_same_accounts_is_refused(self):
		self.loaded()
		b = self.batch(self.good_book())
		self.assertFalse(migration.load(b))
		self.assertIn("already in the system", "\n".join(p.problem for p in b.problems))

	# ---- approval
	def test_approval_by_another_person_closes_the_batch(self):
		b = self.loaded()
		b.reload()
		approve(b, maker=ACCOUNTS_OFFICER, approver=GENERAL_MANAGER)
		self.assertEqual((b.docstatus, b.workflow_state), (1, "Approved"))
		from utility_billing.utility_billing.doctype.migration_batch import migration_batch  # noqa: PLC0415

		self.assertRaises(frappe.ValidationError, migration_batch.undo, b.name)

	def test_an_unloaded_batch_cannot_go_for_approval(self):
		b = self.batch(self.good_book())
		self.assertRaises(frappe.ValidationError, act, b, "Submit for Approval", BILLING_MANAGER)

	# ---- profile
	def test_the_profile_refuses_unknown_columns_and_bad_patterns(self):
		p = frappe.get_doc("Migration Profile", self.profile.name)
		p.append("columns", {"sheet": "Readings", "source_column": "X", "target": "colour"})
		self.assertRaisesRegex(frappe.ValidationError, "isn't a column", p.save)
		p.reload()
		p.house_pattern = r"(\d+)"
		self.assertRaisesRegex(frappe.ValidationError, "named house", p.save)

	def test_sap_isu_columns_are_added_once(self):
		from utility_billing.utility_billing.doctype.migration_profile import migration_profile  # noqa: PLC0415

		n = migration_profile.fill_sap_isu(self.profile.name)
		self.assertEqual(n, len(migration.SAP_ISU))
		self.assertEqual(migration_profile.fill_sap_isu(self.profile.name), 0)

	def test_the_template_workbook_has_every_sheet(self):
		from openpyxl import load_workbook  # noqa: PLC0415

		wb = load_workbook(io.BytesIO(migration.template_workbook()))
		# every sheet but the older Network layout, which is still read but no longer offered (increment 32)
		self.assertEqual(wb.sheetnames, [s for s in migration.TARGETS if s != "Network"])
		self.assertEqual([c.value for c in wb["Transformers"][1]], ["code", "feeder", "kva", "location"])
		self.assertEqual([c.value for c in wb["Readings"][1]], migration.TARGETS["Readings"])

	# ---- the parallel run
	def parallel(self):
		self.loaded()
		c1, c3 = self.conn("MIG-1001"), self.conn("MIG-1003")
		run = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": "2026-11-01", "issue_date": "2026-11-05"})
		run.name = f"TST-MIG-RUN-{frappe.generate_hash(length=4)}"
		run.db_insert()
		for conn, units, cur, arr, sur in ((c1, 70, 3400, 560, 0), (c3, 20, 800, 0, 0)):
			bill = frappe.get_doc({"doctype": "Utility Bill", "connection": conn, "society": SOC, "utility": "Electricity", "billing_month": "2026-11-01",
			                       "bill_run": run.name, "units": units, "current_amount": cur, "arrears": arr, "surcharge_amount": sur,
			                       "payable_within_due": cur + arr + sur})
			bill.name = f"TST-MIG-BILL-{frappe.generate_hash(length=5)}"
			bill.db_insert()
		return frappe.get_doc({"doctype": "Parallel Run Month", "society": SOC, "billing_month": "2026-11-15", "bill_run": run.name}).insert()

	def test_the_parallel_month_compares_account_by_account(self):
		prm = self.parallel()
		prm.workbook = workbook({"Old Bills": [migration.TARGETS["Old Bills"],
		                                      ["MIG-1001", 70, 3400, 560, 0, 3960, ""],
		                                      ["MIG-1003", 25, 1000, 0, 0, 1000, ""],
		                                      ["MIG-1002", 5, 200, 0, 0, 200, ""]]})
		migration.compare(prm)
		res = {r.old_account_no or r.connection: r for r in prm.rows}
		self.assertEqual(res["MIG-1001"].result, "Match")
		self.assertEqual((res["MIG-1003"].result, res["MIG-1003"].difference), ("Different", -200))
		self.assertIn("units: theirs 25, ours 20", res["MIG-1003"].explanation)
		self.assertIn("current bill: ours is", res["MIG-1003"].explanation)
		self.assertEqual(res["MIG-1002"].result, "Not in our run")
		self.assertEqual((prm.accounts_compared, prm.accounts_matched, prm.comparison_status), (3, 1, "Compared"))

	def test_old_system_payments_are_recorded_once(self):
		prm = self.parallel()
		prm.workbook = workbook({"Payments": [migration.TARGETS["Payments"],
		                                     ["MIG-1001", "12-11-2026", 3960, "TXN-1", "Bank Transfer"],
		                                     ["MIG-1001", "12-11-2026", 3960, "TXN-1", "Bank Transfer"],
		                                     ["MIG-7777", "12-11-2026", 100, "TXN-2", ""]]})
		out = migration.load_payments(prm)
		self.assertEqual(out, {"recorded": 1, "skipped": 1})
		self.assertEqual(frappe.db.get_value("Payment Receipt", {"channel_txn_id": "TXN-1", "docstatus": 1}, "amount"), 3960)
		self.assertEqual(migration.load_payments(prm), {"recorded": 1, "skipped": 1})  # loading the same sheet again adds nothing
		self.assertEqual(frappe.db.count("Payment Receipt", {"channel_txn_id": "TXN-1"}), 1)
		self.assertEqual([p.result for p in prm.payments], ["Recorded", "Unknown account"])
