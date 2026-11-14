# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests of the tax rules and part-month billing (increment 14; FS-CFG-14, FS-BIL-07, FS-BIL-01). No database.
Test case 1 (house N 2308, October): energy 264 x 70 = 18,480, FPA 1,080, service 1,700, aquifer 1,000: current bill 22,260."""

from datetime import date
from decimal import Decimal

from frappe.tests import UnitTestCase

from utility_billing.utility_billing import bill_engine as be
from utility_billing.utility_billing import prorata
from utility_billing.utility_billing import tax_engine as te

LINES = [("TST Energy", "Electricity", 18_480), ("TST FPA", "Electricity", 1_080), ("TST Service", "Society", 1_700), ("TST Aquifer", "Society", 1_000)]


def tax(code, rate, **kw):
	return te.TaxSpec(code=code, name=code, rate=Decimal(str(rate)), **kw)


def amounts(lines):
	return {t.code: int(t.amount) for t in lines}


class UnitTestTaxEngine(UnitTestCase):
	def test_no_tax_is_the_default(self):
		self.assertEqual(te.calculate_taxes(LINES, []), [])
		self.assertEqual(te.added([]), 0)

	def test_a_tax_on_every_charge(self):
		self.assertEqual(amounts(te.calculate_taxes(LINES, [tax("GST", 10)])), {"GST": 2_226})

	def test_a_tax_on_some_charge_groups_or_types(self):
		self.assertEqual(amounts(te.calculate_taxes(LINES, [tax("SOC", 5, applies_to=te.GROUPS, groups=("Society",))])), {"SOC": 135})  # 5% of 2,700
		self.assertEqual(amounts(te.calculate_taxes(LINES, [tax("AQ", 10, applies_to=te.TYPES, charge_types=("TST Aquifer",))])), {"AQ": 100})
		self.assertEqual(amounts(te.calculate_taxes(LINES, [tax("NONE", 10, applies_to=te.GROUPS, groups=("Gas",))])), {"NONE": 0})

	def test_an_included_tax_is_carved_out_and_adds_nothing(self):
		(t,) = te.calculate_taxes(LINES, [tax("VAT", 17, inclusive=True)])
		self.assertEqual((t.amount, t.inclusive), (Decimal(3_234), True))  # 22,260 x 17 / 117
		self.assertEqual(te.added([t]), 0)

	def test_tax_on_tax_in_calculation_order(self):
		specs = [tax("B", 5, order=20, on_taxes=("A",)), tax("A", 10, order=10)]  # listed out of order on purpose
		out = te.calculate_taxes(LINES, specs)
		self.assertEqual([t.code for t in out], ["A", "B"])
		self.assertEqual(amounts(out), {"A": 2_226, "B": 1_224})  # 5% of 22,260 + 2,226 = 24,486
		self.assertEqual(te.added(out), 3_450)

	def test_a_tax_on_tax_does_not_pick_up_an_included_tax(self):
		out = te.calculate_taxes(LINES, [tax("INC", 17, inclusive=True, order=10), tax("ADD", 10, order=20, on_taxes=("INC",))])
		self.assertEqual(amounts(out), {"INC": 3_234, "ADD": 2_226})

	def test_a_tax_on_a_tax_that_comes_later_is_ignored(self):
		out = te.calculate_taxes(LINES, [tax("A", 10, order=10, on_taxes=("B",)), tax("B", 5, order=20)])
		self.assertEqual(amounts(out), {"A": 2_226, "B": 1_113})

	def test_zero_rate_or_zero_base_gives_zero(self):
		self.assertEqual(amounts(te.calculate_taxes(LINES, [tax("Z", 0)])), {"Z": 0})
		self.assertEqual(amounts(te.calculate_taxes([("TST Energy", "Electricity", 0)], [tax("Z", 10)])), {"Z": 0})

	def test_rounding_follows_the_taxs_own_rule(self):
		lines = [("c", "Other", 1_005)]
		self.assertEqual(te.calculate_taxes(lines, [tax("N", 7.5)])[0].amount, Decimal(75))  # 75.375 to the nearest rupee
		self.assertEqual(te.calculate_taxes(lines, [tax("U", 7.5, rounding="Round up")])[0].amount, Decimal(76))
		self.assertEqual(te.calculate_taxes(lines, [tax("X", 7.5, rounding="No rounding")])[0].amount, Decimal("75.38"))


class UnitTestPartMonths(UnitTestCase):
	def test_days_connected_count_both_ends(self):
		self.assertEqual(prorata.active_days("2026-11-15", connected_on="2026-11-11"), (20, 30))
		self.assertEqual(prorata.active_days("2026-11-15", disconnected_on="2026-11-10"), (10, 30))
		self.assertEqual(prorata.active_days("2026-11-15", "2026-11-11", "2026-11-20"), (10, 30))
		self.assertEqual(prorata.active_days("2026-11-15", "2020-01-01", None), (30, 30))
		self.assertEqual(prorata.active_days("2026-11-15", "2026-11-30"), (1, 30))
		self.assertEqual(prorata.active_days("2028-02-10", connected_on="2028-02-15"), (15, 29))  # a leap February

	def test_a_whole_month_has_no_fraction(self):
		self.assertEqual(prorata.fraction("2026-11-01"), (None, ""))
		self.assertEqual(prorata.fraction("2026-11-01", "2026-11-01", "2026-11-30"), (None, ""))
		self.assertEqual(prorata.fraction("2026-11-01", "2020-05-05", "2030-01-01"), (None, ""))

	def test_a_part_month_gives_the_fraction_and_the_note(self):
		f, note = prorata.fraction("2026-10-20", connected_on="2026-10-11")
		self.assertEqual((f, note), (Decimal(21) / Decimal(31), "Pro-rata: 21 of 31 days"))

	def test_a_connection_outside_the_month_is_not_billed(self):
		for month, connected, disconnected in (("2026-10-01", "2026-11-05", None), ("2026-10-01", None, "2026-09-30"), ("2026-10-01", "2026-10-20", "2026-10-10")):
			with self.assertRaises(prorata.NotConnected):
				prorata.active_days(month, connected, disconnected)


class UnitTestBillWithTaxesAndPartMonths(UnitTestCase):
	def charges(self):
		return [be.Charge("TST Service", "Service Charges", "Society", 40, "Nearest rupee", be.FIXED, Decimal(1700)),
		        be.Charge("TST Aquifer", "Aquifer Charges", "Society", 50, "Nearest rupee", be.FIXED, Decimal(1000))]

	def bill(self, **kw):
		energy = be.Charge("TST Energy", "Energy Charges", "Electricity", 10, "Nearest rupee", be.PER_UNIT)
		fpa = be.Adjustment(be.Charge("TST FPA", "FPA", "Electricity", 20, "Nearest rupee", be.PER_UNIT), units=Decimal(831), rate=Decimal("1.30"), reference="August 2026")
		return be.calculate(264, {"method": "Flat rate", "flat_rate": 70, "slabs": [], "minimum_charge": 0}, energy, charges=self.charges(), adjustments=[fpa],
		                    arrears=1_104, surcharge={"basis": "Percentage of current bill", "rate": 8}, **kw)

	def test_test_case_1_is_unchanged_without_taxes(self):
		r = self.bill()
		self.assertEqual((r.current, r.payable_within_due, r.payable_after_due, r.taxes, r.tax_added), (22_260, 23_364, 25_145, [], 0))

	def test_a_tax_adds_a_line_and_the_surcharge_follows_the_new_total(self):
		r = self.bill(taxes=[tax("GST", 10)])
		line = [l for l in r.lines if l.tax == "GST"][0]
		self.assertEqual((line.group, line.amount, line.label, line.charge_type), ("Taxes", Decimal(2_226), "GST", ""))
		self.assertEqual((r.current, r.tax_added, r.payable_within_due, r.surcharge), (24_486, 2_226, 25_590, 1_959))  # 8% of 24,486
		self.assertEqual(r.lines[-1].tax, "GST")  # taxes print after the charges

	def test_an_included_tax_leaves_the_bill_as_it_was(self):
		r = self.bill(taxes=[tax("VAT", 17, inclusive=True)])
		self.assertEqual((r.current, r.payable_within_due, [l.tax for l in r.lines if l.tax], r.tax_added), (22_260, 23_364, [], 0))
		self.assertEqual([(t.code, int(t.amount)) for t in r.taxes], [("VAT", 3_234)])  # still recorded, for the tax register

	def test_a_part_month_charges_fixed_charges_for_the_days_connected(self):
		f, note = prorata.fraction("2026-10-15", connected_on="2026-10-11")
		r = self.bill(fraction=f, fraction_note=note)
		by = {l.label: l for l in r.lines}
		self.assertEqual((by["Service Charges"].amount, by["Aquifer Charges"].amount), (Decimal(1_152), Decimal(677)))  # 21 of 31 days
		self.assertEqual(by["Service Charges"].note, "Pro-rata: 21 of 31 days")
		self.assertEqual((by["Energy Charges"].amount, by["FPA"].amount), (Decimal(18_480), Decimal(1_080)))  # metered, never pro-rated
		self.assertEqual(r.current, 18_480 + 1_080 + 1_152 + 677)

	def test_one_off_charges_are_not_pro_rated(self):
		energy = be.Charge("TST Energy", "Energy Charges", "Electricity", 10, "Nearest rupee", be.PER_UNIT)
		one_off = be.Charge("TST Connection", "Connection Fee", "Society", 60, "Nearest rupee", be.ONE_OFF, Decimal(500))
		r = be.calculate(100, {"method": "Flat rate", "flat_rate": 70, "slabs": [], "minimum_charge": 0}, energy, charges=self.charges() + [one_off],
		                 fraction=Decimal(1) / Decimal(2), fraction_note="Pro-rata: 15 of 30 days")
		by = {l.label: l.amount for l in r.lines}
		self.assertEqual((by["Connection Fee"], by["Service Charges"], by["Aquifer Charges"]), (Decimal(500), Decimal(850), Decimal(500)))

	def test_taxes_are_worked_out_on_the_pro_rated_charges(self):
		f, note = prorata.fraction("2026-10-15", connected_on="2026-10-11")
		r = self.bill(fraction=f, fraction_note=note, taxes=[tax("SOC", 10, applies_to=te.GROUPS, groups=("Society",))])
		self.assertEqual(r.tax_added, Decimal(183))  # 10% of 1,152 + 677 = 1,829 -> 182.9
