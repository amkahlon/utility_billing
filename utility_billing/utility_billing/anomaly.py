# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Consumption anomaly and theft suspicion (AI analytics 3; increment 24). A score per account from rules, no model: what stands out in
its own readings and against similar properties. The score is a reason to inspect, never proof: nobody is accused by it, and the
written explanation (ai.explain_account) only words the reasons found here.

Rules (points, capped at 100):
- **Drop**: the last 3 months average under half of the 9 months before (which averaged 50+ units): 30.
- **Stuck**: zero units for the last 3 months while the account is active: 25.
- **Flat**: the same units 4 months running (above zero): 20.
- **Far below similar properties**: last month under 30% of the median of 5+ active accounts of the same category and plot size: 25.
- **Repeated estimates**: 3+ of the last 6 months estimated: 15.
- **Drop after a meter change**: the meter changed in the last 4 months and units fell by half since: 15."""

import statistics
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

from utility_billing.utility_billing import mis
from utility_billing.utility_billing import rights

VIEWERS = set(mis.RECOVERY + ["Billing Officer", "Billing Manager"])
DONE = ("Valid", "Approved", "Estimated")
MIGRATED = "Migrated"  # a month billed by the previous system


def require_viewer():
	if not rights.can("anomaly.view"):
		raise frappe.PermissionError(_("You don't have permission to see theft suspicion scores."))


def histories(society, month, connections=None):
	"""{connection: [(billing_month, units, status)]} for the 12 months up to `month`, oldest first: the readings, and the previous
	system's bills for the months without one."""
	start = add_months(month, -11)
	cond = " and r.connection in %(c)s" if connections else ""
	values = {"s": society, "a": start, "b": month, "st": DONE, "c": tuple(connections or ())}
	out = defaultdict(list)
	for r in frappe.db.sql(f"""select r.connection, r.billing_month, r.units, r.status from `tabMeter Reading` r
	                          where r.society = %(s)s and r.billing_month between %(a)s and %(b)s and r.status in %(st)s {cond}
	                          order by r.billing_month""", values, as_dict=True):
		out[r.connection].append((getdate(r.billing_month), flt(r.units), r.status))
	# months before go-live: the units the previous system billed (increment 64), so a year's pattern is there from the first month
	from utility_billing.utility_billing.mis_more import solar_accounts  # noqa: PLC0415

	read = {c: {m for m, _u, _s in h} for c, h in out.items()}
	solar = solar_accounts(society)  # the previous system's units for these are net of exports: not comparable with readings
	for r in frappe.db.sql(f"""select r.connection, r.billing_month, sum(r.units) units from `tabLegacy Bill` r
	                          where r.society = %(s)s and r.billing_month between %(a)s and %(b)s {cond}
	                          group by r.connection, r.billing_month""", values, as_dict=True):
		if r.connection not in solar and getdate(r.billing_month) not in read.get(r.connection, ()):
			out[r.connection].append((getdate(r.billing_month), flt(r.units), MIGRATED))
	for h in out.values():
		h.sort(key=lambda x: x[0])
	return out


def reasons_for(hist, peer_median=None, changed_on=None, month=None):
	"""(score, [reasons]) for one account's history."""
	units = [u for _m, u, _s in hist]
	reasons, score = [], 0
	if len(units) >= 6:
		recent, before = units[-3:], units[:-3][-9:]
		avg_r, avg_b = sum(recent) / 3, sum(before) / len(before)
		if avg_b >= 50 and avg_r < avg_b / 2:
			score += 30
			reasons.append(_("Units fell from about {0} to {1} a month over the last 3 months").format(round(avg_b), round(avg_r)))
	if len(units) >= 3 and all(u == 0 for u in units[-3:]):
		score += 25
		reasons.append(_("No units for the last 3 months while active"))
	elif len(units) >= 4 and units[-1] > 0 and len(set(units[-4:])) == 1:
		score += 20
		reasons.append(_("The same {0} units 4 months running").format(round(units[-1])))
	if peer_median and units and units[-1] < 0.3 * peer_median:
		score += 25
		reasons.append(_("Last month {0} units against about {1} for similar properties").format(round(units[-1]), round(peer_median)))
	estimated = sum(1 for _m, _u, st in hist[-6:] if st == "Estimated")
	if estimated >= 3:
		score += 15
		reasons.append(_("{0} of the last 6 months estimated").format(estimated))
	if changed_on and month and len(units) >= 2:
		after = [u for m, u, _s in hist if m > getdate(changed_on)]
		before = [u for m, u, _s in hist if m <= getdate(changed_on)][-3:]
		if after and before and getdate(changed_on) >= add_months(month, -4) and sum(after) / len(after) < (sum(before) / len(before)) / 2:
			score += 15
			reasons.append(_("Units halved after the meter change on {0}").format(frappe.utils.formatdate(changed_on)))
	return min(score, 100), reasons


def scores(society, month=None, min_score=1) -> list[dict]:
	month = mis.month_start(month or add_months(getdate(nowdate()).replace(day=1), -1))
	conns = frappe.db.sql("""select c.name, c.consumer_category category, p.plot_size_class plot, c.consumer_name, c.property
	                         from `tabConnection` c left join `tabProperty` p on p.name = c.property
	                         where c.society = %s and c.status = 'Active'""", society, as_dict=True)
	hist = histories(society, month)
	peers = defaultdict(list)
	for c in conns:
		h = hist.get(c.name)
		if h and h[-1][0] == month:
			peers[(c.category, c.plot or "")].append(h[-1][1])
	medians = {k: statistics.median(v) for k, v in peers.items() if len(v) >= 5}
	changes = dict(frappe.db.sql("""select m.parent, max(m.installed_on) from `tabConnection Meter` m join `tabConnection` c on c.name = m.parent
	                                where c.society = %s and m.start_reading is not null and m.idx > 1 group by m.parent""", society))
	out = []
	for c in conns:
		h = hist.get(c.name) or []
		score, why = reasons_for(h, medians.get((c.category, c.plot or "")), changes.get(c.name), month)
		if score >= min_score:
			out.append({"connection": c.name, "consumer_name": c.consumer_name, "property": c.property, "category": c.category, "score": score,
			            "reasons": why, "history": ", ".join(str(round(u)) for _m, u, _s in h)})
	return sorted(out, key=lambda r: (-r["score"], r["connection"]))


def score_account(connection):
	c = frappe.db.get_value("Connection", connection, ["society"], as_dict=True)
	if not c:
		return None
	return next((r for r in scores(c.society, min_score=0) if r["connection"] == connection), None)
