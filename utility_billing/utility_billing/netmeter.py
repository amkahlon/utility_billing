# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Net metering (increment 28; FS-CFG-23, FS-BIL-13, FS-WF-34, SC-26, test case 2). User's decisions, 2026-11-18:

1. Three methods, each society chooses (Society → Net Metering Method):
   - **Net units**: billed units = imports − exports − units brought forward, never below zero; the rest is carried forward.
   - **Buyback rate**: imports billed in full; exports credited at the approved tariff's buyback rate (a credit line on the bill;
     a bill that goes below zero leaves a credit on the account, which the next bill uses).
   - **Society rebate pool**: each month the society's pool (residents' imports − what the DISCO billed it) is shared among solar
     consumers: exports × (pool ÷ residents' exports), rounded by the society's rule, then set against imports like net units.
2. Unused export units are carried forward, without expiry.
3. Each per-unit charge says whether it is worked out on net or on imported units (Charge Type → Units for Net-Metering
   Accounts); FPA and QTA follow the society's own setting.
4. One bi-directional meter: the Meter Reading has an import and an export register.

The calculation below is pure (no database); the helpers after it read the units brought forward, the month's pool and the
figures the pool is made from."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

NET, BUYBACK, POOL = "Net units", "Buyback rate", "Society rebate pool"
IMPORTED = "Imported units"
HALF_UP, DOWN, UP, NO_ROUNDING = "Nearest unit (half up)", "Round down", "Round up", "No rounding"


def D(v) -> Decimal:
	return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def round_units(v, rule: str = HALF_UP) -> Decimal:
	v = D(v)
	if rule == DOWN:
		return v.quantize(Decimal(1), ROUND_FLOOR)
	if rule == UP:
		return v.quantize(Decimal(1), ROUND_CEILING)
	if rule == NO_ROUNDING:
		return v.quantize(Decimal("0.01"), ROUND_HALF_UP)
	return v.quantize(Decimal(1), ROUND_HALF_UP)


@dataclass
class NetResult:
	method: str
	import_units: Decimal
	export_units: Decimal
	credited_units: Decimal  # exports counted for the bill (all of them, or their share of the pool)
	credit_factor: Decimal | None
	carried_in: Decimal  # units brought forward from earlier bills
	set_off: Decimal  # units set against this month's imports
	carried_out: Decimal  # units carried forward to the next bill
	billed_units: Decimal  # the units energy (and "net units" charges) are worked out on
	buyback_rate: Decimal = Decimal(0)
	buyback_credit: Decimal = Decimal(0)  # money, for the buyback method


def compute(method: str, import_units, export_units, carried_in=0, pool_units=None, pool_exports=None, rounding: str = HALF_UP,
            buyback_rate=0) -> NetResult:
	imp, exp, cin = D(import_units), D(export_units), D(carried_in)
	if method == BUYBACK:
		rate = D(buyback_rate)
		# units brought forward from an earlier method stay where they are; exports are paid for in money
		return NetResult(BUYBACK, imp, exp, exp, None, cin, Decimal(0), cin, imp, rate, (exp * rate).quantize(Decimal("0.01"), ROUND_HALF_UP))
	factor = None
	if method == POOL:
		pool, exports = D(pool_units), D(pool_exports)
		# the exact share, not the rounded factor: test case 2 credits 908 × 126,089 ÷ 408,159 = 280.5005 → 281
		credited = round_units(exp * pool / exports, rounding) if exports > 0 else Decimal(0)
		factor = (pool / exports).quantize(Decimal("0.000001"), ROUND_HALF_UP) if exports > 0 else Decimal(0)
	else:
		method, credited = NET, exp
	available = credited + cin
	set_off = min(imp, available)
	return NetResult(method, imp, exp, credited, factor, cin, set_off, available - set_off, imp - set_off)


def pool_figures(residents_import, disco_import, residents_export, disco_export) -> dict:
	"""The month's pool, as test case 2 sets it out."""
	ri, di, re_, de = D(residents_import), D(disco_import), D(residents_export), D(disco_export)
	pool = max(ri - di, Decimal(0))
	used_inside = re_ - de
	return {"pool_units": pool, "solar_used_inside": used_inside, "losses_units": used_inside - pool,
	        "credit_factor": (pool / re_).quantize(Decimal("0.000001"), ROUND_HALF_UP) if re_ > 0 else Decimal(0)}


# ---------------------------------------------------------------- database helpers
def method_of(society) -> str:
	import frappe  # noqa: PLC0415

	return frappe.get_cached_value("Society", society, "net_metering_method") or NET


def carried_in(connection, month) -> Decimal:
	"""Solar units brought forward: from the account's last posted bill before `month` (the latest version of it), or, before its
	first bill here, from the opening balances it was migrated with."""
	import frappe  # noqa: PLC0415

	from utility_billing.utility_billing.readings import month_start  # noqa: PLC0415

	last = frappe.get_all("Utility Bill", filters={"connection": connection, "docstatus": 1, "billing_month": ["<", month_start(month)],
	                                               "superseded_by": ["is", "not set"]},
	                      fields=["units_carried_out"], order_by="billing_month desc, creation desc", limit=1)
	if last:
		return D(last[0].units_carried_out)
	row = frappe.db.sql("""select ifnull(sum(r.solar_credit_units), 0) from `tabOpening Balance Row` r
	                       join `tabOpening Balance Import` i on i.name = r.parent
	                       where r.connection = %s and i.docstatus = 1""", connection)
	return D(row[0][0] if row else 0)


def approved_pool(society, utility, month):
	import frappe  # noqa: PLC0415

	from utility_billing.utility_billing.readings import month_start  # noqa: PLC0415

	name = frappe.db.get_value("Solar Rebate Pool", {"society": society, "utility": utility, "billing_month": month_start(month), "docstatus": 1})
	return frappe.get_cached_doc("Solar Rebate Pool", name) if name else None


def residents_figures(society, utility, month) -> dict:
	"""Residents' imports (every meter) and exports (solar meters) in the month's final readings."""
	import frappe  # noqa: PLC0415

	from utility_billing.utility_billing.readings import DONE, month_start  # noqa: PLC0415

	row = frappe.db.sql("""select ifnull(sum(units), 0), ifnull(sum(export_units), 0), ifnull(sum(net_metering), 0) from `tabMeter Reading`
	                       where society = %s and billing_month = %s and status in %s
	                       and reading_cycle in (select name from `tabReading Cycle` where utility = %s)""",
	                    (society, month_start(month), DONE, utility))[0]
	return {"residents_import_units": float(row[0]), "residents_export_units": float(row[1]), "solar_accounts": int(row[2])}


def pool_notice(doc) -> str:
	"""The month's figures in words, for the bills (as the SNOCHS bill prints them)."""
	from frappe import _  # noqa: PLC0415
	from frappe.utils import formatdate  # noqa: PLC0415

	n = lambda v: f"{float(v or 0):,.0f}"  # noqa: E731
	return _(
		"Solar rebate, {month}: the DISCO billed the society for {disco} units, while residents' meters recorded {res} units, a difference "
		"of {pool} units. Residents produced {exp} solar units; {dexp} of them were exported to the DISCO, leaving {inside} used inside "
		"the society. The {pool} units are credited to solar producers at {factor}% of their exported units; losses were {loss} units."
	).format(month=formatdate(doc.billing_month, "MMMM yyyy"), disco=n(doc.disco_import_units), res=n(doc.residents_import_units),
	         pool=n(doc.pool_units), exp=n(doc.residents_export_units), dexp=n(doc.disco_export_units), inside=n(doc.solar_used_inside),
	         factor=f"{float(doc.credit_factor or 0) * 100:.1f}", loss=n(doc.losses_units))


def reference_units(connection, month, basis: str):
	"""Units of an earlier month for FPA/QTA on a net-metering account: its imports (the reading), or the net units billed."""
	import frappe  # noqa: PLC0415

	from utility_billing.utility_billing import readings  # noqa: PLC0415

	if basis == IMPORTED:
		return readings.get_billable_units(connection, month)
	bill = frappe.get_all("Utility Bill", filters={"connection": connection, "docstatus": 1, "billing_month": readings.month_start(month),
	                                               "superseded_by": ["is", "not set"]}, fields=["units"], limit=1)
	return float(bill[0].units) if bill else readings.get_billable_units(connection, month)
