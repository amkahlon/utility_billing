"""Benchmark of the bulk paths a society meets in its first month (the performance step, part 2): the migration load of N accounts
from a workbook, the month's reading sheet, and a readings file upload. Developer tool; test site only, never ub.localhost.

	cd ~/frappe-bench/sites && ../env/bin/python ../apps/utility_billing/dev/bench_load.py test.localhost 5000

Uses the BENCH society of bench_billing.py (purged first and again at the end; pass --keep to look at it afterwards).
--profile saves each step's profile to /tmp/ub-<step>.prof and prints its hottest functions; --queries lists its statements."""

import io
import os
import random
import sys
import time

import frappe

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_billing as bb  # noqa: E402

SOC, MONTH = bb.SOC, bb.MONTH


def step(label, fn, *a, **kw):
	t = time.time()
	if "--queries" in sys.argv:
		out = bb.counted(label, fn, *a, **kw)
	elif "--profile" in sys.argv:
		import cProfile
		import pstats

		prof = cProfile.Profile()
		out = prof.runcall(fn, *a, **kw)
		prof.dump_stats(f"/tmp/ub-{label.split()[0]}.prof")
		print(f"---- profile of {label}", flush=True)
		pstats.Stats(prof).sort_stats("tottime").print_stats(18)
	else:
		out = fn(*a, **kw)
	secs = time.time() - t
	print(f"{label}: {secs:,.1f} s", flush=True)
	return out, secs


def workbook(n) -> str:
	from openpyxl import Workbook

	wb = Workbook(write_only=True)
	ws = wb.create_sheet("Connections")
	ws.append(["old_account_no", "old_customer_id", "consumer_name", "cnic", "mobile", "house", "block", "plot_area", "plot_unit", "utility",
	           "category", "meter_no", "meter_type", "feeder", "transformer"])
	for i in range(n):
		ws.append([f"BL{i:06d}", f"C{i:06d}", f"Bench Consumer {i}", f"35202{i:07d}1", f"0300{i:07d}", str(i + 1), f"B{i // 500}", 5, "Marla",
		           "Electricity", "BENCH Residential", f"BLM{i:06d}", "Single Phase", "BF1", f"BT{i // 200}"])
	ws = wb.create_sheet("Readings")
	ws.append(["old_account_no", "meter_no", "last_reading", "reading_date"])
	for i in range(n):
		ws.append([f"BL{i:06d}", f"BLM{i:06d}", 1000 + i % 500, "2026-05-25"])
	ws = wb.create_sheet("Balances")
	ws.append(["old_account_no", "current_bill", "arrears"])
	for i in range(n):
		ws.append([f"BL{i:06d}", 3000 + i % 900, 0 if i % 4 else 1200])
	ws = wb.create_sheet("History")
	ws.append(["old_account_no", "month", "units", "current_amount", "bill_amount", "paid_on", "paid_amount", "balance"])
	for i in range(n):
		for m in (3, 4, 5):  # three months each keeps the file a reasonable size; a real load has twelve
			ws.append([f"BL{i:06d}", f"2026-{m:02d}-01", 200 + i % 90, 9000, 9000, f"2026-{m + 1:02d}-10", 9000, 0])
	ws = wb.create_sheet("Network")
	ws.append(["kind", "code", "name", "feeder", "kva"])
	ws.append(["Feeder", "BF1", "Bench feeder", "", ""])
	for t in range(n // 200 + 1):
		ws.append(["Transformer", f"BT{t}", "", "BF1", 200])
	out = io.BytesIO()
	wb.save(out)
	f = frappe.get_doc({"doctype": "File", "file_name": f"bench-load-{n}.xlsx", "content": out.getvalue(), "is_private": 1}).insert()
	return f.file_url


def migrate(n):
	from utility_billing.utility_billing import migration

	if not frappe.db.exists("Migration Profile", {"society": SOC}):
		frappe.get_doc({"doctype": "Migration Profile", "profile_name": "BENCH source", "society": SOC, "default_utility": "Electricity"}).insert()
	profile = frappe.db.get_value("Migration Profile", {"society": SOC})
	url, _s = step(f"build the workbook ({n} accounts)", workbook, n)
	batch = frappe.get_doc({"doctype": "Migration Batch", "society": SOC, "profile": profile, "as_of_date": "2026-05-31", "workbook": url}).insert()
	frappe.db.commit()
	errors, check = step("check the workbook", migration.check, batch)
	print(f"  errors {errors}, warnings {batch.warning_count}, accounts {batch.account_count}: {[p.problem for p in batch.problems if p.severity == 'Error'][:3]}", flush=True)
	ok, load = step("load the accounts", migration.load, batch, True)  # as the background job does: a commit per group
	batch.save()
	frappe.db.commit()
	print(f"  loaded {ok}: {[(r.record_type, r.record_count) for r in batch.records]}", flush=True)
	return check, load


def readings_month(n):
	from utility_billing.utility_billing import readings

	cycle = f"{SOC}-Electricity-2026-07"
	if not frappe.db.exists("Reading Cycle", cycle):
		frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": MONTH}).insert()
	_x, sheet = step("create the reading sheet", readings.create_reading_sheet, cycle)
	frappe.db.commit()
	rnd = random.Random(7)
	lines = ["Reference No,Present Reading,Reading Date"]
	for conn in frappe.get_all("Connection", filters={"society": SOC}, pluck="name", order_by="name"):
		lines.append(f"{conn},{1600 + rnd.randint(60, 600)},2026-07-05")
	f = frappe.get_doc({"doctype": "File", "file_name": f"bench-readings-{n}.csv", "content": "\n".join(lines), "is_private": 1}).insert()
	frappe.db.commit()
	result, upload = step("upload the readings file", readings.upload_readings, cycle, f.file_url)
	frappe.db.commit()
	print(f"  updated {result['updated']}, errors {len(result['errors'])}: {result['errors'][:2]}", flush=True)
	print("  readings by status:", frappe.db.sql("select status, count(*) from `tabMeter Reading` where society=%s group by status", SOC), flush=True)
	_x, close = step("close the cycle", readings.close_cycle, cycle)
	frappe.db.commit()
	return sheet, upload


def purge():
	for dt in ("Legacy Bill", "Opening Balance Row"):
		if frappe.db.table_exists(dt):
			frappe.db.sql(f"delete from `tab{dt}` where {'connection' if dt == 'Legacy Bill' else 'parent'} in "
			              f"(select name from `tab{'Connection' if dt == 'Legacy Bill' else 'Opening Balance Import'}` where society = %s)", SOC)
	batches = frappe.get_all("Migration Batch", filters={"society": SOC}, pluck="name")
	for dt in ("Migration Record", "Migration Problem"):
		frappe.db.sql(f"delete from `tab{dt}` where parent in %s", (batches or [""],))
	for dt in ("Migration Batch", "Opening Balance Import", "Migration Column", "Migration Value Map"):
		try:
			frappe.db.sql(f"delete from `tab{dt}` where " + ("society = %s" if dt in ("Migration Batch", "Opening Balance Import") else
			              "parent in (select name from `tabMigration Profile` where society = %s)"), SOC)
		except Exception as e:  # noqa: BLE001
			print(f"  (purge {dt}: {e})")
	frappe.db.sql("delete from `tabMigration Profile` where society = %s", SOC)
	for dt in ("Transformer", "Feeder"):
		frappe.db.sql(f"delete from `tab{dt}` where society = %s", SOC)
	frappe.db.sql("delete from `tabFile` where file_name like 'bench-load-%%' or file_name like 'bench-readings-%%'")
	frappe.db.commit()
	bb.purge()


if __name__ == "__main__":
	site, n = sys.argv[1], int(sys.argv[2])
	if site == "ub.localhost":
		sys.exit("Never on ub.localhost: it holds the demo data.")
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	try:
		purge()
		bb.setup(0)
		check, load = migrate(n)
		sheet, upload = readings_month(n)
		print(f"RESULT accounts={n} check={check:,.0f}s load={load:,.0f}s ({n / max(load, 0.01):,.0f} accounts/s) "
		      f"sheet={sheet:,.0f}s upload={upload:,.0f}s ({n / max(upload, 0.01):,.0f} readings/s)", flush=True)
	finally:
		if "--keep" not in sys.argv:
			purge()
		frappe.destroy()
