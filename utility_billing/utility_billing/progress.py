# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""How far work running in the background has got, for the progress bar on its screen (increment 54).

User, 2026-11-25: "since we have been adding progress bar again n again for different processes on various screens ... progress bar
should be displayed everywhere for all such cases". Every background job of the app reports here: a bill run's calculation and
posting, and a migration batch's load and undo (opening balances keep their own store, built the same way in increment 48).

The news is kept in Redis, not the database, so the screen sees it while the job's transaction is still open:
{state: Queued | Running | Done | Failed, done, total, text, error, at}. A job silent for longer than its `stale_after` is reported
as stopped (Failed), so a screen never waits for a worker that died.
"""

import contextlib

import frappe
from frappe import _
from frappe.utils import cint, cstr

STALE_AFTER = 20 * 60  # seconds without news before a job is taken as stopped
KEEP = 6 * 3600  # seconds the last news is kept


def key(kind: str, name: str) -> str:
	return f"ub_progress:{kind}:{name}"


def tell(kind: str, name: str, state: str = "Running", done=0, total=0, text: str | None = None, error: str | None = None, **extra):
	frappe.cache.set_value(key(kind, name), {"state": state, "done": cint(done), "total": cint(total), "text": text, "error": error,
	                                         "at": frappe.utils.now(), **extra}, expires_in_sec=KEEP)


def get(kind: str, name: str, stale_after: int = STALE_AFTER, stopped: str | None = None):
	"""The job's last news, or None. A Queued or Running job silent for `stale_after` seconds comes back Failed, with `stopped` (or a
	general message) as its error."""
	p = frappe.cache.get_value(key(kind, name), expires=True, use_local_cache=False)
	if p and p.get("state") in ("Queued", "Running") and frappe.utils.time_diff_in_seconds(frappe.utils.now(), p.get("at")) > stale_after:
		p = {**p, "state": "Failed", "error": stopped or _("It stopped: nothing was heard from it for {0} minutes.").format(stale_after // 60)}
	return p


def clear(kind: str, name: str):
	frappe.cache.delete_value(key(kind, name))


def running(p) -> bool:
	return bool(p and p.get("state") in ("Queued", "Running"))


@contextlib.contextmanager
def reporting(kind: str, name: str):
	"""Inside, step() reports for this job: code deep in a long task says where it is without being handed the job."""
	before = frappe.flags.get("ub_progress")
	frappe.flags.ub_progress = (kind, name)
	try:
		yield
	finally:
		frappe.flags.ub_progress = before


def step(text: str, done=0, total=0):
	job = frappe.flags.get("ub_progress")
	if job:
		now = frappe.cache.get_value(key(*job), expires=True, use_local_cache=False) or {}
		tell(job[0], job[1], "Running", done, total, text, by=now.get("by"), title=now.get("title"))


# ---------------------------------------------------------------- buttons whose work runs in the background (increment 55)
# User, 2026-11-25: the society-wide buttons that greyed the screen run here instead, with a progress bar that doesn't block the
# screen. Each is the same whitelisted function as before, run in a worker as the user who pressed the button, so its own permission
# checks and refusals are unchanged. Only these functions can be started: {job: (function, the argument naming the record)}.
_U = "utility_billing.utility_billing."
JOBS = {
	"reading_sheet": (_U + "readings.create_reading_sheet", "cycle"),
	"estimate_unread": (_U + "readings.estimate_unread", "cycle"),
	"upload_readings": (_U + "readings.upload_readings", "cycle"),
	"migration_check": (_U + "doctype.migration_batch.migration_batch.check", "name"),
	"opening_load": (_U + "opening.load_file", "name"),
	"parallel_payments": (_U + "doctype.parallel_run_month.parallel_run_month.load_payments", "name"),
	"parallel_compare": (_U + "doctype.parallel_run_month.parallel_run_month.compare", "name"),
	"ra_checks": (_U + "revenue_assurance.run_now", "society"),
	"gl_journals": (_U + "gl.make_journals", "society"),
	"gl_reconcile": (_U + "gl.reconcile_now", "society"),
	"period_checklist": (_U + "periods.preview", "period"),
	"period_close": (_U + "periods.close", "period"),
	"period_reopen": (_U + "periods.reopen", "period"),
	"year_prepare": (_U + "yearend.prepare", "name"),
	"year_close": (_U + "yearend.close", "name"),
	"delivery": (_U + "delivery.mark", "run"),
	"solar_figures": (_U + "doctype.solar_rebate_pool.solar_rebate_pool.get_figures", "name"),
	"snapshot_restore": (_U + "snapshots.restore", "snapshot"),
	"run_cancel": (_U + "doctype.bill_run.bill_run.cancel_in_background", "run"),
	"print_bills": (_U + "bill_print.print_run_bills_to_file", "run"),
	"walk_build": (_U + "walk.build_from_references", "society"),
	"walk_import": (_U + "walk.import_walk_order", "society"),
}
JOB = "job"
JOB_STALE_AFTER = 30 * 60  # some steps report no count for minutes at a time


def job_key(job: str, record: str) -> str:
	return f"{job}:{record}"


@frappe.whitelist(methods=["POST"])
def start(job: str, args: str | dict | None = None, title: str | None = None) -> dict:
	"""Start one of JOBS for a record. A second start of the same job on the same record is refused while the first runs."""
	if job not in JOBS:
		frappe.throw(_("{0} can't run in the background.").format(job))
	args = frappe.parse_json(args or {}) or {}
	if not isinstance(args, dict):
		frappe.throw(_("Send the job's arguments as an object."))
	record = cstr(args.get(JOBS[job][1]))
	if not record:
		frappe.throw(_("Say which record the job is for ({0}).").format(JOBS[job][1]))
	k = job_key(job, record)
	now = get(JOB, k, JOB_STALE_AFTER)
	if running(now):
		frappe.throw(_("This is already running: {0}. Wait for it to finish.").format(now.get("text") or now.get("title") or job), title=_("Already running"))
	user = frappe.session.user
	tell(JOB, k, "Queued", text=_("Waiting to start"), by=user, title=title)
	at_once = bool(frappe.flags.in_test)
	frappe.enqueue(_U + "progress.run", queue="long", timeout=3 * 3600, now=at_once, enqueue_after_commit=not at_once,
	               job=job, record=record, args=args, user=user, title=title, at_once=at_once)
	return {"job": job, "record": record}


def run(job, record, args, user, title=None, at_once=False):
	"""The worker: the job's function as the user who started it, all of it or none of it."""
	k = job_key(job, record)
	frappe.set_user(user)
	if at_once:  # a test: inside the caller's transaction, undoing only its own work and committing nothing
		frappe.db.savepoint("ub_job")
	try:
		with reporting(JOB, k):
			result = frappe.get_attr(JOBS[job][0])(**args)
		result = frappe.parse_json(frappe.as_json(result)) if result is not None else None  # as the web reply would carry it
		if not at_once:
			frappe.db.commit()
		tell(JOB, k, "Done", by=user, title=title, result=result)
	except Exception as e:
		frappe.db.rollback(save_point="ub_job") if at_once else frappe.db.rollback()
		said = (frappe.utils.strip_html(cstr(e)) if isinstance(e, frappe.ValidationError | frappe.PermissionError) else "") or _(
			"It stopped with an error ({0}). Nothing was changed; the error is logged for support.").format(e.__class__.__name__)
		tell(JOB, k, "Failed", error=said, by=user, title=title)
		if at_once:
			raise
		frappe.log_error(title=f"Background job {job} on {record} failed")
		frappe.db.commit()


@frappe.whitelist()
def job_progress(job: str, record: str):
	"""How far the job is, for its progress bar. Its result goes only to the user who started it."""
	p = get(JOB, job_key(job, record), JOB_STALE_AFTER)
	if p and p.get("by") != frappe.session.user:
		p = {k: v for k, v in p.items() if k != "result"}
	return p
