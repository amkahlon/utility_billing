# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Which bills the money received paid (FS-REC-17: exact tax collected by bill). Pure Python, no database.

A payment goes to the account, not to a bill. For tax on a collection basis each account's payments are allocated oldest
bill first, each bill taking up to its own charges (the current bill, which carries the tax); what is left over goes to the next.
Surcharge and fees are not part of a bill's charges, so they never take any of the allocation.
"""

from __future__ import annotations

from decimal import Decimal


def D(v) -> Decimal:
	return Decimal(str(v or 0))


def fifo(paid, amounts) -> list[Decimal]:
	"""`amounts`: each bill's charges, oldest first. Returns how much of each the payments cover."""
	left, out = D(paid), []
	for a in amounts:
		take = max(min(left, D(a)), Decimal(0))
		out.append(take)
		left -= take
	return out


def shares(paid, amounts) -> list[Decimal]:
	"""The paid fraction (0 to 1) of each bill; a bill with no charges counts as unpaid."""
	return [(p / D(a)) if D(a) > 0 else Decimal(0) for p, a in zip(fifo(paid, amounts), amounts, strict=True)]
