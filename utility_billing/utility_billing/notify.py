# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Consumer notifications (increment 12; FS-NTF-01 to 09, FS-PAY-09, and the sending side of FS-REC-06).

How it works
- `notify(event, connection, context)` decides, from the society's **Event Rules**, the consumer's opt-ins and preferred
  channel, which channel to use, fills the **Notification Template** (society's own, else the default; consumer's language,
  else English; built-in text if none) and writes a **Message Log** row, Queued. Quiet hours hold ordinary messages until
  they end; critical events go out at once.
- `process_queue()` (the scheduler runs it every minute) hands each due message to the channel's **Messaging Provider**:
  Dry run (records only, for testing), Frappe Email (the site's mail queue) or an HTTP gateway (JSON over HTTPS). A failure is
  retried after 5, 15 and 60 minutes; when the attempts are used up the message is Failed and the next channel of the rule
  is tried (a new log row linked to the failed one). Nothing is sent without a provider: SMS and WhatsApp wait, Queued.
- Every attempt, status, provider reference and cost is in the Message Log, which also feeds the cost report (FS-NTF-05, 06).
- Events with a source in the system today: payment received and reversed, bill issued, reminder before the due date, bill
  overdue, recovery notices (SMS and WhatsApp), outage broadcasts. The others (connection disconnected and reconnected,
  reading taken, request updated, bill revised) have rules and templates ready and fire when their features are built.
- WhatsApp is off unless the society turns it on, and sends only from approved templates (FS-NTF-03, 06). Push through the
  customer app waits for that app; it is accepted in a rule and skipped until a consumer has it installed."""

import functools
import hashlib
import hmac
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, formatdate, get_datetime, get_url, getdate, now_datetime, nowdate

from utility_billing.utility_billing import notify_engine as engine
from utility_billing.utility_billing.recovery_engine import render_message

SENDERS = ["Billing Manager", "General Manager", "Society Admin", "Recovery Officer"]

DEFAULT_TEXT = {
	"bill.issued": "Dear {consumer}, your bill for {bill_month} is Rs {amount} (Rs {amount_after_due} after the due date), due {due_date}. Account {reference}. Bill: {link}",
	"bill.revised": "Dear {consumer}, your bill for {bill_month} was revised to Rs {amount}, due {due_date}. Account {reference}. Bill: {link}",
	"payment.received": "Dear {consumer}, we received Rs {amount} on {date} for account {reference}. Receipt {receipt}. Balance Rs {balance}. Thank you.",
	"payment.reversed": "Dear {consumer}, your payment of Rs {amount} (receipt {receipt}) was reversed. Balance Rs {balance}. Please contact the {society} office.",
	"reminder.due": "Dear {consumer}, your bill of Rs {amount} for account {reference} is due on {due_date}. Please pay by then to avoid the late payment surcharge.",
	"bill.overdue": "Dear {consumer}, your bill for account {reference} was due on {due_date}. Rs {balance} is now overdue and a late payment surcharge applies.",
	"disconnection.notice": "{message}", "connection.disconnected": "Dear {consumer}, supply to account {reference} has been disconnected. Contact the {society} office to restore it.",
	"connection.reconnected": "Dear {consumer}, supply to account {reference} has been restored.",
	"reading.taken": "Dear {consumer}, the meter reading for account {reference} was taken on {date}.",
	"request.updated": "Dear {consumer}, your request for account {reference} was updated: {message}",
	"outage.notice": "{society}: {message}", "recovery.notice": "{message}",
}


# ---------------------------------------------------------------- rules, consumers, templates, providers
def rules_of(society) -> dict:
	"""{event: (channels, critical, enabled)} for a society; events without a row use the defaults."""
	s = frappe.get_cached_doc("Society", society)
	out = {ev: (tuple(c for c in chans if c), engine.EVENTS[ev][1], True) for ev, chans in engine.DEFAULT_RULES.items()}
	for r in s.notification_rules:
		out[r.event] = (tuple(c for c in (r.channel_1, r.channel_2, r.channel_3) if c), bool(cint(r.critical)), bool(cint(r.enabled)))
	return out


def consumer_of(connection) -> frappe._dict:
	c = frappe.db.get_value("Connection", connection, ["consumer", "consumer_name", "society", "reference_no"], as_dict=True)
	info = frappe.db.get_value("Consumer", c.consumer, ["mobile_no", "email", "notify_sms", "notify_whatsapp", "notify_email", "notify_app",
	                                                    "preferred_language", "preferred_channel"], as_dict=True) or {}
	return frappe._dict(**info, consumer=c.consumer, consumer_name=c.consumer_name, society=c.society, reference=c.reference_no or connection)


def template_for(society, event, channel, language):
	"""(subject, body) for a message: the society's template, else the default one, in the consumer's language else English,
	else the built-in text. WhatsApp uses approved templates only; None means there is none to send."""
	langs = [language] if language in (None, "", "English") else [language, "English"]
	for lang in langs or ["English"]:
		for soc in (society, None):
			t = frappe.db.get_value("Notification Template", {"society": soc or ["is", "not set"], "event": event, "channel": channel,
			                                                   "language": lang or "English", "enabled": 1},
			                        ["subject", "body", "whatsapp_status"], as_dict=True)
			if t:
				if channel == "WhatsApp" and t.whatsapp_status != "Approved":
					continue
				return t.subject, t.body
	if channel == "WhatsApp":
		return None
	return None if event not in DEFAULT_TEXT else (engine.EVENTS[event][0], DEFAULT_TEXT[event])


def provider_for(society, channel):
	rows = frappe.get_all("Messaging Provider", filters={"channel": channel, "enabled": 1, "society": ["in", [society, ""]]},
	                      fields=["name", "society", "priority", "provider_type"], order_by="priority asc")
	rows.sort(key=lambda r: (0 if r.society else 1, cint(r.priority)))  # a society's own provider before a shared one
	if rows:
		return frappe.get_doc("Messaging Provider", rows[0].name)
	if channel == "Email":
		return frappe._dict(name=None, provider_type="Frappe Email", cost_per_message=0)
	return None


def recipient_for(channel, cons) -> str:
	return (cons.get("email") if channel == "Email" else cons.get("mobile_no")) or ""


# ---------------------------------------------------------------- writing messages
def _new_log(society, event, channel, cons, connection, subject, body, critical, reference=None, fallback_of=None, broadcast=None,
             bill_link=None, status="Queued", error=None):
	now = now_datetime()
	soc = frappe.get_cached_doc("Society", society)
	when = now
	if status == "Queued" and not critical:
		when = engine.next_allowed(now, _time(soc.quiet_hours_start), _time(soc.quiet_hours_end))
	log = frappe.get_doc({
		"doctype": "Message Log", "event": event, "channel": channel, "status": status, "society": society, "connection": connection,
		"consumer_name": cons.get("consumer_name"), "recipient": recipient_for(channel, cons), "language": cons.get("preferred_language") or "English",
		"critical": 1 if critical else 0, "subject": subject, "body": body, "bill_link": bill_link, "scheduled_for": when, "error": error,
		"reference_doctype": reference[0] if reference else None, "reference_name": reference[1] if reference else None,
		"fallback_of": fallback_of, "broadcast": broadcast, "attempts": 0})
	log.insert(ignore_permissions=True)
	return log


def _time(value):
	"""A Time field comes back as a timedelta or a string."""
	if value in (None, ""):
		return None
	if hasattr(value, "total_seconds"):
		secs = int(value.total_seconds())
		return datetime.min.replace(hour=secs // 3600 % 24, minute=secs // 60 % 60, second=secs % 60).time()
	return get_datetime(f"2000-01-01 {value}").time()


def notify(event, connection, context=None, reference=None, text=None, channels=None, broadcast=None, critical=None):
	"""Queue the message for an event. `text` replaces the template (recovery notices, broadcasts); `channels` replaces the
	rule's channels. Returns the new Message Log names (one, or a Skipped row saying why nothing was sent)."""
	if event not in engine.EVENTS:
		frappe.throw(_("Unknown notification event {0}.").format(event))
	cons = consumer_of(connection)
	rule_channels, rule_critical, enabled = rules_of(cons.society).get(event, ((), False, True))
	if not enabled:
		return []
	soc = frappe.get_cached_doc("Society", cons.society)
	order = channels or rule_channels
	critical = rule_critical if critical is None else critical
	usable = engine.usable_channels(order, cons, bool(cint(soc.whatsapp_enabled)))
	ctx = {"consumer": cons.consumer_name, "reference": cons.reference, "society": soc.society_name, "date": formatdate(nowdate()),
	       **{k: v for k, v in (context or {}).items()}}
	bill_link = ctx.get("link")
	for ch in usable:
		if text and ch == "WhatsApp" and not template_for(cons.society, event, ch, cons.get("preferred_language")):
			continue  # free text never goes by WhatsApp: only from an approved template
		subject, body = (None, text) if text else (template_for(cons.society, event, ch, cons.get("preferred_language")) or (None, None))
		if text and ch == "Email":
			subject = engine.EVENTS[event][0]
		if not body:
			continue  # no usable template on this channel (for example WhatsApp not approved yet): try the next
		return [_new_log(cons.society, event, ch, cons, connection, render_message(subject, **ctx) if subject else None,
		                 render_message(body, **ctx), critical, reference, broadcast=broadcast, bill_link=bill_link).name]
	reason = _("No channel the consumer accepts") if not usable else _("No message template for this event and channel")
	first = (order or ("SMS",))[0]
	return [_new_log(cons.society, event, first, cons, connection, None, None, critical, reference, broadcast=broadcast, bill_link=bill_link,
	                 status="Skipped", error=reason).name]


# ---------------------------------------------------------------- sending
def send_log(name):
	"""Send one due message. Returns the status it ends with."""
	log = frappe.get_doc("Message Log", name)
	if log.status != "Queued" or (log.scheduled_for and get_datetime(log.scheduled_for) > now_datetime()):
		return log.status
	provider = provider_for(log.society, log.channel)
	if not provider:
		log.db_set("error", _("No {0} provider is set up yet; the message waits.").format(log.channel), update_modified=False)
		return log.status
	log.attempts = cint(log.attempts) + 1
	try:
		ref = PROVIDERS[provider.provider_type](provider, log)
	except Exception as e:  # a provider fails in many ways; every one is retried and logged
		return _failed(log, provider, str(e)[:300])
	parts = engine.sms_parts(log.body) if log.channel == "SMS" else 1
	log.db_set({"status": "Sent", "sent_at": now_datetime(), "provider": provider.get("name"), "provider_ref": ref, "attempts": log.attempts,
	            "cost": flt(provider.get("cost_per_message")) * parts, "parts": parts if log.channel == "SMS" else 0, "error": None},
	           update_modified=False)
	_sync_reference(log.name)
	return "Sent"


def _failed(log, provider, error):
	again = engine.retry_at(now_datetime(), log.attempts)
	values = {"attempts": log.attempts, "error": error, "provider": provider.get("name")}
	if again:
		log.db_set({**values, "scheduled_for": again}, update_modified=False)
		return "Queued"
	log.db_set({**values, "status": "Failed"}, update_modified=False)
	_fallback(log)
	_sync_reference(log.name)
	return "Failed"


def _chain(log):
	"""Channels already tried for this message, following the fallback links back."""
	tried, cur = [], log
	while cur:
		tried.append(cur.channel)
		cur = frappe.get_doc("Message Log", cur.fallback_of) if cur.fallback_of else None
	return tried


def _fallback(failed):
	"""Try the rule's next channel after a message has failed for good (FS-NTF-02)."""
	if not failed.connection:
		return None
	cons = consumer_of(failed.connection)
	soc = frappe.get_cached_doc("Society", failed.society)
	order, rule_critical, _enabled = rules_of(failed.society).get(failed.event, ((), False, True))
	tried = _chain(failed)
	for ch in engine.usable_channels(order, cons, bool(cint(soc.whatsapp_enabled)), switched_off=tried):
		if ch == "WhatsApp" and not template_for(failed.society, failed.event, ch, cons.get("preferred_language")):
			continue  # WhatsApp sends only from an approved template
		# the message that failed is already filled in, so the next channel carries the same text
		subject = failed.subject or (engine.EVENTS[failed.event][0] if ch == "Email" else None)
		ref = (failed.reference_doctype, failed.reference_name) if failed.reference_doctype else None
		return _new_log(failed.society, failed.event, ch, cons, failed.connection, subject, failed.body, bool(cint(failed.critical)), ref,
		                fallback_of=failed.name, broadcast=failed.broadcast, bill_link=failed.bill_link).name
	return None


def _sync_reference(log_name):
	"""A recovery notice sent by SMS or WhatsApp follows its message: Sent when it goes, Failed when every channel failed."""
	log = frappe.get_doc("Message Log", log_name)
	if log.reference_doctype == "Utility Bill" and log.event == "bill.issued" and log.status in ("Sent", "Delivered", "Read") and log.reference_name:
		from utility_billing.utility_billing import delivery  # noqa: PLC0415

		delivery.record([log.reference_name], delivery.MESSAGE, remarks=log.channel, user=frappe.session.user, keep_stronger=True)
		return
	if log.reference_doctype != "Recovery Notice" or not log.reference_name:
		return
	status = {"Sent": "Sent", "Delivered": "Sent", "Read": "Sent", "Failed": "Failed"}.get(log.status)
	if not status:
		return
	if status == "Failed" and frappe.db.exists("Message Log", {"fallback_of": log.name}):
		return  # the next channel is still trying
	frappe.db.set_value("Recovery Notice", log.reference_name, "status", status)


def process_queue(limit=300):
	"""Scheduler, every minute: send what is due. Returns how many messages it handled."""
	names = frappe.get_all("Message Log", filters={"status": "Queued", "scheduled_for": ["<=", now_datetime()]}, pluck="name",
	                       order_by="critical desc, scheduled_for asc", limit=limit)
	for n in names:
		try:
			send_log(n)
		except Exception:
			frappe.log_error(title=f"Message {n} could not be processed")
	return len(names)


@frappe.whitelist(methods=["POST"])
def send_now():
	"""Staff: send the queued messages that are due now, without waiting for the scheduler's next tick."""
	if not set(frappe.get_roles()) & set(SENDERS + ["Accounts Officer"]) and frappe.session.user != "Administrator":
		frappe.throw(_("You can't send messages."), frappe.PermissionError)
	return process_queue()


def queue_send(names):
	"""After the transaction commits, send these messages soon without waiting for the next scheduler tick."""
	if frappe.flags.in_test or not names:
		return
	frappe.enqueue("utility_billing.utility_billing.notify.process_queue", queue="short", enqueue_after_commit=True)


# ---------------------------------------------------------------- providers (FS-NTF-07: interchangeable)
def _dry_run(provider, log):
	return f"DRY-{log.name}"


def _frappe_email(provider, log):
	if not log.recipient:
		raise ValueError("The consumer has no email address.")
	paragraphs = "".join(f"<p>{frappe.utils.escape_html(p)}</p>" for p in (log.body or "").split("\n") if p.strip())
	frappe.sendmail(recipients=[log.recipient], subject=log.subject or engine.EVENTS.get(log.event, (log.event,))[0], message=paragraphs,
	                reference_doctype=log.doctype, reference_name=log.name, now=False)
	return f"MAIL-{log.name}"


def _http_gateway(provider, log):
	import requests

	if not provider.endpoint_url or not provider.endpoint_url.startswith("https://"):
		raise ValueError("The gateway URL must start with https://")
	key = provider.get_password("api_key", raise_exception=False)
	r = requests.post(provider.endpoint_url, json={"to": log.recipient, "text": log.body, "subject": log.subject, "channel": log.channel,
	                                               "sender": provider.sender_id, "reference": log.name},
	                  headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=15)
	if not 200 <= r.status_code < 300:
		raise ValueError(f"Gateway answered {r.status_code}: {r.text[:120]}")
	try:
		data = r.json()
	except ValueError:
		data = {}
	return str(data.get("id") or data.get("message_id") or f"GW-{log.name}")


PROVIDERS = {"Dry run": _dry_run, "Frappe Email": _frappe_email, "HTTP gateway": _http_gateway}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def delivery_report(provider_ref: str, status: str, token: str):
	"""A gateway's delivery report: Delivered, Read or Failed for a message it accepted (FS-NTF-05). The token is the one
	set on the Messaging Provider; without a matching one nothing changes."""
	if status not in ("Delivered", "Read", "Failed"):
		frappe.throw(_("Unknown status."))
	log_name = frappe.db.get_value("Message Log", {"provider_ref": provider_ref})
	if not log_name:
		frappe.throw(_("Unknown message."), frappe.DoesNotExistError)
	log = frappe.get_doc("Message Log", log_name)
	prov = frappe.get_doc("Messaging Provider", log.provider) if log.provider else None
	expected = prov.get_password("callback_token", raise_exception=False) if prov else None
	if not expected or not hmac.compare_digest(str(expected), str(token or "")):
		frappe.throw(_("Not allowed."), frappe.PermissionError)
	if log.status in ("Read",) or (log.status == "Delivered" and status == "Delivered"):
		return log.status
	if status == "Failed":
		log.attempts = cint(log.attempts)
		log.db_set({"status": "Failed", "error": _("The gateway reported a failed delivery.")}, update_modified=False)
		_fallback(log)
	else:
		log.db_set({"status": status, "delivered_at": now_datetime()}, update_modified=False)
	_sync_reference(log.name)
	return frappe.db.get_value("Message Log", log.name, "status")


# ---------------------------------------------------------------- bill PDF link (FS-NTF-08)
def _secret() -> bytes:
	from frappe.utils.password import get_encryption_key

	key = get_encryption_key()
	return key.encode() if isinstance(key, str) else key


def bill_token(name) -> str:
	return hmac.new(_secret(), f"bill:{name}".encode(), hashlib.sha256).hexdigest()[:32]


def bill_link(name) -> str:
	return f"{get_url()}/api/method/utility_billing.utility_billing.notify.public_bill?name={name}&token={bill_token(name)}"


@frappe.whitelist(allow_guest=True)
def public_bill(name: str, token: str):
	"""The PDF behind the link in a bill message: opens without signing in, for exactly this bill and no other."""
	from utility_billing.utility_billing.bill_print import pdf_of, send_pdf

	if not hmac.compare_digest(bill_token(name), token or ""):
		frappe.throw(_("This link is not valid."), frappe.PermissionError)
	if frappe.db.get_value("Utility Bill", name, "docstatus") != 1:
		frappe.throw(_("This bill is not available."), frappe.DoesNotExistError)
	frappe.set_user("Administrator")
	send_pdf(pdf_of([name]), f"{name}.pdf")


# ---------------------------------------------------------------- event sources
def safe(fn):
	"""A notification must never stop a payment, a bill run or a recovery run: a failure is logged and the work carries on.
	(In tests the error is raised, so it can't hide.)"""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		try:
			return fn(*args, **kwargs)
		except Exception:
			if frappe.flags.in_test:
				raise
			frappe.log_error(title=f"Notification failed in {fn.__name__}")
			return []

	return wrapper


def _money(v):
	return f"{flt(v):,.0f}"


@safe
def payment_received(receipt, reversed_=False):
	"""FS-PAY-09: a receipt notification as the payment is posted."""
	ev = "payment.reversed" if reversed_ else "payment.received"
	ctx = {"amount": _money(receipt.amount), "receipt": receipt.name, "date": formatdate(receipt.payment_date),
	       "balance": _money(frappe.db.get_value("Customer Ledger Entry", {"connection": receipt.connection}, "balance", order_by="seq desc") or 0)}
	names = notify(ev, receipt.connection, ctx, reference=("Payment Receipt", receipt.name))
	queue_send(names)
	return names


@safe
def bill_revised(bill):
	"""A revised bill replaces an earlier one: tell the consumer, with the link to the new PDF (FS-BIL-11)."""
	names = notify("bill.revised", bill.connection, {
		"bill_month": getdate(bill.billing_month).strftime("%B %Y"), "amount": _money(bill.payable_within_due),
		"due_date": formatdate(bill.get("extended_due_date") or bill.due_date), "link": bill_link(bill.name)}, reference=("Utility Bill", bill.name))
	queue_send(names)
	return names


@safe
def bills_issued(run_name):
	"""A bill run was posted: one message per bill with the PDF link (FS-NTF-01, 08)."""
	names = []
	for b in frappe.get_all("Utility Bill", filters={"bill_run": run_name, "docstatus": 1},
	                        fields=["name", "connection", "billing_month", "current_amount", "payable_within_due", "payable_after_due", "due_date"]):
		names += notify("bill.issued", b.connection, {
			"bill_month": getdate(b.billing_month).strftime("%B %Y"), "amount": _money(b.payable_within_due), "amount_after_due": _money(b.payable_after_due),
			"due_date": formatdate(b.due_date), "link": bill_link(b.name)}, reference=("Utility Bill", b.name))
	queue_send(names)
	return names


@safe
def after_run_posted(run):
	"""Bill Run on_submit hook point: the messages are written in the background for a big run."""
	if frappe.flags.in_test:
		return bills_issued(run.name)
	frappe.enqueue("utility_billing.utility_billing.notify.bills_issued", run_name=run.name, queue="long", enqueue_after_commit=True)


def _already_sent(event, doctype, name) -> bool:
	return bool(frappe.db.exists("Message Log", {"event": event, "reference_doctype": doctype, "reference_name": name,
	                                              "status": ["!=", "Cancelled"]}))


def daily(as_of=None, society=None):
	"""Scheduler, daily: reminders before the due date and overdue notices, once per bill (FS-NTF-01)."""
	as_of = getdate(as_of or nowdate())
	sent = 0
	for soc in frappe.get_all("Society", filters={"name": society} if society else {}, fields=["name", "reminder_days_before_due"]):
		rules = rules_of(soc.name)
		days = cint(soc.reminder_days_before_due)
		for event, due in (("reminder.due", add_days(as_of, days)), ("bill.overdue", add_days(as_of, -1))):
			if not rules.get(event, ((), 0, False))[2] or (event == "reminder.due" and days <= 0):
				continue
			for b in _unpaid_bills(soc.name, due):
				if _already_sent(event, "Utility Bill", b.name):
					continue
				names = notify(event, b.connection, {"amount": _money(b.payable_within_due), "due_date": formatdate(b.due_date),
				                                     "balance": _money(b.balance), "bill_month": getdate(b.billing_month).strftime("%B %Y"),
				                                     "link": bill_link(b.name)}, reference=("Utility Bill", b.name))
				sent += len(names)
	return sent


def _unpaid_bills(society, due_date):
	"""Posted bills due on a date whose account still owes something."""
	rows = frappe.db.sql(
		"""select b.name, b.connection, b.billing_month, b.due_date, b.payable_within_due,
		          (select e.balance from `tabCustomer Ledger Entry` e where e.connection = b.connection order by e.seq desc limit 1) balance
		from `tabUtility Bill` b where b.society = %s and b.docstatus = 1 and b.status = 'Posted'
		  and ifnull(b.extended_due_date, b.due_date) = %s""", (society, due_date), as_dict=True)
	return [r for r in rows if flt(r.balance) > 0]


@safe
def send_recovery_notice(notice):
	"""An SMS or WhatsApp recovery notice goes through the message queue; the notice follows its message (FS-REC-06)."""
	ch = notice.channel
	if ch not in ("SMS", "WhatsApp"):
		return []
	names = notify("recovery.notice", notice.connection, {"message": notice.message}, reference=("Recovery Notice", notice.name),
	               text=notice.message, channels=[ch] + [c for c in engine.DEFAULT_RULES["recovery.notice"] if c != ch])
	queue_send(names)
	log = frappe.db.get_value("Message Log", names[0], ["status", "channel"], as_dict=True) if names else None
	if log and log.status == "Skipped":
		frappe.db.set_value("Recovery Notice", notice.name, "status", "Failed")
	return names


# ---------------------------------------------------------------- broadcast (FS-NTF-09)
def broadcast_connections(b) -> list[str]:
	filters = {"society": b.society, "status": "Active"}
	if b.audience == "Feeder":
		filters["feeder"] = b.feeder
	elif b.audience == "Transformer":
		filters["transformer"] = b.transformer
	conns = frappe.get_all("Connection", filters=filters, fields=["name", "property"])
	if b.audience == "Block":
		blocks = {p.name: p.block for p in frappe.get_all("Property", filters={"society": b.society}, fields=["name", "block"])}
		conns = [c for c in conns if blocks.get(c.property) == b.block]
	return [c.name for c in conns]


def send_broadcast(b):
	if b.status != "Draft":
		frappe.throw(_("This broadcast was already sent."))
	conns = broadcast_connections(b)
	if not conns:
		frappe.throw(_("No active connection matches this audience."))
	queued = []
	for c in conns:
		queued += [n for n in notify("outage.notice", c, {"message": b.message}, text=b.message, broadcast=b.name)
		           if frappe.db.get_value("Message Log", n, "status") != "Skipped"]
	b.recipients, b.messages_queued = len(conns), len(queued)
	b.status, b.sent_by, b.sent_on = "Sent", frappe.session.user, now_datetime()
	queue_send(queued)
	return len(queued)


# ---------------------------------------------------------------- cost (FS-NTF-06)
def cost_rows(society, from_date, to_date):
	return frappe.db.sql(
		"""select channel, status, count(*) messages, sum(cost) cost, sum(parts) parts from `tabMessage Log`
		where society = %s and date(creation) between %s and %s group by channel, status""", (society, from_date, to_date), as_dict=True)
