# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Taxes and duties on a bill, without any database access (FS-CFG-14, FS-BIL-01, FS-REC-17). Unit tested on their own.

A tax has a rate, the charges it applies to (all, some charge groups, or some charge types), a calculation order, and optionally
other taxes it is also worked out on (tax on tax). It is either **added** on top (exclusive) or **included** in the charges
(inclusive: the tax is carved out of the amount, the bill total does not change). A society with no tax has none, and the bill
is exactly what it was before taxes existed: the Pakistan housing society of test case 1 pays none, because the DISCO taxes the
society's bulk meter."""

from dataclasses import dataclass, field
from decimal import Decimal

ALL, GROUPS, TYPES = "All charges", "Charge groups", "Charge types"


def D(v) -> Decimal:
	return Decimal(str(v or 0))


@dataclass
class TaxSpec:
	code: str  # the Tax document's name
	name: str
	rate: Decimal
	applies_to: str = ALL
	groups: tuple = ()
	charge_types: tuple = ()
	inclusive: bool = False
	order: int = 10
	on_taxes: tuple = ()  # codes of earlier taxes this one is also worked out on
	rounding: str = "Nearest rupee"


@dataclass
class TaxLine:
	code: str
	name: str
	base: Decimal
	rate: Decimal
	amount: Decimal
	inclusive: bool = False
	order: int = 10
	note: str = ""
	parts: list = field(default_factory=list)


def matches(spec: TaxSpec, charge_type, group) -> bool:
	if spec.applies_to == GROUPS:
		return group in spec.groups
	if spec.applies_to == TYPES:
		return charge_type in spec.charge_types
	return True


def calculate_taxes(lines, specs, rnd=None) -> list[TaxLine]:
	"""Tax lines for a bill. `lines` are (charge_type, group, amount) of the charges; `specs` the taxes in force. Taxes are
	worked out in order; a tax with `on_taxes` also takes the amounts of those earlier taxes into its base."""
	if rnd is None:
		from utility_billing.utility_billing.bill_engine import rnd as rnd_  # noqa: PLC0415 (avoids a circular import)

		rnd = rnd_
	done: dict[str, TaxLine] = {}
	out = []
	for spec in sorted(specs, key=lambda s: (s.order, s.code)):
		base = sum((D(a) for ct, grp, a in lines if matches(spec, ct, grp)), Decimal(0))
		base += sum((done[c].amount for c in spec.on_taxes if c in done and not done[c].inclusive), Decimal(0))
		rate = D(spec.rate)
		if rate <= 0 or base <= 0:
			amount = Decimal(0)
		elif spec.inclusive:
			amount = rnd(base * rate / (100 + rate), spec.rounding)
		else:
			amount = rnd(base * rate / 100, spec.rounding)
		note = (f"{rate.normalize():f}% of {base:,.2f}".replace(".00", "") if not spec.inclusive else
		        f"{rate.normalize():f}% included in {base:,.2f}".replace(".00", ""))
		line = TaxLine(spec.code, spec.name, base, rate, amount, bool(spec.inclusive), spec.order, note)
		done[spec.code] = line
		out.append(line)
	return out


def added(tax_lines) -> Decimal:
	"""What taxes add to the bill: inclusive taxes add nothing."""
	return sum((t.amount for t in tax_lines if not t.inclusive), Decimal(0))
