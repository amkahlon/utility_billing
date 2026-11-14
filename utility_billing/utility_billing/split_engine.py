# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Splitting a bill when a tariff changes in the middle of the reading period (FS-CFG-05, FS-BIL-02). Pure Python, no database.

The reading period runs from the previous reading date to this one: its days are the days between them. A tariff
in force from the day it starts; the days of each tariff are the days of the period it covers. Units are split by days, and
the last share takes the remainder so the shares always add up to the units read.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

OPEN_END = date(9999, 12, 31)


def D(v) -> Decimal:
	return Decimal(str(v or 0))


def tariff_days(start: date, end: date, tariffs: list[tuple]) -> list[tuple[str, date, date, int]]:
	"""`tariffs`: (name, effective_from, effective_to or None). Returns (name, from, to, days) for each tariff that covers part of
	the period [start, end) in date order; `to` is the last day it covers. A period with no days returns []."""
	if end <= start:
		return []
	out = []
	for name, eff_from, eff_to in sorted(tariffs, key=lambda t: t[1]):
		lo = max(start, eff_from)
		hi = min(end, (eff_to + timedelta(days=1)) if eff_to else OPEN_END)  # exclusive upper bound
		days = (hi - lo).days
		if days > 0:
			out.append((name, lo, hi - timedelta(days=1), days))
	return out


def split_units(units, days: list[int], places: int = 2) -> list[Decimal]:
	"""Units shared by days; the last share is the remainder."""
	total = sum(days)
	if not total:
		return [D(units)]
	q = Decimal(1).scaleb(-places)
	shares = [(D(units) * d / total).quantize(q, rounding=ROUND_HALF_UP) for d in days[:-1]]
	return [*shares, D(units) - sum(shares, Decimal(0))]
