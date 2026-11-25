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
from frappe.utils import cint

STALE_AFTER = 20 * 60  # seconds without news before a job is taken as stopped
KEEP = 6 * 3600  # seconds the last news is kept


def key(kind: str, name: str) -> str:
	return f"ub_progress:{kind}:{name}"


def tell(kind: str, name: str, state: str = "Running", done=0, total=0, text: str | None = None, error: str | None = None):
	frappe.cache.set_value(key(kind, name), {"state": state, "done": cint(done), "total": cint(total), "text": text, "error": error,
	                                         "at": frappe.utils.now()}, expires_in_sec=KEEP)


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
		tell(job[0], job[1], "Running", done, total, text)
