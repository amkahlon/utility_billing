# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Notification rules without any database access (FS-NTF-01, 02, 04). Unit tested on their own.

A society maps each event to up to three channels in fallback order (for example SMS, then Email). A message goes by
the first channel the consumer accepts and the society has switched on; if it fails for good, the next channel is tried.
Quiet hours hold ordinary messages until they end; a critical event ignores them."""

from datetime import datetime, time, timedelta

from frappe.utils import cint

SMS, WHATSAPP, EMAIL, PUSH = "SMS", "WhatsApp", "Email", "App"
CHANNELS = (SMS, WHATSAPP, EMAIL, PUSH)

# event: (label, critical by default)
EVENTS = {
	"bill.issued": ("Bill issued", False),
	"bill.revised": ("Bill revised", False),
	"payment.received": ("Payment received", False),
	"payment.reversed": ("Payment reversed", True),
	"reminder.due": ("Reminder: bill due soon", False),
	"bill.overdue": ("Bill overdue", False),
	"disconnection.notice": ("Disconnection notice", True),
	"connection.disconnected": ("Connection disconnected", True),
	"connection.reconnected": ("Connection reconnected", False),
	"reading.taken": ("Reading taken", False),
	"request.updated": ("Request updated", False),
	"outage.notice": ("Outage or maintenance notice", True),
	"recovery.notice": ("Recovery notice", True),
}

# status words of a Message Log
QUEUED, SENT, DELIVERED, READ, FAILED, SKIPPED, CANCELLED = "Queued", "Sent", "Delivered", "Read", "Failed", "Skipped", "Cancelled"
FINAL = (SENT, DELIVERED, READ, FAILED, SKIPPED, CANCELLED)
DEFAULT_RULES = {
	"bill.issued": (SMS, EMAIL, None), "bill.revised": (SMS, EMAIL, None), "payment.received": (SMS, EMAIL, None),
	"payment.reversed": (SMS, EMAIL, None), "reminder.due": (SMS, EMAIL, None), "bill.overdue": (SMS, EMAIL, None),
	"disconnection.notice": (SMS, EMAIL, None), "connection.disconnected": (SMS, EMAIL, None),
	"connection.reconnected": (SMS, EMAIL, None), "reading.taken": (SMS, None, None), "request.updated": (SMS, EMAIL, None),
	"outage.notice": (SMS, EMAIL, None), "recovery.notice": (SMS, EMAIL, None),
}
RETRY_MINUTES = (5, 15, 60)  # wait before the 2nd, 3rd and later attempts
MAX_ATTEMPTS = 3


def accepts(channel, consumer: dict) -> bool:
	"""Whether the consumer has opted in to a channel and has somewhere to receive it (FS-NTF-04). `consumer` carries
	mobile_no, email, notify_sms, notify_whatsapp, notify_email, notify_app (the customer app is not built yet)."""
	if channel == SMS:
		return bool(cint(consumer.get("notify_sms")) and (consumer.get("mobile_no") or "").strip())
	if channel == WHATSAPP:
		return bool(cint(consumer.get("notify_whatsapp")) and (consumer.get("mobile_no") or "").strip())
	if channel == EMAIL:
		return bool(cint(consumer.get("notify_email")) and (consumer.get("email") or "").strip())
	if channel == PUSH:
		return bool(cint(consumer.get("notify_app")) and cint(consumer.get("app_installed")))
	return False


def usable_channels(order, consumer: dict, whatsapp_enabled=False, switched_off=()) -> list[str]:
	"""The rule's channels, in order, that the society allows and the consumer accepts. The consumer's preferred channel
	moves to the front when it is among them. WhatsApp can be switched off for the whole society (FS-NTF-06)."""
	out = []
	for ch in order:
		if not ch or ch in out or ch in switched_off:
			continue
		if ch == WHATSAPP and not whatsapp_enabled:
			continue
		if accepts(ch, consumer):
			out.append(ch)
	pref = consumer.get("preferred_channel")
	if pref in out:
		out.remove(pref)
		out.insert(0, pref)
	return out


def next_channel(remaining, failed):
	"""The channel to try after `failed`, from the usable channels that have not been tried."""
	rest = [c for c in remaining if c != failed]
	return rest[0] if rest else None


def in_quiet_hours(now: datetime, start: time | None, end: time | None) -> bool:
	"""True during the society's quiet hours; the window may pass midnight (21:00-08:00). Equal times mean no quiet hours."""
	if not start or not end or start == end:
		return False
	t = now.time()
	return (start <= t < end) if start < end else (t >= start or t < end)


def next_allowed(now: datetime, start: time | None, end: time | None) -> datetime:
	"""When a message held by quiet hours may go out: the end of the window."""
	if not in_quiet_hours(now, start, end):
		return now
	today_end = datetime.combine(now.date(), end)
	return today_end if now < today_end else today_end + timedelta(days=1)


def retry_at(now: datetime, attempts: int) -> datetime | None:
	"""When to try again after `attempts` failed attempts, or None once the attempts are used up."""
	if attempts >= MAX_ATTEMPTS:
		return None
	return now + timedelta(minutes=RETRY_MINUTES[min(attempts - 1, len(RETRY_MINUTES) - 1)] if attempts >= 1 else 0)


def sms_parts(text: str) -> int:
	"""Number of SMS parts a text takes (160 characters, 70 for Urdu or Arabic, then 153 and 67 per part)."""
	n = len(text or "")
	if n == 0:
		return 0
	unicode_ = any(ord(c) > 127 for c in text)
	single, multi = (70, 67) if unicode_ else (160, 153)
	return 1 if n <= single else -(-n // multi)
