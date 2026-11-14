# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Part-month billing (FS-BIL-07), without any database access. Unit tested on their own.

A connection that starts or is disconnected during a billing month pays its fixed and per-area charges for the days it was
connected, both days counted: connected on the 11th of a 30-day month is 20 of 30 days. Metered units are what the meter
recorded and are never pro-rated; one-off charges are not pro-rated either."""

import calendar
from datetime import date
from decimal import Decimal

from frappe.utils import getdate


class NotConnected(Exception):
	"""The connection was not connected at all in the billing month."""


def month_bounds(month) -> tuple[date, date]:
	m = getdate(month)
	return m.replace(day=1), m.replace(day=calendar.monthrange(m.year, m.month)[1])


def active_days(month, connected_on=None, disconnected_on=None) -> tuple[int, int]:
	"""(days connected in the month, days in the month). Raises NotConnected when none."""
	start, end = month_bounds(month)
	total = (end - start).days + 1
	first = max(start, getdate(connected_on)) if connected_on else start
	last = min(end, getdate(disconnected_on)) if disconnected_on else end
	if first > end:
		raise NotConnected("The connection started after this billing month.")
	if last < start:
		raise NotConnected("The connection was disconnected before this billing month.")
	if last < first:
		raise NotConnected("The disconnection date is before the connection date.")
	return (last - first).days + 1, total


def fraction(month, connected_on=None, disconnected_on=None):
	"""(fraction to charge, note) for a part month, or (None, "") for a whole month."""
	days, total = active_days(month, connected_on, disconnected_on)
	if days >= total:
		return None, ""
	return Decimal(days) / Decimal(total), f"Pro-rata: {days} of {total} days"
