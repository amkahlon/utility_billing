"""Demo data on the QA site (user's request, 2026-11-15): keep one QA society, delete every other test society, and give the kept one
50 consumers with seven months of readings, bills, payments, surcharges, workflows and losses, so the dashboards have something to show.
Then time a bill run of the 50 connections.

QA site only (never ub.localhost). Run from ~/frappe-bench/sites:
    ../env/bin/python ../apps/utility_billing/dev/demo_data.py purge     # delete the other societies (asks nothing: the user decided)
    ../env/bin/python ../apps/utility_billing/dev/demo_data.py seed      # the 50 consumers and their history
"""

import random
import sys
import time

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, add_months, flt, get_last_day, getdate

SITE = "qa.localhost"
USERS = {k: f"qa.{k}@example.com" for k in ("bo", "bm", "gm", "ao", "rec", "cashier")}
MONTHS = [f"2026-{m:02d}-01" for m in range(4, 11)]  # April to October 2026 (March is closed on the kept society)
BLOCKS = ("A", "B", "C", "D", "E")
R = random.Random(42)


def connect():
	frappe.init(site=SITE, sites_path=".")
	frappe.connect()
	assert frappe.local.site == SITE, "the demo data is only for the QA site"
	frappe.set_user("Administrator")


def kept_society():
	"""The newest QA society that has posted bills (a complete end-to-end run)."""
	rows = frappe.db.sql("""select s.name from `tabSociety` s where s.name like 'QA%%'
	                        and exists (select 1 from `tabUtility Bill` b where b.society = s.name and b.docstatus = 1)
	                        order by s.creation desc limit 1""")
	return rows[0][0]


# ---------------------------------------------------------------- purge
def purge():
	connect()
	keep = kept_society()
	doctypes = [d for d in frappe.get_all("DocType", filters={"istable": 0, "issingle": 0, "custom": 0}, pluck="name")
	            if frappe.db.table_exists(d) and frappe.get_meta(d).has_field("society") and d != "Society"]
	removed = 0
	for dt in doctypes:
		names = frappe.get_all(dt, filters={"society": ["!=", keep]}, pluck="name")
		if not names:
			continue
		for t in frappe.get_meta(dt).get_table_fields():
			for i in range(0, len(names), 500):
				frappe.db.delete(t.options, {"parenttype": dt, "parent": ["in", names[i:i + 500]]})
		for i in range(0, len(names), 500):
			frappe.db.delete(dt, {"name": ["in", names[i:i + 500]]})
		removed += len(names)
		print(f"{dt}: {len(names)}")
	others = frappe.get_all("Society", filters={"name": ["!=", keep]}, pluck="name")
	for t in frappe.get_meta("Society").get_table_fields():
		frappe.db.delete(t.options, {"parenttype": "Society", "parent": ["in", others or [""]]})
	frappe.db.delete("Society", {"name": ["in", others or [""]]})
	frappe.db.delete("User Permission", {"allow": "Society", "for_value": ["in", others or [""]]})
	# consumers no longer linked to anything, and masters only the deleted societies used
	frappe.db.sql("""delete from `tabConsumer` where name not in (select consumer from `tabConnection` where consumer is not null)
	                 and name not in (select consumer from `tabProperty Occupancy` where consumer is not null)
	                 and name not in (select ifnull(new_consumer, '') from `tabService Request`)""")
	used_cats = {x for x in frappe.db.sql_list("select consumer_category from `tabConnection` union select consumer_category from `tabTariff`"
	                                            " union select consumer_category from `tabUtility Bill`") if x}
	for c in frappe.get_all("Consumer Category", pluck="name"):
		if c not in used_cats:
			frappe.db.delete("Consumer Category", {"name": c})
	soc = frappe.get_doc("Society", keep)
	used_ct = {x for x in frappe.db.sql_list("""select charge_type from `tabTariff Charge` union select charge_type from `tabBill Line`
	                                             union select charge_type from `tabTariff Adjustment`""") if x}
	used_ct |= {soc.energy_charge_type, "Energy Charges", "Fuel Price Adjustment", "Quarterly Tariff Adjustment", "Fixed Charges", "Maintenance Charges"}
	for c in frappe.get_all("Charge Type", pluck="name"):
		if c not in used_ct:
			frappe.db.delete("Charge Type", {"name": c})
	for dt in frappe.get_all("DocType", filters={"module": "Utility Billing", "istable": 0, "issingle": 0}, pluck="name"):
		frappe.db.sql(f"""delete from `tabNotification Log` where document_type = %s and document_name not in (select name from `tab{dt}`)""", dt)
	frappe.db.commit()
	print("kept", keep, "removed", removed, "documents and", len(others), "societies")


# ---------------------------------------------------------------- seed
def as_user(key, fn, *a, **kw):
	frappe.set_user(USERS[key])
	try:
		return fn(*a, **kw)
	finally:
		frappe.set_user("Administrator")


def act(doc, action, key):
	doc.reload()
	return as_user(key, apply_workflow, doc, action)


def ensure_category(name, utility="Electricity"):
	cid = f"{utility}-{name}"
	if not frappe.db.exists("Consumer Category", cid):
		frappe.get_doc({"doctype": "Consumer Category", "category_name": name, "utility": utility}).insert()
	return cid


def ensure_tariff(soc, category, rate, charges):
	from utility_billing.utility_billing.doctype.tariff.tariff import get_applicable_tariff

	if get_applicable_tariff(soc, "Electricity", category, "2026-04-01"):
		return
	t = frappe.get_doc({"doctype": "Tariff", "society": soc, "utility": "Electricity", "consumer_category": category, "effective_from": "2026-01-01",
	                    "pricing_method": "Flat rate", "flat_rate": rate, "charges": charges}).insert()
	act(t, "Submit for Approval", "bm")
	act(t, "Approve", "gm")


def seed():
	from utility_billing.utility_billing import account, billing, readings

	connect()
	soc_name = kept_society()
	soc = frappe.get_doc("Society", soc_name)
	print("seeding", soc_name, soc.society_name)
	first = frappe.get_all("Connection", filters={"society": soc_name}, fields=["consumer_category", "transformer", "feeder"], limit=1)[0]
	res_cat = first.consumer_category
	com_cat = ensure_category(f"Commercial {soc_name}")
	ca_cat = ensure_category(f"Common Area {soc_name}")
	tariff = frappe.get_doc("Tariff", frappe.get_all("Tariff", filters={"society": soc_name, "consumer_category": res_cat, "docstatus": 1},
	                                                  pluck="name", order_by="effective_from asc", limit=1)[0])
	fixed = [{"charge_type": c.charge_type, "rate": c.rate} for c in tariff.charges]
	ensure_tariff(soc_name, com_cat, 85, fixed)
	ensure_tariff(soc_name, ca_cat, 60, [])

	# network: two feeders with three transformers each
	feeders = []
	for fcode in ("F1", "F2"):
		name = f"{soc_name}-{fcode}"
		if not frappe.db.exists("Feeder", name):
			frappe.get_doc({"doctype": "Feeder", "society": soc_name, "feeder_code": fcode, "feeder_name": f"Feeder {fcode}"}).insert()
		feeders.append(name)
	transformers = []
	for i in range(6):
		code = f"T{i + 1}"
		name = f"{soc_name}-{code}"
		if not frappe.db.exists("Transformer", name):
			frappe.get_doc({"doctype": "Transformer", "society": soc_name, "transformer_code": code, "feeder": feeders[i // 3], "kva_rating": R.choice((100, 200, 200))}).insert()
		transformers.append(name)

	# 50 consumers, properties, meters and connections
	conns = []
	for i in range(1, 51):
		block = BLOCKS[(i - 1) // 10]
		kind = "Common" if i in (10, 30) else ("Commercial" if i % 7 == 0 else "Residential")
		cat = {"Common": ca_cat, "Commercial": com_cat, "Residential": res_cat}[kind]
		cname = f"{'Society ' + ('Tube Well' if i == 10 else 'Street Lights') if kind == 'Common' else R.choice(FIRST) + ' ' + R.choice(LAST)}"
		cons = frappe.get_doc({"doctype": "Consumer", "consumer_name": cname, "consumer_type": "Company" if kind != "Residential" else "Individual",
		                       "id_type": "CNIC", "id_number": f"35202-{R.randint(1000000, 9999999)}-{R.randint(1, 9)}", "mobile_no": f"0300-{R.randint(1000000, 9999999)}",
		                       "preferred_language": R.choice(("English", "Urdu")), "notify_sms": 1, "tax_status": R.choice(("Filer", "Filer", "Non-filer"))}).insert()
		prop = frappe.get_doc({"doctype": "Property", "society": soc_name, "property_no": f"{block}-{100 + i}", "block": block, "sector": "Phase 1",
		                       "area": R.choice((5, 10, 20)), "area_unit": "Marla", "plot_size_class": R.choice(("5 Marla", "10 Marla", "1 Kanal")),
		                       "occupants": [{"consumer": cons.name, "occupant_role": "Owner", "from_date": "2024-01-01", "share_percent": 100, "is_billed": 1,
		                                      "liable_for_arrears": 1}]}).insert()
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": f"{soc_name}-DM{i:03d}", "society": soc_name, "utility": "Electricity", "make": R.choice(("Elster", "Landis+Gyr", "Microtech")),
		                        "meter_type": "Single phase" if kind == "Residential" else "Three phase", "digits": 6, "multiplier": 1}).insert()
		start = R.randint(1000, 9000)
		conn = frappe.get_doc({"doctype": "Connection", "society": soc_name, "property": prop.name, "consumer": cons.name, "utility": "Electricity",
		                       "consumer_category": cat, "status": "Active", "connection_date": "2025-01-01", "transformer": transformers[(i - 1) // 9],
		                       "feeder": feeders[0 if (i - 1) // 9 < 3 else 1], "sanctioned_load_kw": R.choice((3, 5, 7.5)) if kind == "Residential" else 15,
		                       "phase": "Single Phase" if kind == "Residential" else "Three Phase",
		                       "meters": [{"meter": meter.name, "installed_on": "2025-01-01", "start_reading": start}]}).insert()
		base = {"Residential": R.randint(250, 650), "Commercial": R.randint(900, 1600), "Common": R.randint(1200, 2000)}[kind]
		behaviour = "common" if kind == "Common" else R.choices(("prompt", "late", "partial", "defaulter"), (68, 15, 10, 7))[0]
		conns.append({"name": conn.name, "base": base, "reading": start, "behaviour": behaviour, "kind": kind})
	frappe.db.commit()
	print("50 connections made")

	# seven months: readings, FPA, bill run, payments, surcharges, DISCO bill
	season = {4: 0.85, 5: 1.15, 6: 1.4, 7: 1.5, 8: 1.45, 9: 1.2, 10: 0.95}
	timings = []
	for month in MONTHS:
		m = getdate(month)
		cycle_name = frappe.db.get_value("Reading Cycle", {"society": soc_name, "utility": "Electricity", "billing_month": month})
		if not cycle_name:
			cycle_name = frappe.get_doc({"doctype": "Reading Cycle", "society": soc_name, "utility": "Electricity", "billing_month": month}).insert().name
		frappe.db.set_value("Reading Cycle", cycle_name, "status", "Open")
		readings.create_reading_sheet(cycle_name)
		for c in conns:
			units = max(20, int(c["base"] * season[m.month] * R.uniform(0.85, 1.15)))
			if c["name"] == conns[24]["name"] and m.month == 9:
				units = int(units * 0.3)  # a sudden drop for the theft-suspicion view
			c["reading"] += units
			r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle_name, "connection": c["name"]})
			r.present_reading, r.reading_date = c["reading"], f"{month[:8]}25"
			r.mdi_kw = round(units / 720 * R.uniform(3, 6), 2)
			r.save()
			if r.status == "Flagged":
				readings.approve_reading(r.name, "Checked on site")
		for r in frappe.get_all("Meter Reading", filters={"reading_cycle": cycle_name, "status": ["in", ["Pending", "Re-read Requested", "Flagged"]]}, pluck="name"):
			readings.approve_reading(r, "Checked on site") if frappe.db.get_value("Meter Reading", r, "status") == "Flagged" else None
		readings.estimate_unread(cycle_name)
		readings.close_cycle(cycle_name)
		if soc.charges_fpa and not frappe.db.exists("Tariff Adjustment", {"society": soc_name, "billing_month": month, "adjustment_type": "FPA", "docstatus": 1}):
			adj = as_user("bm", lambda: frappe.get_doc({"doctype": "Tariff Adjustment", "adjustment_type": "FPA", "society": soc_name, "utility": "Electricity",
			                                            "billing_month": month, "rate_per_unit": round(R.uniform(0.8, 3.2), 2),
			                                            "charge_type": frappe.db.get_value("Tariff Adjustment", {"society": soc_name}, "charge_type")
			                                            or "Fuel Price Adjustment"}).insert())
			act(adj, "Submit for Approval", "bm")
			act(adj, "Approve", "gm")
		issue = add_months(m, 1)
		run = as_user("bo", lambda: frappe.get_doc({"doctype": "Bill Run", "society": soc_name, "utility": "Electricity", "billing_month": month,
		                                            "issue_date": issue}).insert())
		t0 = time.time()
		as_user("bo", billing.calculate_run, run.name)
		t1 = time.time()
		act(run, "Submit for Approval", "bo")
		act(run, "Approve", "bm")
		t2 = time.time()
		run.reload()
		timings.append((month, run.bill_count, round(t1 - t0, 2), round(t2 - t1, 2)))
		due = getdate(run.due_date)
		for c in conns:
			b = frappe.db.get_value("Utility Bill", {"bill_run": run.name, "connection": c["name"]}, ["name", "payable_within_due", "current_amount"], as_dict=True)
			if not b:
				continue
			kind, owed = c["behaviour"], flt(account.balance(c["name"]))
			if kind == "defaulter" or owed <= 0:
				continue
			if kind == "prompt" or kind == "common":
				pay_on, amount = add_days(due, -R.randint(1, 10)), owed
			elif kind == "late":
				pay_on, amount = add_days(due, R.randint(3, 12)), owed
			else:
				pay_on, amount = add_days(due, -R.randint(0, 5)), round(owed * R.uniform(0.4, 0.8))
			if getdate(pay_on) > getdate(frappe.utils.nowdate()):
				continue
			frappe.get_doc({"doctype": "Payment Receipt", "reference_no": c["name"], "payment_channel": "Bank / Channel", "payment_date": pay_on,
			                "amount": amount, "mode": R.choice(("Bank Transfer", "Bank Transfer", "1Link", "Kuickpay", "Cheque")),
			                "channel_txn_id": f"DEMO-{c['name']}-{month}"}).insert().submit()
		account.apply_surcharges(as_of=add_days(due, 20), society=soc_name)
		billed_units = frappe.db.sql("select sum(units) from `tabUtility Bill` where bill_run = %s", run.name)[0][0] or 0
		if not frappe.db.exists("DISCO Bill", {"society": soc_name, "billing_month": month}):
			inp = flt(billed_units) * R.uniform(1.08, 1.16)
			frappe.get_doc({"doctype": "DISCO Bill", "society": soc_name, "utility": "Electricity", "billing_month": month, "disco_name": "LESCO",
			                "bill_no": f"LESCO-{month[:7]}", "units_kwh": round(inp), "energy_amount": round(inp * 48), "fpa_amount": round(inp * 1.9),
			                "inputs": [{"feeder": f, "input_kwh": round(inp * share)} for f, share in zip(feeders, (0.55, 0.45), strict=True)]}).insert()
		frappe.db.commit()
		print(month, "bills", run.bill_count, "calculate", timings[-1][2], "s, approve and post", timings[-1][3], "s")

	extras(soc_name, conns)
	frappe.db.commit()
	print("timings (month, bills, calculate s, approve+post s):", timings)


def extras(soc_name, conns):
	"""Workflows on top of the history: plans, write-off, deposits, flags, routes, requests, a recovery run, a revenue target."""
	from utility_billing.utility_billing import servicereq

	servicereq.ensure_types(soc_name)
	defaulters = [c["name"] for c in conns if c["behaviour"] == "defaulter"]
	prompt = [c["name"] for c in conns if c["behaviour"] == "prompt"]
	# security deposits received for ten accounts
	for name in prompt[:10]:
		d = as_user("ao", lambda n=name: frappe.get_doc({"doctype": "Security Deposit", "connection": n, "deposit_type": "Received", "amount": 5000,
		                                                  "mode": "Bank Transfer", "posting_date": "2026-04-05"}).insert())
		act(d, "Submit for Approval", "ao")
		act(d, "Approve", "gm")
	# flags and routes
	for name, flag in ((defaulters[0], "DISPUTE") if defaulters else (None, None), (prompt[11], "STAFF")):
		if name:
			c = frappe.get_doc("Connection", name)
			c.append("connection_flags", {"flag": flag, "from_date": "2026-09-01", "remarks": "Demo"})
			c.save()
	for code, block_conns in (("R-AB", [c["name"] for c in conns[:20]]), ("R-CDE", [c["name"] for c in conns[20:]])):
		if not frappe.db.exists("Reading Route", f"{soc_name}-Electricity-{code}"):
			frappe.get_doc({"doctype": "Reading Route", "society": soc_name, "utility": "Electricity", "route_code": code,
			                "stops": [{"sequence": i + 1, "connection": n} for i, n in enumerate(block_conns)]}).insert()
	# a revenue target for the year
	if not frappe.db.exists("Revenue Target", f"{soc_name}-2026"):
		billed = {str(r[0]): flt(r[1]) for r in frappe.db.sql("""select billing_month, sum(current_amount) from `tabUtility Bill`
		                                                         where society = %s and docstatus = 1 group by billing_month""", soc_name)}
		frappe.get_doc({"doctype": "Revenue Target", "society": soc_name, "target_year": 2026,
		                "months": [{"month": m, "billed": round(billed.get(m, 0) * 1.05, -3), "collected": round(billed.get(m, 0) * 0.95, -3)} for m in MONTHS]}).insert()
	# service requests: a completed disconnection of a defaulter, a contact update, an open new connection
	if len(defaulters) > 1:
		sr = as_user("bo", lambda: frappe.get_doc({"doctype": "Service Request", "request_type": f"{soc_name}-DISC", "connection": defaulters[1],
		                                           "disconnection_reason": "Defaulter", "requested_on": "2026-10-20", "effective_date": "2026-11-01"}).insert())
		as_user("bm", servicereq.advance, sr.name, 1, "Three notices unanswered")
		as_user("rec", servicereq.advance, sr.name, 2, "Supply cut", 0)
	sr = as_user("bo", lambda: frappe.get_doc({"doctype": "Service Request", "request_type": f"{soc_name}-CONTACT", "connection": prompt[2],
	                                           "new_mobile": "0321-5550101"}).insert())
	as_user("bo", servicereq.advance, sr.name, 1, "At the counter")
	print("extras done")


FIRST = ("Ahmed", "Ayesha", "Bilal", "Fatima", "Hamza", "Hina", "Imran", "Kashif", "Mariam", "Nadia", "Omar", "Rabia", "Saad", "Sana", "Tariq",
         "Usman", "Zainab", "Zeeshan", "Asad", "Mehwish")
LAST = ("Khan", "Malik", "Qureshi", "Butt", "Chaudhry", "Sheikh", "Raza", "Hussain", "Javed", "Iqbal", "Siddiqui", "Mirza")

if __name__ == "__main__":
	{"purge": purge, "seed": seed}[sys.argv[1]]()
