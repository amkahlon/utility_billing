# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Revenue assurance (FS-RA-01 to 10): monthly checks that point to revenue being lost, each finding kept as a case (RA Finding)
to assign, investigate and close with a result (FS-RA-09).

The rules are plain and explainable (thresholds on the Society, "Revenue assurance"); RA-10's statistical outlier stands in for
the AI model planned in increment 24. A finding is raised once per check, subject and month: running the checks again refreshes
an open finding's figures and never reopens a closed one.

Pure functions first (unit-tested without a database), then the checks that read the society's data, then the run."""

import json
import statistics
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, get_last_day, getdate, now_datetime

from utility_billing.utility_billing import losses
from utility_billing.utility_billing.mis import month_start
from utility_billing.utility_billing import rights

RESIDENTIAL_WORDS = ("resid", "domestic", "house")
COMMERCIAL_PROPERTY = ("Shop", "Office", "Commercial Building")
MULTIPLIER_FACTORS = (2, 5, 10, 20, 40)
WORKERS = {"System Manager", "Billing Manager", "General Manager", "Recovery Officer"}


# ---------------------------------------------------------------- pure rules
def average(values):
	values = [flt(v) for v in values if v is not None]
	return sum(values) / len(values) if values else None


def is_drop(units, own_history, peer_median, own_pct=50, peer_pct=50, min_history=3) -> bool:
	"""RA-01: units well below the account's own average and (when there are similar accounts) below theirs too."""
	if len([u for u in own_history if u is not None]) < min_history:
		return False
	own = average(own_history)
	if not own or own <= 0:
		return False
	if flt(units) >= own * flt(own_pct) / 100:
		return False
	return peer_median is None or peer_median <= 0 or flt(units) < flt(peer_median) * flt(peer_pct) / 100


def drop_score(units, own_avg) -> int:
	"""0 to 100: how far below its average the account fell (a fall to nothing scores 100)."""
	if not own_avg or own_avg <= 0:
		return 0
	return max(0, min(100, round((1 - flt(units) / own_avg) * 100)))


def multiplier_factor(units, own_avg, factors=MULTIPLIER_FACTORS, tolerance=0.15):
	"""RA-06: the factor (e.g. 10 or 1/10) by which this month's units differ from the average when that is close to a whole
	multiplier step, else None. A wrong CT multiplier shows as a jump or fall by exactly such a step."""
	if not own_avg or own_avg <= 0 or flt(units) <= 0:
		return None
	ratio = flt(units) / own_avg
	for fct in factors:
		for target, label in ((fct, fct), (1 / fct, -fct)):
			if abs(ratio - target) <= target * tolerance:
				return label
	return None


def consecutive_estimates(types) -> int:
	"""RA-05: how many bills in a row, counting back from the latest, were estimated. `types` is newest first."""
	n = 0
	for t in types:
		if t != "Estimated":
			break
		n += 1
	return n


def outliers(values: dict, z=2.0, min_peers=3) -> dict:
	"""RA-08: {key: value} of the values above mean + z standard deviations of the others (each value is compared with its peers
	without itself, so one large value can't hide by raising the mean)."""
	out = {}
	if len(values) < min_peers:
		return out
	for k, v in values.items():
		others = [x for kk, x in values.items() if kk != k]
		if len(others) < 2:
			continue
		mean, sd = statistics.mean(others), statistics.pstdev(others)
		if flt(v) > mean + z * max(sd, mean * 0.25, 1):
			out[k] = v
	return out


def zscore(value, history):
	"""RA-10: how many standard deviations this month's units are from the account's own history, or None with too little history."""
	history = [flt(h) for h in history if h is not None]
	if len(history) < 4:
		return None
	mean, sd = statistics.mean(history), statistics.pstdev(history)
	if sd < max(1, mean * 0.05):
		sd = max(1, mean * 0.05)
	return (flt(value) - mean) / sd


def is_residential(category) -> bool:
	return any(w in (category or "").lower() for w in RESIDENTIAL_WORDS)


def size_band(area, plot_size_class=None) -> str:
	"""Similar properties: the plot size class when the society keeps one, else the area rounded to 5 units (marla)."""
	if plot_size_class:
		return str(plot_size_class)
	return f"~{round(flt(area) / 5) * 5:g}" if flt(area) else "?"


# ---------------------------------------------------------------- settings and data
def settings(society) -> frappe._dict:
	s = frappe.db.get_value("Society", society, ["ra_drop_own_percent", "ra_drop_peer_percent", "ra_history_months", "ra_max_estimates",
	                                             "ra_residential_max_kw", "ra_adjustment_limit", "ra_outlier_z"], as_dict=True) or frappe._dict()
	return frappe._dict(own_pct=flt(s.ra_drop_own_percent) or 50, peer_pct=flt(s.ra_drop_peer_percent) or 50,
	                    months=cint(s.ra_history_months) or 6, max_estimates=cint(s.ra_max_estimates) or 2,
	                    residential_kw=flt(s.ra_residential_max_kw) or 7, adjustment_limit=flt(s.ra_adjustment_limit), z=flt(s.ra_outlier_z) or 3)


def _bills(society, month):
	return frappe.db.sql("""select b.name, b.connection, case when ifnull(b.nm_method, '') != '' then b.import_units else b.units end units,
	                               b.current_amount, b.consumer_category, b.consumption_type,
	                               p.area, p.plot_size_class, c.current_meter
	                        from `tabUtility Bill` b left join `tabProperty` p on p.name = b.property join `tabConnection` c on c.name = b.connection
	                        where b.society = %s and b.billing_month = %s and b.docstatus = 1 and ifnull(b.superseded_by, '') = ''""",
	                     (society, month), as_dict=True)


def _history(society, month, months):
	"""{connection: [units of each earlier month, newest first]} over the history months."""
	start = add_months(month, -months)
	out = defaultdict(dict)
	for r in frappe.db.sql("""select connection, billing_month,
	                          sum(case when ifnull(nm_method, '') != '' then import_units else units end) units from `tabUtility Bill`
	                          where society = %s and docstatus = 1 and ifnull(superseded_by, '') = '' and billing_month >= %s and billing_month < %s
	                          group by connection, billing_month""", (society, start, month), as_dict=True):
		out[r.connection][getdate(r.billing_month)] = flt(r.units)
	return {c: [v for _k, v in sorted(m.items(), reverse=True)] for c, m in out.items()}


# ---------------------------------------------------------------- the checks: each yields finding dicts
def check_drop_and_outliers(society, month, s):
	bills = _bills(society, month)
	history = _history(society, month, s.months)
	peers = defaultdict(list)
	for b in bills:
		peers[(b.consumer_category, size_band(b.area, b.plot_size_class))].append(flt(b.units))
	for b in bills:
		hist = history.get(b.connection, [])
		own = average(hist)
		group = peers[(b.consumer_category, size_band(b.area, b.plot_size_class))]
		peer_median = statistics.median(group) if len(group) >= 3 else None
		rate = flt(b.current_amount) / flt(b.units) if flt(b.units) > 0 else 0
		if b.consumption_type != "Estimated" and is_drop(b.units, hist, peer_median, s.own_pct, s.peer_pct):
			yield dict(check="RA-01 Consumption drop", subject_type="Connection", subject=b.connection, connection=b.connection,
			           score=drop_score(b.units, own), amount=round(max(0, own - flt(b.units)) * (rate or 0), 2),
			           details=_("Billed {0} units against an average of {1} over {2} months; similar accounts used {3}.").format(
			               f"{flt(b.units):,.0f}", f"{own:,.0f}", len(hist), f"{peer_median:,.0f}" if peer_median is not None else _("(too few to compare)")),
			           evidence={"units": flt(b.units), "own_average": round(own, 2), "history": hist, "peer_median": peer_median})
			continue
		factor = multiplier_factor(b.units, own) if len(hist) >= 3 else None
		if factor:
			yield dict(check="RA-06 Wrong multiplier", subject_type="Meter" if b.current_meter else "Connection", subject=b.current_meter or b.connection,
			           connection=b.connection, score=80, amount=round(max(0, own - flt(b.units)) * (rate or 0), 2),
			           details=_("Units are {0} times the usual {1} ({2} units): check the meter multiplier.").format(
			               factor if factor > 0 else f"1/{-factor}", f"{own:,.0f}", f"{flt(b.units):,.0f}"),
			           evidence={"units": flt(b.units), "own_average": round(own, 2), "factor": factor})
			continue
		z = zscore(b.units, hist)
		if z is not None and abs(z) >= s.z and b.consumption_type != "Estimated":
			yield dict(check="RA-10 Consumption outlier", subject_type="Connection", subject=b.connection, connection=b.connection,
			           score=min(100, round(abs(z) * 15)), amount=round(max(0, own - flt(b.units)) * (rate or 0), 2),
			           details=_("Units {0} are {1} standard deviations from this account's own pattern (average {2}).").format(
			               f"{flt(b.units):,.0f}", f"{z:+.1f}", f"{own:,.0f}"),
			           evidence={"units": flt(b.units), "z": round(z, 2), "history": hist})


def check_reading_multiplier(society, month, s):
	"""RA-06 (second rule): the reading was taken with a multiplier other than the meter's own."""
	for r in frappe.db.sql("""select r.name, r.connection, r.meter, r.multiplier, m.multiplier meter_multiplier
	                          from `tabMeter Reading` r join `tabMeter` m on m.name = r.meter
	                          where r.society = %s and r.billing_month = %s and r.docstatus < 2 and ifnull(r.multiplier, 0) > 0
	                          and ifnull(m.multiplier, 0) > 0 and r.multiplier != m.multiplier""", (society, month), as_dict=True):
		yield dict(check="RA-06 Wrong multiplier", subject_type="Meter", subject=r.meter, connection=r.connection, score=90, amount=0,
		           details=_("Reading {0} used multiplier {1}; meter {2} has {3}.").format(r.name, f"{flt(r.multiplier):g}", r.meter, f"{flt(r.meter_multiplier):g}"),
		           evidence={"reading": r.name, "reading_multiplier": flt(r.multiplier), "meter_multiplier": flt(r.meter_multiplier)})


def check_unbilled(society, month, s):
	"""RA-02: active accounts with no bill in a month whose main run is posted (left out without being excluded or held), and valid
	readings with no bill."""
	runs = frappe.get_all("Bill Run", filters={"society": society, "billing_month": month, "docstatus": 1}, fields=["name", "utility"])
	if not runs:
		return
	excluded = set(frappe.get_all("Bill Run Exclusion", filters={"parent": ["in", [r.name for r in runs]]}, pluck="connection"))
	billed = set(frappe.get_all("Utility Bill", filters={"society": society, "billing_month": month, "docstatus": 1}, pluck="connection"))
	end = get_last_day(month)
	from utility_billing.utility_billing import flags  # noqa: PLC0415

	held = flags.held(society, flags.HOLD_BILLING, end)
	for c in frappe.get_all("Connection", filters={"society": society, "status": "Active", "utility": ["in", list({r.utility for r in runs})]},
	                        fields=["name", "connection_date"]):
		if c.name in billed or c.name in excluded or c.name in held:
			continue
		if c.connection_date and getdate(c.connection_date) > end:
			continue
		yield dict(check="RA-02 Not billed", subject_type="Connection", subject=c.name, connection=c.name, score=85, amount=0,
		           details=_("Active account with no bill for {0}, though the month's bill run is posted and the account wasn't excluded or held.").format(
		               getdate(month).strftime("%B %Y")), evidence={})
	for r in frappe.db.sql("""select r.name, r.connection from `tabMeter Reading` r join `tabReading Cycle` y on y.name = r.reading_cycle
	                          where r.society = %s and r.billing_month = %s and y.status = 'Closed' and r.status in ('Valid', 'Approved', 'Estimated')""",
	                       (society, month), as_dict=True):
		if r.connection not in billed and r.connection not in excluded and r.connection not in held:
			yield dict(check="RA-02 Not billed", subject_type="Meter Reading", subject=r.name, connection=r.connection, score=80, amount=0,
			           details=_("Reading {0} is in a closed cycle but no bill was made from it.").format(r.name), evidence={})


def _norm_serial(serial):
	return "".join(ch for ch in (serial or "").upper() if ch.isalnum()).lstrip("0")


def check_meter_records(society, month, s):
	"""RA-03: the same meter entered twice under different spellings, meters installed on no account, accounts with no meter."""
	meters = frappe.get_all("Meter", filters={"society": society}, fields=["name", "serial_no", "status", "current_connection"])
	seen = defaultdict(list)
	for m in meters:
		seen[_norm_serial(m.serial_no)].append(m.name)
	for key, names in seen.items():
		if key and len(names) > 1:
			yield dict(check="RA-03 Meter records", subject_type="Meter", subject=sorted(names)[0], connection=None, score=60, amount=0, month=None,
			           details=_("The same meter number appears {0} times: {1}.").format(len(names), ", ".join(sorted(names))), evidence={"meters": sorted(names)})
	active = {c.name: c for c in frappe.get_all("Connection", filters={"society": society}, fields=["name", "status", "current_meter", "utility"])}
	for m in meters:
		if m.status == "Installed":
			c = active.get(m.current_connection)
			if not c or c.status not in ("Active", "Applied", "Disconnected") or c.current_meter != m.name:  # a disconnected account keeps its meter
				yield dict(check="RA-03 Meter records", subject_type="Meter", subject=m.name, connection=m.current_connection, score=55, amount=0, month=None,
				           details=_("Meter {0} is recorded as installed but isn't the current meter of an active account.").format(m.name), evidence={})
	metered = set(frappe.get_all("Utility Type", filters={"is_metered": 1}, pluck="name"))
	for c in active.values():
		if c.status == "Active" and c.utility in metered and not c.current_meter:
			yield dict(check="RA-03 Meter records", subject_type="Connection", subject=c.name, connection=c.name, score=70, amount=0, month=None,
			           details=_("Active metered account with no meter."), evidence={})


def check_tariff_load(society, month, s):
	"""RA-04: a residential account drawing more than a house should, maximum demand above the sanctioned load, a shop or office
	billed as residential."""
	for c in frappe.db.sql("""select c.name, c.consumer_category, c.sanctioned_load_kw, c.last_mdi_kw, p.property_type
	                          from `tabConnection` c left join `tabProperty` p on p.name = c.property
	                          where c.society = %s and c.status = 'Active'""", society, as_dict=True):
		reasons = []
		residential = is_residential(c.consumer_category)
		load = max(flt(c.sanctioned_load_kw), flt(c.last_mdi_kw))
		if residential and load > s.residential_kw:
			reasons.append(_("residential, with a load of {0} kW (limit {1} kW)").format(f"{load:g}", f"{s.residential_kw:g}"))
		if flt(c.sanctioned_load_kw) > 0 and flt(c.last_mdi_kw) > flt(c.sanctioned_load_kw):
			reasons.append(_("maximum demand {0} kW is above the sanctioned {1} kW").format(f"{flt(c.last_mdi_kw):g}", f"{flt(c.sanctioned_load_kw):g}"))
		if residential and c.property_type in COMMERCIAL_PROPERTY:
			reasons.append(_("a {0} billed on a residential category").format(_(c.property_type).lower()))
		if reasons:
			yield dict(check="RA-04 Tariff or load mismatch", subject_type="Connection", subject=c.name, connection=c.name, score=50 + 15 * (len(reasons) - 1),
			           amount=0, details=_("Check the category and load: {0}.").format("; ".join(reasons)),
			           evidence={"sanctioned_kw": flt(c.sanctioned_load_kw), "mdi_kw": flt(c.last_mdi_kw), "property_type": c.property_type})


def check_estimates(society, month, s):
	"""RA-05: more estimated bills in a row than the society allows."""
	rows = frappe.db.sql("""select connection, consumption_type, billing_month from `tabUtility Bill`
	                        where society = %s and docstatus = 1 and ifnull(superseded_by, '') = '' and billing_month <= %s and billing_month > %s
	                        order by connection, billing_month desc""", (society, month, add_months(month, -12)), as_dict=True)
	by = defaultdict(list)
	for r in rows:
		by[r.connection].append(r)
	for conn, bills in by.items():
		if getdate(bills[0].billing_month) != getdate(month):
			continue
		n = consecutive_estimates([b.consumption_type for b in bills])
		if n > s.max_estimates:
			yield dict(check="RA-05 Repeated estimates", subject_type="Connection", subject=conn, connection=conn, score=min(100, 40 + 15 * n), amount=0,
			           details=_("{0} estimated bills in a row (the society allows {1}): the meter needs a real reading.").format(n, s.max_estimates),
			           evidence={"estimated_in_a_row": n})


def check_network(society, month, s):
	"""RA-07: feeders and transformers losing more than the society's High level."""
	for by in ("Feeder", "Transformer"):
		for r in losses.network_losses(society, month, by):
			if r["status"] == losses.HIGH and r["item"]:
				yield dict(check="RA-07 Network imbalance", subject_type=by, subject=r["item"], connection=None,
				           score=min(100, round(flt(r["loss_pct"]) * 3)), amount=0,
				           details=_("{0} {1}: {2} kWh in, {3} kWh billed, a loss of {4}%.").format(
				               _(by), r["name"], f"{r['input_kwh']:,.0f}", f"{r['billed_kwh']:,.0f}", f"{flt(r['loss_pct']):.1f}"),
				           evidence={k: r[k] for k in ("input_kwh", "billed_kwh", "loss_pct")})


def check_reversals(society, month, s):
	"""RA-08: users whose payment reversals, and makers or approvers whose adjustments, stand out in the month."""
	start, end = getdate(month), get_last_day(month)
	totals = defaultdict(float)
	counts = defaultdict(int)
	for r in frappe.db.sql("""select owner, abs(amount) amount from `tabCustomer Ledger Entry`
	                          where society = %s and entry_type = 'Reversal' and posting_date between %s and %s""", (society, start, end), as_dict=True):
		totals[("Reversals by", r.owner)] += flt(r.amount)
		counts[("Reversals by", r.owner)] += 1
	for a in frappe.db.sql("""select requested_by, owner, approved_by, amount from `tabAccount Adjustment`
	                          where society = %s and docstatus = 1 and posting_date between %s and %s""", (society, start, end), as_dict=True):
		totals[("Adjustments made by", a.requested_by or a.owner)] += flt(a.amount)
		counts[("Adjustments made by", a.requested_by or a.owner)] += 1
		if a.approved_by:
			totals[("Adjustments approved by", a.approved_by)] += flt(a.amount)
			counts[("Adjustments approved by", a.approved_by)] += 1
	flagged = {}
	for role in ("Reversals by", "Adjustments made by", "Adjustments approved by"):
		group = {k: v for k, v in totals.items() if k[0] == role}
		for k in outliers(group):
			flagged[k] = _("well above other users ({0} in the month)").format(f"Rs {group[k]:,.0f}")
	if s.adjustment_limit:
		for k, v in totals.items():
			if v > s.adjustment_limit:
				flagged.setdefault(k, _("above the society's limit of Rs {0}").format(f"{s.adjustment_limit:,.0f}"))
	for (role, user), why in flagged.items():
		yield dict(check="RA-08 Unusual reversals or adjustments", subject_type="User", subject=user, connection=None, score=60, amount=totals[(role, user)],
		           details=_("{0} {1}: {2} entries for Rs {3}, {4}.").format(_(role), user, counts[(role, user)], f"{totals[(role, user)]:,.0f}", why),
		           evidence={"role": role, "count": counts[(role, user)], "total": totals[(role, user)]}, key_extra=role)


SOLAR_HOURS_PER_DAY = 6  # generous: Pakistan averages about 5 peak sun hours a day
CREDIT_GROWING_MONTHS = 12


def credit_growing(carried, months=CREDIT_GROWING_MONTHS) -> bool:
	"""`carried`: solar units carried forward by each bill, newest first. True when each of the last `months` bills carried more than
	the one before it."""
	return len(carried) >= months + 1 and all(carried[i] > carried[i + 1] for i in range(months))


def check_solar(society, month, s):
	"""RA-11 (increment 28): an export reading on a meter that isn't a net-metering meter; exports beyond what the account's solar
	capacity (or, without one, its sanctioned load) could produce in the month; solar credit carried forward and growing for 12 months."""
	from frappe.utils import get_last_day  # noqa: PLC0415

	days = getdate(get_last_day(month)).day
	for r in frappe.db.sql("""select r.name, r.connection, r.meter, r.export_units, r.present_export_reading, m.meter_type
	                          from `tabMeter Reading` r join `tabMeter` m on m.name = r.meter
	                          where r.society = %s and r.billing_month = %s and r.docstatus < 2
	                          and (ifnull(r.present_export_reading, 0) > 0 or ifnull(r.export_units, 0) > 0)
	                          and ifnull(m.meter_type, '') != 'Net Metering'""", (society, month), as_dict=True):
		yield dict(check="RA-11 Solar export anomaly", subject_type="Meter", subject=r.meter, connection=r.connection, score=90, amount=0,
		           details=_("Reading {0} has an export reading of {1} on meter {2}, which is not a net-metering meter ({3}).").format(
		               r.name, f"{flt(r.present_export_reading):,.0f}", r.meter, r.meter_type or _("no type")),
		           evidence={"reading": r.name, "export_units": flt(r.export_units), "meter_type": r.meter_type}, key_extra="type")
	for b in frappe.db.sql("""select b.connection, b.export_units, b.import_units, b.current_amount, c.solar_capacity_kw, c.sanctioned_load_kw
	                          from `tabUtility Bill` b join `tabConnection` c on c.name = b.connection
	                          where b.society = %s and b.billing_month = %s and b.docstatus = 1 and ifnull(b.superseded_by, '') = ''
	                          and ifnull(b.nm_method, '') != ''""", (society, month), as_dict=True):
		kw = flt(b.solar_capacity_kw) or flt(b.sanctioned_load_kw)
		most = kw * SOLAR_HOURS_PER_DAY * days
		if kw and flt(b.export_units) > most:
			yield dict(check="RA-11 Solar export anomaly", subject_type="Connection", subject=b.connection, connection=b.connection, score=70, amount=0,
			           details=_("Exported {0} units; {1} kW of {2} could produce at most about {3} in the month.").format(
			               f"{flt(b.export_units):,.0f}", f"{kw:g}", _("solar capacity") if flt(b.solar_capacity_kw) else _("sanctioned load"),
			               f"{most:,.0f}"),
			           evidence={"export_units": flt(b.export_units), "capacity_kw": kw, "max_units": most}, key_extra="capacity")
	rows = frappe.db.sql("""select connection, billing_month, units_carried_out from `tabUtility Bill`
	                        where society = %s and docstatus = 1 and ifnull(superseded_by, '') = '' and ifnull(nm_method, '') != ''
	                        and billing_month <= %s and billing_month > %s order by connection, billing_month desc""",
	                     (society, month, add_months(month, -CREDIT_GROWING_MONTHS - 1)), as_dict=True)
	by = defaultdict(list)
	for r in rows:
		by[r.connection].append(r)
	for conn, bills in by.items():
		if getdate(bills[0].billing_month) != getdate(month):
			continue
		carried = [flt(b.units_carried_out) for b in bills[:CREDIT_GROWING_MONTHS + 1]]  # newest first
		if credit_growing(carried):
			yield dict(check="RA-11 Solar export anomaly", subject_type="Connection", subject=conn, connection=conn, score=50, amount=0,
			           details=_("Solar credit carried forward has grown every month for {0} months, to {1} units: exports keep exceeding use. "
			                     "Check the meter and the solar installation.").format(CREDIT_GROWING_MONTHS, f"{carried[0]:,.0f}"),
			           evidence={"carried_forward": carried}, key_extra="growing")


CHECKS = (check_drop_and_outliers, check_reading_multiplier, check_unbilled, check_meter_records, check_tariff_load, check_estimates,
          check_network, check_reversals, check_solar)


# ---------------------------------------------------------------- run
def finding_key(society, check, subject_type, subject, month, extra=""):
	return "|".join([society, check[:5], subject_type or "", subject or "", str(month or ""), extra or ""])[:140]


def record(society, month, f) -> str:
	"""Create the finding, or refresh an open one's figures. Returns 'new', 'updated' or 'kept' (closed, left alone)."""
	m = f.get("month", month)
	key = finding_key(society, f["check"], f["subject_type"], f["subject"], m, f.get("key_extra"))
	name = frappe.db.get_value("RA Finding", {"finding_key": key})
	values = {"score": cint(f["score"]), "amount_at_risk": flt(f["amount"]), "details": f["details"], "evidence": json.dumps(f.get("evidence") or {}, default=str)}
	if name:
		if frappe.db.get_value("RA Finding", name, "status") == "Closed":
			return "kept"
		frappe.db.set_value("RA Finding", name, values)
		return "updated"
	doc = frappe.get_doc({"doctype": "RA Finding", "society": society, "check_type": f["check"], "billing_month": m, "subject_type": f["subject_type"],
	                      "subject": f["subject"], "connection": f.get("connection"), "finding_key": key, **values})
	doc.flags.ignore_links = True  # the subject can be a user who has since left, or a record renamed since
	doc.insert(ignore_permissions=True)
	return "new"


def run_checks(society, month) -> dict:
	"""Run every check for one society and month; {check: {new, updated, kept}} and the totals."""
	month = month_start(month)
	s = settings(society)
	summary = defaultdict(lambda: defaultdict(int))
	for check in CHECKS:
		for f in check(society, month, s):
			summary[f["check"]][record(society, month, f)] += 1
	return {"society": society, "month": str(month), "by_check": {k: dict(v) for k, v in summary.items()},
	        "new": sum(v["new"] for v in summary.values()), "updated": sum(v["updated"] for v in summary.values())}


@frappe.whitelist()
def run_now(society: str, month: str | None = None) -> dict:
	"""The Run Checks button: a Billing Manager, General Manager or Recovery Officer runs the checks for a month (default: the
	latest month with posted bills)."""
	if not rights.can("ra.work"):
		raise frappe.PermissionError(_("Only a Billing Manager, General Manager or Recovery Officer runs the revenue assurance checks."))
	frappe.has_permission("Society", "read", society, throw=True)
	month = month or frappe.db.get_value("Utility Bill", {"society": society, "docstatus": 1}, "billing_month", order_by="billing_month desc")
	if not month:
		frappe.throw(_("No posted bills yet: there is nothing to check."))
	return run_checks(society, month)


def monthly():
	"""Scheduler: on the 1st, check last month for every society with posted bills."""
	if getdate(now_datetime()).day != 1:
		return
	month = month_start(add_months(getdate(now_datetime()), -1))
	for society in frappe.get_all("Society", pluck="name"):
		if frappe.db.exists("Utility Bill", {"society": society, "billing_month": month, "docstatus": 1}):
			try:
				run_checks(society, month)
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title=f"Revenue assurance checks failed for {society}")


def open_findings(society) -> tuple[int, float]:
	"""(number of open findings, their revenue at risk) for the dashboard."""
	r = frappe.db.sql("""select count(*), sum(amount_at_risk) from `tabRA Finding` where society = %s and status != 'Closed'""", society)[0]
	return cint(r[0]), flt(r[1])
