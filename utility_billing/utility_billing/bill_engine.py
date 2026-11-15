# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill calculation (FS-BIL-01, 03, 04, 05, 09, 15). Pure Python, no database.

Order: energy -> per-unit add-ons -> adjustments (FPA/QTA on their reference units) -> fixed and
per-area charges -> percentage charges -> minimum bill -> current bill -> arrears -> payable within
due date -> surcharge on the current bill only -> payable after due date.
Each line is rounded by its own rounding rule; totals are sums of rounded lines.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from utility_billing.utility_billing.tariff_engine import calculate_energy

NEAREST = "Nearest rupee"
NONE = "No rounding"
UP = "Round up"
DOWN = "Round down"

PER_UNIT = "Per Unit"
FIXED = "Fixed"
PER_AREA = "Per Area"
PERCENT = "Percentage of Charges"
ONE_OFF = "One-off"
BY_PLOT_SIZE = "By Plot Size"  # resolved to a fixed monthly amount for the property's plot size class before calculating
SURCHARGE_WITH_ARREARS = "Percentage of current bill and arrears"


def D(v) -> Decimal:
	return Decimal(str(v or 0))


def rnd(amount, rule: str = NEAREST) -> Decimal:
	a = D(amount)
	if rule == NONE:
		return a.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	mode = {NEAREST: ROUND_HALF_UP, UP: ROUND_CEILING, DOWN: ROUND_FLOOR}.get(rule, ROUND_HALF_UP)
	return a.quantize(Decimal("1"), rounding=mode)


def fmt(v) -> str:
	return f"{D(v):,.2f}".rstrip("0").rstrip(".")


@dataclass
class Charge:
	"""A charge type as the engine needs it."""

	charge_type: str
	label: str
	group: str = "Electricity"
	order: int = 10
	rounding: str = NEAREST
	basis: str = FIXED
	rate: Decimal = Decimal(0)


@dataclass
class Adjustment:
	"""FPA/QTA: rate per unit on the units of a reference period."""

	charge: Charge
	units: Decimal | None  # None = no reading for the reference period
	rate: Decimal
	reference: str  # e.g. "August 2026"
	exempt_reason: str | None = None


@dataclass
class Line:
	charge_type: str
	label: str
	group: str
	order: int
	basis: str
	quantity: Decimal
	rate: Decimal
	amount: Decimal
	note: str = ""
	tax: str = ""  # the Tax a tax line belongs to


@dataclass
class BillResult:
	lines: list[Line] = field(default_factory=list)
	current: Decimal = Decimal(0)
	arrears: Decimal = Decimal(0)
	payable_within_due: Decimal = Decimal(0)
	surcharge: Decimal = Decimal(0)
	payable_after_due: Decimal = Decimal(0)
	taxes: list = field(default_factory=list)  # tax_engine.TaxLine, inclusive ones too
	tax_added: Decimal = Decimal(0)
	installment: Decimal | None = None  # an installment plan's installment due with this bill (FS-BIL-14)


def calculate(
	units,
	tariff: dict,
	energy: Charge,
	charges: Iterable[Charge] = (),
	adjustments: Iterable[Adjustment] = (),
	area=0,
	arrears=0,
	minimum_bill=0,
	surcharge: dict | None = None,
	bill_rounding: str = NEAREST,
	taxes: Iterable = (),
	fraction: Decimal | None = None,
	fraction_note: str = "",
	installment: Decimal | None = None,
	segments: Iterable[dict] | None = None,
) -> BillResult:
	"""`tariff`: {"method", "flat_rate", "slabs", "minimum_charge"}; `surcharge`: {"basis",
	"rate", "fixed_amount", "cap"} with basis "Percentage of current bill" or "Fixed amount".
	`taxes`: tax_engine.TaxSpec list (none by default). `fraction`: share of a part month to charge for the fixed and
	per-area charges (FS-BIL-07), with its note.
	`segments`: when a tariff changes in the middle of the reading period (FS-CFG-05, FS-BIL-02), a list of
	{"units", "tariff", "charges", "fraction", "note"}, one per tariff in force; the energy and the tariff's own charges are worked
	out for each on its share of the units and of the days, and `units`, `tariff`, `charges` and `fraction` are not used.
	"""
	frac_all = D(fraction) if fraction is not None else Decimal(1)
	lines: list[Line] = []
	segs = [dict(s) for s in segments] if segments else [
		{"units": units, "tariff": tariff, "charges": charges, "fraction": frac_all, "note": fraction_note}]

	for seg in segs:
		u, trf, frac, seg_note = D(seg["units"]), seg["tariff"], D(seg["fraction"]), seg.get("note") or ""
		# 1. Energy (none for a service billed without a meter: `energy` is None, increment 22)
		energy_amount = Decimal(0)
		if energy is not None:
			e = calculate_energy(u, trf.get("method"), flat_rate=trf.get("flat_rate"), slabs=trf.get("slabs") or (),
			                     minimum_charge=trf.get("minimum_charge"))
			energy_note = "; ".join(f"{fmt(x['units'])} × {fmt(x['rate'])}" for x in e.lines)
			if e.minimum_applied:
				energy_note += f" (minimum charge {fmt(e.amount)})"
			if len(segs) > 1 and seg_note:
				energy_note = f"{seg_note}: {energy_note}"
			avg_rate = (e.amount / u) if u else D(trf.get("flat_rate"))
			lines.append(Line(energy.charge_type, energy.label, energy.group, energy.order, PER_UNIT, u,
			                  avg_rate.quantize(Decimal("0.0001")), rnd(e.amount, energy.rounding), energy_note))
			energy_amount = rnd(e.amount, energy.rounding)

		seg_charges = list(seg["charges"])
		split_note = seg_note if len(segs) > 1 else fraction_note
		# 2. Per-unit add-ons
		for c in (c for c in seg_charges if c.basis == PER_UNIT):
			lines.append(Line(c.charge_type, c.label, c.group, c.order, PER_UNIT, u, D(c.rate), rnd(u * D(c.rate), c.rounding),
			                  f"{fmt(u)} × {fmt(c.rate)}" + (f"; {seg_note}" if len(segs) > 1 and seg_note else "")))
		# 4. Fixed, one-off and per-area charges
		for c in (c for c in seg_charges if c.basis in (FIXED, ONE_OFF)):
			part = frac if c.basis == FIXED else (Decimal(1) if len(segs) == 1 or seg is segs[0] else Decimal(0))  # one-off: once
			if part == 0 and c.basis == ONE_OFF:
				continue
			lines.append(Line(c.charge_type, c.label, c.group, c.order, c.basis, D(1), D(c.rate), rnd(D(c.rate) * part, c.rounding),
			                  split_note if part != 1 else ""))
		for c in (c for c in seg_charges if c.basis == PER_AREA):
			lines.append(Line(c.charge_type, c.label, c.group, c.order, PER_AREA, D(area), D(c.rate),
			                  rnd(D(area) * D(c.rate) * frac, c.rounding), f"{fmt(area)} × {fmt(c.rate)}" + (f"; {split_note}" if frac != 1 else "")))
		# 5. Percentage charges: % of the energy charge
		for c in (c for c in seg_charges if c.basis == PERCENT):
			lines.append(Line(c.charge_type, c.label, c.group, c.order, PERCENT, energy_amount, D(c.rate),
			                  rnd(energy_amount * D(c.rate) / 100, c.rounding), f"{fmt(c.rate)}% of energy {fmt(energy_amount)}"))
	# 3. Adjustments (FPA/QTA) on their reference units
	for a in adjustments:
		c = a.charge
		if a.exempt_reason:
			lines.append(Line(c.charge_type, c.label, c.group, c.order, PER_UNIT, D(0), D(a.rate), D(0),
			                  f"Exempt: {a.exempt_reason}"))
			continue
		if a.units is None:
			lines.append(Line(c.charge_type, c.label, c.group, c.order, PER_UNIT, D(0), D(a.rate), D(0),
			                  f"No reading for {a.reference}"))
			continue
		lines.append(Line(c.charge_type, c.label, c.group, c.order, PER_UNIT, D(a.units), D(a.rate),
		                  rnd(D(a.units) * D(a.rate), c.rounding), f"{a.reference} units {fmt(a.units)} × {fmt(a.rate)}"))

	current = sum((l.amount for l in lines), Decimal(0))
	# 6. Minimum bill
	if D(minimum_bill) > 0 and current < D(minimum_bill):
		diff = rnd(D(minimum_bill) - current, bill_rounding)
		lines.append(Line("", "Minimum bill adjustment", "Other", 999, FIXED, D(1), diff, diff,
		                  f"Minimum bill {fmt(minimum_bill)}"))
		current += diff
	# 6b. Taxes, in their configured order, on the charges they apply to (FS-CFG-14)
	tax_lines = []
	taxes = list(taxes)
	if taxes:
		from utility_billing.utility_billing import tax_engine  # noqa: PLC0415

		tax_lines = tax_engine.calculate_taxes([(l.charge_type, l.group, l.amount) for l in lines], taxes)
		for t in tax_lines:
			if not t.inclusive and t.amount:
				lines.append(Line("", t.name, "Taxes", 1000 + t.order, PERCENT, t.base, t.rate, t.amount, t.note, tax=t.code))
				current += t.amount

	lines.sort(key=lambda l: (l.order, l.label))
	result = BillResult(lines=lines, current=current, arrears=rnd(arrears, NONE), taxes=tax_lines,
	                    tax_added=sum((t.amount for t in tax_lines if not t.inclusive), Decimal(0)))
	result.payable_within_due = result.current + result.arrears
	if installment is not None:  # under a plan only the installment of the arrears is due now
		result.installment = rnd(installment, NONE)
		result.payable_within_due = result.current + result.installment
	# 7. Surcharge on the current bill only (FS-BIL-04), or, where the society chooses it, on the current bill and unpaid arrears
	# (the Park View bills: the surcharge grows with every unpaid month; increment 22)
	s = surcharge or {}
	if s.get("basis") == "Fixed amount":
		sur = D(s.get("fixed_amount"))
	elif s.get("basis") == SURCHARGE_WITH_ARREARS:
		sur = rnd((result.current + max(result.arrears, Decimal(0))) * D(s.get("rate", 10)) / 100, bill_rounding)
	else:
		sur = rnd(result.current * D(s.get("rate", 10)) / 100, bill_rounding)
	if D(s.get("cap")) > 0:
		sur = min(sur, D(s.get("cap")))
	result.surcharge = max(sur, Decimal(0)) if result.current > 0 else Decimal(0)
	result.payable_after_due = result.payable_within_due + result.surcharge
	return result
