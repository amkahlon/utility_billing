# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Recovery rules without any database access (FS-REC-04, 05, 07, 15). Unit tested on their own.

A defaulter owes at least `min_amount` and has charges overdue by at least `min_days`. Recovery runs through stages
(reminder, notice, final notice, disconnection order), each reached after a number of days overdue. A stage is never
skipped, and one stage is issued per account per run. An open legal case, or a promise to pay that has not yet fallen
due, stops the automatic steps."""

from datetime import date

from frappe.utils import cint, flt, getdate

STAGES = ("Reminder", "Notice", "Final Notice", "Disconnection Order")
DISCONNECTION = STAGES[-1]
CASE_OPEN = "Open"
CASE_PROMISE = "Promise to Pay"
CASE_LEGAL = "Legal Case"
CASE_CLOSED = "Closed"


def is_defaulter(owed, oldest_days, min_amount, min_days) -> bool:
	"""True when the account owes at least min_amount and its oldest overdue charge is at least min_days old."""
	return flt(owed) > 0 and flt(owed) >= flt(min_amount) and cint(oldest_days) >= max(cint(min_days), 1)


def ordered_rules(rules):
	"""[(stage, days, channel)] in stage order, one row per stage (the first row wins)."""
	seen, out = set(), []
	for stage, days, channel in rules:
		if stage in STAGES and stage not in seen:
			seen.add(stage)
			out.append((stage, cint(days), channel))
	return sorted(out, key=lambda r: STAGES.index(r[0]))


def check_rules(rules) -> str | None:
	"""A message if the stage rules are unusable, else None. Days must rise with the stage, and a stage can't be repeated."""
	seen, last = set(), None
	for stage, days, _channel in rules:
		if stage not in STAGES:
			return f"Unknown stage {stage}."
		if stage in seen:
			return f"{stage} is listed twice."
		seen.add(stage)
		if cint(days) < 1:
			return f"{stage}: days overdue must be at least 1."
	for stage, days, _channel in sorted(rules, key=lambda r: STAGES.index(r[0])):
		if last is not None and cint(days) <= last:
			return f"{stage} must come after the stage before it (more days overdue)."
		last = cint(days)
	return None


def next_stage(rules, oldest_days, done_stages):
	"""(stage, channel) to issue now, or None. The earliest stage not yet issued, if the account is old enough for it."""
	done = set(done_stages or ())
	for stage, days, channel in ordered_rules(rules):
		if stage in done:
			continue
		return (stage, channel) if cint(oldest_days) >= days else None
	return None


def on_hold(status, promise_date, as_of) -> str | None:
	"""Why automatic recovery steps are stopped on this case (FS-REC-15), or None."""
	if status == CASE_LEGAL:
		return "Legal case open"
	if status == CASE_PROMISE and promise_date and getdate(promise_date) >= getdate(as_of):
		return "Promise to pay not yet due"
	return None


def promise_state(status, promise_date, as_of) -> str | None:
	"""Kept / broken status words for the screen: 'Due DD' while waiting, 'Broken' once the promised date has passed."""
	if status != CASE_PROMISE or not promise_date:
		return None
	return "Promised" if getdate(promise_date) >= getdate(as_of) else "Broken"


def progress(target, recovered) -> dict:
	"""Recovery against a monthly target: percent reached (not capped, so an over-achievement shows) and what is left."""
	target, recovered = flt(target), flt(recovered)
	pct = round(recovered / target * 100, 1) if target > 0 else None
	return {"target": target, "recovered": recovered, "percent": pct, "remaining": round(max(target - recovered, 0), 2),
	        "reached": bool(target > 0 and recovered >= target)}


def render_message(template: str, **values) -> str:
	"""Fill {consumer}, {owed}, {days}, {society}, {reference} in a notice text. Only plain words in braces are
	replaced (no attribute or index access); unknown words stay as typed."""
	import re

	return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template or "")


def month_first(d) -> date:
	d = getdate(d)
	return d.replace(day=1)
