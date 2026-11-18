"""Bill run benchmark (FS-RUN-07: 20,000 bills in under 10 minutes). Developer tool; test site only, never ub.localhost.

	cd ~/frappe-bench/sites && ../env/bin/python ../apps/utility_billing/dev/bench_billing.py test.localhost 20000

Builds society BENCH with N accounts (one meter each, read once), times the bill run's calculation and posting, prints the
timings and removes everything it made (pass --keep to look at it afterwards)."""

import random
import sys
import time

import frappe

SOC, MONTH, ISSUE = "BENCH", "2026-07-01", "2026-08-01"


def counted(label, fn, *a, **kw):
	"""--queries: the statements a step runs, grouped by their shape, most frequent first."""
	import re
	from collections import Counter

	seen, real = Counter(), frappe.db.sql

	def sql(query, *args, **kwargs):
		seen[re.sub(r"\s+", " ", re.sub(r"'[^']*'|\b\d+(\.\d+)?\b", "?", str(query)))[:150]] += 1
		return real(query, *args, **kwargs)

	frappe.db.sql = sql
	try:
		return fn(*a, **kw)
	finally:
		frappe.db.sql = real
		print(f"---- queries of {label}: {sum(seen.values())}", flush=True)
		for q, n in seen.most_common(45):
			print(f"{n:7d}  {q}", flush=True)


def timed(label, fn, *a, **kw):
	t = time.time()
	if "--queries" in sys.argv and label.startswith(("calculate", "post")):
		out = counted(label, fn, *a, **kw)
		secs = time.time() - t
	elif "--profile" in sys.argv and label.startswith(("calculate", "post")):
		import cProfile
		import pstats

		prof = cProfile.Profile()
		out = prof.runcall(fn, *a, **kw)
		secs = time.time() - t
		prof.dump_stats(f"/tmp/ub-{label.split()[0]}.prof")
		print(f"---- profile of {label}", flush=True)
		pstats.Stats(prof).sort_stats("cumulative").print_stats(r"utility_billing|frappe/model/(document|base_document|naming)|database", 45)
		pstats.Stats(prof).sort_stats("cumulative").print_callers(r"database.py:\d+\(get_value\)")
	else:
		out = fn(*a, **kw)
		secs = time.time() - t
	print(f"{label}: {secs:,.1f} s", flush=True)
	return out, secs


def setup(n):
	from utility_billing.install import create_default_exception_codes, create_default_utilities
	from utility_billing.utility_billing.testing import approve, make_users

	create_default_utilities()
	create_default_exception_codes()
	make_users()
	for name, group, basis, order in (("BENCH Energy", "Electricity", "Per Unit", 10), ("BENCH Service", "Society", "Fixed", 40)):
		if not frappe.db.exists("Charge Type", name):
			frappe.get_doc({"doctype": "Charge Type", "charge_name": name, "charge_group": group, "calculation_basis": basis, "bill_order": order}).insert()
	if not frappe.db.exists("Society", SOC):
		frappe.get_doc({"doctype": "Society", "society_name": "Benchmark Society", "short_name": SOC, "energy_charge_type": "BENCH Energy",
		                "surcharge_rate": 10, "days_to_due_date": 14}).insert()
	frappe.db.set_value("Society", SOC, {f: 0 for f in ("charges_fpa", "charges_qta") if frappe.get_meta("Society").has_field(f)})
	cat = "Electricity-BENCH Residential"
	if not frappe.db.exists("Consumer Category", cat):
		frappe.get_doc({"doctype": "Consumer Category", "category_name": "BENCH Residential", "utility": "Electricity"}).insert()
	if not frappe.db.get_value("Tariff", {"society": SOC, "docstatus": 1}):
		approve(frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": cat, "effective_from": "2026-01-01",
		                        "pricing_method": "Progressive slabs", "slabs": [{"from_units": 0, "to_units": 100, "rate": 20}, {"from_units": 100, "to_units": 300, "rate": 35},
		                                                              {"from_units": 300, "to_units": 0, "rate": 50}],
		                        "charges": [{"charge_type": "BENCH Service", "rate": 500}]}).insert())
	have = frappe.db.count("Connection", {"society": SOC})
	consumers = [frappe.get_doc({"doctype": "Consumer", "consumer_name": f"Bench Consumer {i}"}).insert().name for i in range(max(1, n // 50))]
	for i in range(have, n):
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": f"B-{i:05d}", "sector": f"S{i // 500}"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": f"BENCH-{i:05d}", "society": SOC, "utility": "Electricity"}).insert().name
		frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumers[i % len(consumers)], "utility": "Electricity",
		                "consumer_category": cat, "status": "Active", "meters": [{"meter": meter, "installed_on": "2026-06-01", "start_reading": 1000}]}).insert()
		if i % 500 == 499:
			frappe.db.commit()
			print(f"  {i + 1} accounts", flush=True)
	frappe.db.commit()


def read_all():
	from utility_billing.utility_billing import readings

	cycle = f"{SOC}-Electricity-2026-07"
	if not frappe.db.exists("Reading Cycle", cycle):
		frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": MONTH}).insert()
	readings.create_reading_sheet(cycle)
	frappe.db.commit()
	rnd = random.Random(7)
	for k, name in enumerate(frappe.get_all("Meter Reading", filters={"reading_cycle": cycle, "status": "Pending"}, pluck="name")):
		r = frappe.get_doc("Meter Reading", name)
		r.present_reading, r.reading_date = 1000 + rnd.randint(60, 600), "2026-07-05"
		r.flags.approve_as_is = True
		r.save()
		if k % 1000 == 999:
			frappe.db.commit()
	readings.close_cycle(cycle)
	frappe.db.commit()


def drop_runs():
	"""--rerun: earlier BENCH runs and their bills go, so the accounts and readings already made are billed again."""
	runs = frappe.get_all("Bill Run", filters={"society": SOC}, pluck="name")
	bills = frappe.get_all("Utility Bill", filters={"society": SOC}, pluck="name")
	for k in range(0, len(bills), 1000):
		chunk = bills[k:k + 1000]
		for t in ("Bill Line", "Bill Tax"):
			frappe.db.sql(f"delete from `tab{t}` where parent in %s", (chunk,))
		frappe.db.sql("delete from `tabUtility Bill` where name in %s", (chunk,))
		frappe.db.commit()
	frappe.db.sql("delete from `tabCustomer Ledger Entry` where society = %s", SOC)
	for dt in ("Bill Run Summary", "Bill Run Problem", "Bill Run Exclusion"):
		frappe.db.sql(f"delete from `tab{dt}` where parent in %s", (runs or [""],))
	for dt in ("Run Snapshot", "GL Journal", "Bill Run"):
		frappe.db.sql(f"delete from `tab{dt}` where society = %s", SOC)
	frappe.db.commit()
	print(f"  dropped {len(runs)} earlier run(s) and {len(bills)} bills", flush=True)


def run_bills():
	from utility_billing.utility_billing import billing

	run = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Electricity", "billing_month": MONTH, "issue_date": ISSUE}).insert()
	run.db_set({"reading_cycle": billing.check_run_prerequisites(run), "calculation_status": "Calculating"})
	frappe.db.commit()
	_x, calc = timed("calculate", billing._calculate, run.name)
	frappe.db.commit()
	run.reload()
	print(f"  bills {run.bill_count}, problems {len(run.problems)}: {[p.problem for p in run.problems][:3]}", flush=True)
	print("  readings by status:", frappe.db.sql("select status, count(*) from `tabMeter Reading` where society=%s group by status", SOC), flush=True)
	from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER, act

	act(run, "Submit for Approval", BILLING_OFFICER)
	frappe.db.commit()
	_x, post = timed("post (approval with its hooks)", act, run, "Approve", BILLING_MANAGER)
	frappe.set_user("Administrator")
	frappe.db.commit()
	run.reload()
	return run, calc, post


def purge():
	"""Everything BENCH made, by direct deletes (the society's accounts are throwaway)."""
	conns = frappe.get_all("Connection", filters={"society": SOC}, pluck="name")
	bills = frappe.get_all("Utility Bill", filters={"society": SOC}, pluck="name")
	for table, field, values in (("Bill Line", "parent", bills), ("Bill Tax", "parent", bills), ("Customer Ledger Entry", "society", [SOC]),
	                             ("Utility Bill", "society", [SOC]), ("Connection Meter", "parent", conns), ("Meter Reading", "society", [SOC])):
		for k in range(0, len(values), 1000):
			chunk = values[k:k + 1000]
			if chunk:
				frappe.db.sql(f"delete from `tab{table}` where `{field}` in %s", (chunk,))
				frappe.db.commit()  # in parts: one delete of everything is over the transaction write limit
	for dt in ("Bill Run Summary", "Bill Run Problem", "Bill Run Exclusion"):
		frappe.db.sql(f"delete from `tab{dt}` where parent in (select name from `tabBill Run` where society = %s)", SOC)
	for dt in ("Run Snapshot", "GL Journal", "Bill Run", "Reading Cycle", "Connection", "Meter", "Property", "Tariff", "Message Log", "API Event"):
		if frappe.db.table_exists(dt):
			try:
				frappe.db.sql(f"delete from `tab{dt}` where society = %s", SOC)
			except Exception as e:  # noqa: BLE001
				print(f"  (purge {dt}: {e})")
	frappe.db.sql("delete from `tabConsumer` where consumer_name like 'Bench Consumer %%'")
	frappe.db.commit()
	print("purged BENCH", flush=True)


if __name__ == "__main__":
	site, n = sys.argv[1], int(sys.argv[2])
	if site == "ub.localhost":
		sys.exit("Never on ub.localhost: it holds the demo data.")
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	frappe.flags.post_in_foreground = True  # the posting is what is timed, so it runs here, not in a worker
	frappe.flags.post_batch = 500  # as the background posting commits (bill_run.post_in_background)
	if "--purge-only" in sys.argv:
		purge()
		frappe.destroy()
		sys.exit(0)
	try:
		if "--rerun" in sys.argv:
			drop_runs()
		else:
			timed(f"set up {n} accounts", setup, n)
			timed("read every meter", read_all)
		run, calc, post = run_bills()
		total = calc + post
		print(f"RESULT bills={run.bill_count} calculate={calc:,.0f}s post={post:,.0f}s total={total:,.0f}s "
		      f"({run.bill_count / total:,.1f} bills/s; target 20,000 in 600 s = 33.3 bills/s)", flush=True)
	finally:
		if "--keep" not in sys.argv:
			purge()
		frappe.destroy()
