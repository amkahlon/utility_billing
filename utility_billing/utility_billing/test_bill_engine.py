# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests for the bill engine (no database)."""

from decimal import Decimal

from frappe.tests import UnitTestCase

from utility_billing.utility_billing.bill_engine import (
	FIXED,
	PER_AREA,
	PER_UNIT,
	PERCENT,
	Adjustment,
	Charge,
	calculate,
	rnd,
)

D = Decimal
FLAT70 = {"method": "Flat rate", "flat_rate": 70}
ENERGY = Charge("Energy", "Energy Charges", order=10)
FPA = Charge("FPA", "Fuel Price Adjustment", order=20)
SERVICE = Charge("Service", "Service Charges", group="Society", order=40, basis=FIXED, rate=D(1700))
AQUIFER = Charge("Aquifer", "Aquifer Charges", group="Society", order=50, basis=FIXED, rate=D(1000))


def case_1(**kw):
	args = dict(
		units=264, tariff=FLAT70, energy=ENERGY, charges=[SERVICE, AQUIFER],
		adjustments=[Adjustment(FPA, units=D(831), rate=D("1.30"), reference="August 2026")],
		arrears=1104, surcharge={"basis": "Percentage of current bill", "rate": 8},
	)
	args.update(kw)
	return calculate(**args)


class TestBillEngine(UnitTestCase):
	def test_case_1_exactly(self):
		"""House N 2308, October: 22,260 current; 23,364 within due date; 25,145 after (8%)."""
		r = case_1()
		self.assertEqual([(l.label, l.amount) for l in r.lines], [
			("Energy Charges", D(18480)),
			("Fuel Price Adjustment", D(1080)),
			("Service Charges", D(1700)),
			("Aquifer Charges", D(1000)),
		])
		self.assertEqual((r.current, r.arrears, r.payable_within_due), (D(22260), D(1104), D(23364)))
		self.assertEqual((r.surcharge, r.payable_after_due), (D(1781), D(25145)))
		fpa = r.lines[1]
		self.assertEqual((fpa.quantity, fpa.rate), (D(831), D("1.30")))
		self.assertIn("August 2026 units 831", fpa.note)

	def test_case_1_at_default_10_percent(self):
		r = case_1(surcharge={"basis": "Percentage of current bill", "rate": 10})
		self.assertEqual((r.surcharge, r.payable_after_due), (D(2226), D(25590)))

	def test_surcharge_on_current_bill_only_not_arrears(self):
		self.assertEqual(case_1(arrears=100000).surcharge, D(1781))

	def test_surcharge_fixed_and_cap(self):
		self.assertEqual(case_1(surcharge={"basis": "Fixed amount", "fixed_amount": 500}).surcharge, D(500))
		self.assertEqual(case_1(surcharge={"basis": "Percentage of current bill", "rate": 10, "cap": 1000}).surcharge, D(1000))

	def test_fpa_exempt_and_missing_reference(self):
		exempt = case_1(adjustments=[Adjustment(FPA, units=D(831), rate=D("1.3"), reference="August 2026",
		                                        exempt_reason="Mosque category exempt")])
		self.assertEqual(exempt.lines[1].amount, D(0))
		self.assertEqual(exempt.current, D(21180))
		missing = case_1(adjustments=[Adjustment(FPA, units=None, rate=D("1.3"), reference="August 2026")])
		self.assertEqual((missing.lines[1].amount, missing.lines[1].note), (D(0), "No reading for August 2026"))

	def test_negative_fpa(self):
		r = case_1(adjustments=[Adjustment(FPA, units=D(831), rate=D("-0.50"), reference="August 2026")])
		self.assertEqual(r.lines[1].amount, D(-416))  # -415.5 rounds half up away from zero

	def test_per_unit_add_on_and_percentage_and_area(self):
		om = Charge("O&M", "O&M Charges", order=30, basis=PER_UNIT, rate=D(10))
		tax = Charge("Duty", "Electricity Duty", group="Government", order=60, basis=PERCENT, rate=D(1.5))
		plot = Charge("Society", "Society Charges", group="Society", order=45, basis=PER_AREA, rate=D(100))
		r = calculate(500, {"method": "Flat rate", "flat_rate": 80}, ENERGY, charges=[om, tax, plot], area=20)
		amounts = {l.label: l.amount for l in r.lines}
		self.assertEqual(amounts["O&M Charges"], D(5000))
		self.assertEqual(amounts["Electricity Duty"], D(600))  # 1.5% of 40,000
		self.assertEqual(amounts["Society Charges"], D(2000))
		self.assertEqual(r.current, D(47600))

	def test_minimum_bill(self):
		r = calculate(0, FLAT70, ENERGY, minimum_bill=500)
		self.assertEqual((r.current, r.lines[-1].label), (D(500), "Minimum bill adjustment"))
		self.assertEqual(r.surcharge, D(50))

	def test_slab_energy_note(self):
		slabs = [{"from_units": 0, "to_units": 100, "rate": 10}, {"from_units": 100, "to_units": 0, "rate": 20}]
		r = calculate(150, {"method": "Progressive slabs", "slabs": slabs}, ENERGY)
		self.assertEqual(r.lines[0].amount, D(2000))
		self.assertEqual(r.lines[0].note, "100 × 10; 50 × 20")

	def test_rounding_rules(self):
		self.assertEqual(rnd("1080.30"), D(1080))
		self.assertEqual(rnd("1780.80"), D(1781))
		self.assertEqual(rnd("10.5"), D(11))
		self.assertEqual(rnd("10.1", "Round up"), D(11))
		self.assertEqual(rnd("10.9", "Round down"), D(10))
		self.assertEqual(rnd("10.555", "No rounding"), D("10.56"))
