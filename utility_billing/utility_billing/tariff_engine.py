# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Energy charge calculation for a tariff (FS-CFG-04).

Pure Python: no database access, so it can be unit-tested and reused by the bill engine.
Money is handled as Decimal and rounded to 2 places here; bill-level rounding is applied later.

Slab convention: a slab covers units in (from_units, to_units]. The first slab starts at 0
and also covers 0 units. to_units = 0 means "no upper limit" and is allowed on the last slab only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

FLAT = "Flat rate"
PROGRESSIVE = "Progressive slabs"
WHOLE_BILL = "Whole-bill slabs"
METHODS = (FLAT, PROGRESSIVE, WHOLE_BILL)

CENT = Decimal("0.01")


def money(value) -> Decimal:
	return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def qty(value) -> Decimal:
	return Decimal(str(value or 0))


@dataclass(frozen=True)
class Slab:
	from_units: Decimal
	to_units: Decimal | None  # None = no upper limit
	rate: Decimal

	@classmethod
	def of(cls, from_units, to_units, rate) -> Slab:
		upper = qty(to_units)
		return cls(qty(from_units), upper if upper > 0 else None, qty(rate))


@dataclass
class EnergyCharge:
	units: Decimal
	amount: Decimal
	lines: list[dict] = field(default_factory=list)
	minimum_applied: bool = False


def to_slabs(rows: Iterable) -> list[Slab]:
	"""Accept Slab objects, dicts or Frappe child rows with from_units / to_units / rate."""
	slabs = []
	for r in rows:
		if isinstance(r, Slab):
			slabs.append(r)
		else:
			get = r.get if isinstance(r, dict) else lambda k, r=r: getattr(r, k, None)
			slabs.append(Slab.of(get("from_units"), get("to_units"), get("rate")))
	return slabs


def slab_errors(rows: Iterable) -> list[str]:
	"""Return human-readable problems with a slab table; empty list means valid."""
	slabs = to_slabs(rows)
	errors = []
	if not slabs:
		return ["At least one slab is required."]
	if slabs[0].from_units != 0:
		errors.append("The first slab must start at 0 units.")
	for i, s in enumerate(slabs, start=1):
		if s.rate < 0:
			errors.append(f"Slab {i}: rate can't be negative.")
		if s.to_units is None and i != len(slabs):
			errors.append(f"Slab {i}: only the last slab can be open-ended (To Units = 0).")
		if s.to_units is not None and s.to_units <= s.from_units:
			errors.append(f"Slab {i}: To Units must be greater than From Units.")
		if i > 1:
			prev = slabs[i - 2]
			if prev.to_units is not None and s.from_units != prev.to_units:
				errors.append(f"Slab {i}: must start at {prev.to_units} (where slab {i - 1} ends).")
	return errors


def calculate_energy(units, method: str, flat_rate=0, slabs: Iterable = (), minimum_charge=0) -> EnergyCharge:
	"""Energy charge for `units` under one tariff."""
	u = qty(units)
	if u < 0:
		raise ValueError("Units can't be negative.")
	if method not in METHODS:
		raise ValueError(f"Unknown pricing method: {method}")

	lines: list[dict] = []
	if method == FLAT:
		rate = qty(flat_rate)
		amount = money(u * rate)
		lines.append({"units": u, "rate": rate, "amount": amount})
	else:
		slab_list = to_slabs(slabs)
		errors = slab_errors(slab_list)
		if errors:
			raise ValueError(" ".join(errors))
		if method == PROGRESSIVE:
			amount = Decimal("0.00")
			for s in slab_list:
				if u <= s.from_units:
					break
				top = u if s.to_units is None else min(u, s.to_units)
				band = top - s.from_units
				line_amount = money(band * s.rate)
				lines.append({"units": band, "rate": s.rate, "amount": line_amount,
				              "from_units": s.from_units, "to_units": s.to_units})
				amount += line_amount
		else:  # WHOLE_BILL
			chosen = slab_list[-1]
			for s in slab_list:
				if s.to_units is None or u <= s.to_units:
					chosen = s
					break
			amount = money(u * chosen.rate)
			lines.append({"units": u, "rate": chosen.rate, "amount": amount,
			              "from_units": chosen.from_units, "to_units": chosen.to_units})

	minimum = money(minimum_charge)
	if minimum > 0 and amount < minimum:
		return EnergyCharge(units=u, amount=minimum, lines=lines, minimum_applied=True)
	return EnergyCharge(units=u, amount=amount, lines=lines)
