# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Meter reading calculations (FS-RDG-05, 08, 09, 10, 11). Pure Python, no database.

Units = (present - previous) x multiplier, with roll-over on the dial, plus any units from a meter
replaced during the cycle, minus units already estimated since the last actual reading (true-up).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ROLLOVER_THRESHOLD = Decimal("0.9")  # previous reading must be in the top 10% of the dial

FLAG_LOWER = "Lower than previous reading"
FLAG_ZERO = "Zero use"
FLAG_HIGH = "Above expected range"
FLAG_LOW = "Below expected range"
FLAG_ROLLOVER = "Meter roll-over"
FLAG_NEGATIVE_TRUE_UP = "True-up below zero"
# Information only: these don't send a reading to the review queue.
INFO_FLAGS = {FLAG_ROLLOVER}

AVG_3 = "Average of last 3 months"
AVG_6 = "Average of last 6 months"
SAME_MONTH = "Same month last year"
ESTIMATION_METHODS = (AVG_3, AVG_6, SAME_MONTH)


def D(value) -> Decimal:
	return Decimal(str(value or 0))


def q(value: Decimal) -> Decimal:
	return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP).normalize() + 0


@dataclass
class ReadingResult:
	units: Decimal  # billable units for this cycle
	raw_units: Decimal  # consumption since the last actual reading on the current meter
	flags: list[str] = field(default_factory=list)
	rolled_over: bool = False
	expected: tuple[Decimal, Decimal, Decimal] | None = None  # (average, low, high)

	@property
	def needs_review(self) -> bool:
		return any(f not in INFO_FLAGS for f in self.flags)


def raw_units(previous, present, digits: int, multiplier) -> tuple[Decimal, bool, bool]:
	"""(units, rolled_over, lower_than_previous) on one meter."""
	prev, pres, mult = D(previous), D(present), D(multiplier) or Decimal(1)
	if pres >= prev:
		return q((pres - prev) * mult), False, False
	dial_max = Decimal(10) ** int(digits or 5)
	if prev >= dial_max * ROLLOVER_THRESHOLD:
		return q((dial_max - prev + pres) * mult), True, False
	return q((pres - prev) * mult), False, True


def expected_range(history: Sequence, same_month_last_year=None, tolerance_percent=50):
	"""(average, low, high) from the last 3 months of actual units (most recent first) and, if known,
	the same month last year. None without history."""
	points = [D(u) for u in list(history)[:3]]
	if same_month_last_year not in (None, ""):
		points.append(D(same_month_last_year))
	if not points:
		return None
	avg = sum(points) / len(points)
	tol = D(tolerance_percent) / 100
	return q(avg), q(avg * (1 - tol)), q(avg * (1 + tol))


def evaluate(
	previous,
	present,
	digits: int = 5,
	multiplier=1,
	history: Sequence = (),
	same_month_last_year=None,
	tolerance_percent=50,
	estimated_since=0,
	old_meter_units=0,
) -> ReadingResult:
	"""Billable units and flags for an actual reading."""
	units_now, rolled, lower = raw_units(previous, present, digits, multiplier)
	raw = q(units_now + D(old_meter_units))
	units = q(raw - D(estimated_since))
	flags: list[str] = []
	if rolled:
		flags.append(FLAG_ROLLOVER)
	if lower:
		flags.append(FLAG_LOWER)
	if raw == 0:
		flags.append(FLAG_ZERO)
	if D(estimated_since) > 0 and units < 0:
		flags.append(FLAG_NEGATIVE_TRUE_UP)
	rng = expected_range(history, same_month_last_year, tolerance_percent)
	if rng and not lower and raw > 0:
		_avg, low, high = rng
		# Compare this cycle's own consumption, not the true-up catch-up.
		months = 1 + (1 if D(estimated_since) > 0 else 0)
		per_month = raw / months if D(estimated_since) > 0 else raw
		if per_month > high:
			flags.append(FLAG_HIGH)
		elif per_month < low:
			flags.append(FLAG_LOW)
	return ReadingResult(units=units, raw_units=raw, flags=flags, rolled_over=rolled, expected=rng)


def estimate(method: str, history: Sequence, same_month_last_year=None) -> Decimal | None:
	"""Estimated units for an unread meter. `history` = actual units of past months, most recent
	first. Falls back to the 3-month average when last year's month is unknown; None without history."""
	if method not in ESTIMATION_METHODS:
		raise ValueError(f"Unknown estimation method: {method}")
	hist = [D(u) for u in history]
	if method == SAME_MONTH and same_month_last_year not in (None, ""):
		return q(D(same_month_last_year))
	window = hist[:6] if method == AVG_6 else hist[:3]
	if not window:
		return None
	return q(sum(window) / len(window))
