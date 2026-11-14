# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for notifications (increment 12; FS-NTF-01 to 09, FS-PAY-09, FS-REC-06 sending).
House N 2308 (spec test case 1) is the consumer. Time is fixed with `at()`, so quiet hours don't depend on the hour the tests run."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import frappe

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import mis, notify, recovery
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, CASHIER, RECOVERY_OFFICER

NOON = datetime(2026, 11, 14, 12, 0)
GATEWAY = "https://sms.example.test/send"


@contextmanager
def at(when):
	with patch.object(notify, "now_datetime", return_value=when):
		yield


class IntegrationTestNotify(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		self.consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		frappe.db.set_value("Consumer", self.consumer, {"mobile_no": "0300-1234567", "email": "house@example.com", "notify_sms": 1,
		                                                "notify_email": 1, "notify_whatsapp": 0, "preferred_language": "English"})
		self.provider("SMS", cost=2)
		self.provider("Email")

	# ---- helpers
	def provider(self, channel, kind="Dry run", cost=0, society=SOC, **kw):
		return frappe.get_doc({"doctype": "Messaging Provider", "channel": channel, "provider_type": kind, "society": society,
		                       "cost_per_message": cost, "enabled": 1, **kw}).insert()

	def logs(self, **filters):
		return frappe.get_all("Message Log", filters={"society": SOC, **filters}, fields=["*"], order_by="creation, name")

	def society(self, **values):
		s = frappe.get_doc("Society", SOC)
		s.update(values)
		s.save()

	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	# ---- FS-PAY-09: the receipt message
	def test_a_payment_sends_a_receipt_message_with_the_amount_and_balance(self):
		with at(NOON):
			receipt = self.pay(1000, "2026-11-12")
			(log,) = self.logs(event="payment.received")
			self.assertEqual((log.channel, log.status, log.recipient, log.critical), ("SMS", "Queued", "0300-1234567", 0))
			self.assertEqual((log.reference_doctype, log.reference_name), ("Payment Receipt", receipt.name))
			self.assertIn("Rs 1,000", log.body)
			self.assertIn(receipt.name, log.body)
			self.assertEqual(notify.process_queue(), 1)
		log = frappe.get_doc("Message Log", log.name)
		self.assertEqual((log.status, log.attempts, log.cost, log.parts, log.provider_ref), ("Sent", 1, 2, 1, f"DRY-{log.name}"))
		self.assertTrue(log.sent_at)

	def test_a_reversed_payment_is_critical_and_goes_out_in_quiet_hours(self):
		from utility_billing.utility_billing import account

		with at(NOON):
			r = self.pay(1000, "2026-11-12")
		with at(datetime(2026, 11, 14, 23, 30)):
			frappe.set_user(BILLING_MANAGER)
			account.reverse_document("Payment Receipt", r.name, "CHQ-BOUNCE", "Cheque returned unpaid")
			frappe.set_user("Administrator")
			(log,) = self.logs(event="payment.reversed")
			self.assertEqual((log.critical, log.status), (1, "Queued"))
			self.assertEqual(notify.process_queue(), 2)  # the earlier receipt message (due since noon) and this critical one
		self.assertEqual(frappe.db.get_value("Message Log", log.name, "status"), "Sent")  # a critical message isn't held at 23:30

	# ---- FS-NTF-04: quiet hours, opt-in, preferred channel
	def test_quiet_hours_hold_an_ordinary_message_until_they_end(self):
		with at(datetime(2026, 11, 14, 23, 0)):
			self.pay(500, "2026-11-12")
			(log,) = self.logs(event="payment.received")
			self.assertEqual(str(log.scheduled_for), "2026-11-15 08:00:00")
			notify.process_queue()  # other sites' data may have their own messages due: only this one is checked
			self.assertEqual(frappe.db.get_value("Message Log", log.name, "status"), "Queued")
		with at(datetime(2026, 11, 15, 8, 1)):
			self.assertGreaterEqual(notify.process_queue(), 1)
		self.assertEqual(frappe.db.get_value("Message Log", log.name, "status"), "Sent")

	def test_a_consumer_who_opted_out_gets_the_next_channel_or_a_skipped_row(self):
		frappe.db.set_value("Consumer", self.consumer, "notify_sms", 0)
		with at(NOON):
			self.pay(500, "2026-11-12")
			(log,) = self.logs(event="payment.received")
			self.assertEqual((log.channel, log.recipient), ("Email", "house@example.com"))
			self.assertIsNone(log.fallback_of)
			frappe.db.set_value("Consumer", self.consumer, "notify_email", 0)
			self.pay(500, "2026-11-12")
			skipped = self.logs(event="payment.received", status="Skipped")
			self.assertEqual(len(skipped), 1)
			self.assertIn("No channel the consumer accepts", skipped[0].error)

	def test_the_preferred_channel_goes_first(self):
		frappe.db.set_value("Consumer", self.consumer, "preferred_channel", "Email")
		with at(NOON):
			self.pay(500, "2026-11-12")
		self.assertEqual(self.logs(event="payment.received")[0].channel, "Email")

	def test_an_event_can_be_switched_off_or_mapped_to_other_channels(self):
		self.society(notification_rules=[{"event": "payment.received", "channel_1": "Email", "channel_2": "SMS", "enabled": 1},
		                                 {"event": "bill.overdue", "channel_1": "SMS", "enabled": 0}])
		with at(NOON):
			self.pay(500, "2026-11-12")
			self.assertEqual(self.logs(event="payment.received")[0].channel, "Email")
			self.assertEqual(notify.notify("bill.overdue", self.conn, {}), [])  # switched off: nothing, not even a skipped row

	def test_the_event_rules_must_be_sensible(self):
		s = frappe.get_doc("Society", SOC)
		s.set("notification_rules", [{"event": "bill.issued", "channel_1": "SMS", "enabled": 1}, {"event": "bill.issued", "channel_1": "Email", "enabled": 1}])
		with self.assertRaises(frappe.ValidationError):
			s.save()
		s.set("notification_rules", [{"event": "bill.issued", "channel_1": "SMS", "channel_2": "SMS", "enabled": 1}])
		with self.assertRaises(frappe.ValidationError):
			s.save()
		s.set("notification_rules", [{"event": "bill.issued", "enabled": 1}])
		with self.assertRaises(frappe.ValidationError):
			s.save()

	# ---- WhatsApp: off by default, approved templates only (NTF-03, 06)
	def test_whatsapp_needs_the_switch_the_opt_in_and_an_approved_template(self):
		frappe.db.set_value("Consumer", self.consumer, "notify_whatsapp", 1)
		self.society(notification_rules=[{"event": "payment.received", "channel_1": "WhatsApp", "channel_2": "SMS", "enabled": 1}])
		with at(NOON):
			self.pay(500, "2026-11-12")
			self.assertEqual(self.logs(event="payment.received")[0].channel, "SMS")  # the society hasn't switched WhatsApp on
			self.society(whatsapp_enabled=1)
			self.pay(500, "2026-11-12")
			self.assertEqual(self.logs(event="payment.received")[1].channel, "SMS")  # on, but no approved template yet
			t = frappe.get_doc({"doctype": "Notification Template", "event": "payment.received", "channel": "WhatsApp", "language": "English",
			                    "body": "Thanks {consumer}: Rs {amount} received, receipt {receipt}.", "whatsapp_template_name": "payment_received",
			                    "whatsapp_status": "Submitted"}).insert()
			self.pay(500, "2026-11-12")
			self.assertEqual(self.logs(event="payment.received")[2].channel, "SMS")  # submitted isn't approved
			t.whatsapp_status = "Approved"
			t.save()
			self.pay(500, "2026-11-12")
			self.assertEqual(self.logs(event="payment.received")[3].channel, "WhatsApp")

	def test_free_text_never_goes_by_whatsapp(self):
		frappe.db.set_value("Consumer", self.consumer, "notify_whatsapp", 1)
		self.society(whatsapp_enabled=1)
		(name,) = notify.notify("outage.notice", self.conn, {}, text="Power off 9 to 1", channels=["WhatsApp", "SMS"])
		self.assertEqual(frappe.db.get_value("Message Log", name, "channel"), "SMS")

	# ---- templates (NTF-03)
	def test_a_societys_template_beats_the_default_and_language_falls_back_to_english(self):
		frappe.get_doc({"doctype": "Notification Template", "event": "payment.received", "channel": "SMS", "language": "English",
		                "body": "DEFAULT: {amount} {receipt}"}).insert()
		with at(NOON):
			self.pay(500, "2026-11-12")
			self.assertTrue(self.logs(event="payment.received")[0].body.startswith("DEFAULT: 500"))
			frappe.get_doc({"doctype": "Notification Template", "society": SOC, "event": "payment.received", "channel": "SMS", "language": "English",
			                "body": "OURS: {amount}"}).insert()
			self.pay(500, "2026-11-12")
			self.assertEqual(self.logs(event="payment.received")[1].body, "OURS: 500")
			frappe.db.set_value("Consumer", self.consumer, "preferred_language", "Urdu")
			frappe.get_doc({"doctype": "Notification Template", "society": SOC, "event": "payment.received", "channel": "SMS", "language": "Urdu",
			                "body": "آپ کی ادائیگی {amount}"}).insert()
			self.pay(500, "2026-11-12")
			self.assertEqual(self.logs(event="payment.received")[2].body, "آپ کی ادائیگی 500")

	def test_template_rules(self):
		args = {"doctype": "Notification Template", "society": SOC, "event": "bill.issued", "channel": "SMS", "language": "English", "body": "x {amount}"}
		frappe.get_doc(args).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(args).insert()  # one per society, event, channel and language
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({**args, "event": "bill.revised", "body": "y" * 481}).insert()  # a long SMS
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({**args, "event": "bill.revised", "channel": "WhatsApp", "whatsapp_status": "Approved", "body": "z"}).insert()  # no Meta name

	# ---- providers, retries, fallback (NTF-02, 07)
	def test_a_failing_gateway_is_retried_then_the_next_channel_is_tried(self):
		for p in frappe.get_all("Messaging Provider", filters={"channel": "SMS"}, pluck="name"):
			frappe.db.set_value("Messaging Provider", p, "enabled", 0)
		self.provider("SMS", "HTTP gateway", cost=3, endpoint_url=GATEWAY, api_key="not-a-real-key", sender_id="UB")
		with at(NOON):
			self.pay(500, "2026-11-12")
		(log,) = self.logs(event="payment.received")
		down = MagicMock(status_code=503, text="gateway down")
		with patch("requests.post", return_value=down) as post:
			with at(NOON):
				notify.process_queue()
			row = frappe.get_doc("Message Log", log.name)
			self.assertEqual((row.status, row.attempts), ("Queued", 1))
			self.assertEqual(str(row.scheduled_for), "2026-11-14 12:05:00")
			self.assertIn("503", row.error)
			with at(NOON + timedelta(minutes=6)):
				notify.process_queue()
			self.assertEqual(str(frappe.db.get_value("Message Log", log.name, "scheduled_for")), "2026-11-14 12:21:00")
			with at(NOON + timedelta(minutes=22)):
				notify.process_queue()
			self.assertEqual(post.call_count, 3)
			self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer not-a-real-key"})
			self.assertEqual(post.call_args.kwargs["json"]["to"], "0300-1234567")
		row = frappe.get_doc("Message Log", log.name)
		self.assertEqual((row.status, row.attempts), ("Failed", 3))
		(fallback,) = self.logs(fallback_of=log.name)
		self.assertEqual((fallback.channel, fallback.recipient, fallback.status, fallback.body), ("Email", "house@example.com", "Queued", row.body))
		with at(NOON + timedelta(minutes=23)):
			notify.process_queue()
		self.assertEqual(frappe.db.get_value("Message Log", fallback.name, "status"), "Sent")
		self.assertEqual(notify._chain(frappe.get_doc("Message Log", fallback.name)), ["Email", "SMS"])

	def test_a_working_gateway_records_its_reference_and_cost_by_parts(self):
		for p in frappe.get_all("Messaging Provider", filters={"channel": "SMS"}, pluck="name"):
			frappe.db.set_value("Messaging Provider", p, "enabled", 0)
		self.provider("SMS", "HTTP gateway", cost=3, endpoint_url=GATEWAY, api_key="not-a-real-key")
		ok = MagicMock(status_code=200)
		ok.json.return_value = {"id": "GW-77"}
		with at(NOON), patch("requests.post", return_value=ok):
			(name,) = notify.notify("outage.notice", self.conn, {}, text="x" * 200, channels=["SMS"])
			notify.send_log(name)
		log = frappe.get_doc("Message Log", name)
		self.assertEqual((log.status, log.provider_ref, log.parts, log.cost), ("Sent", "GW-77", 2, 6))

	def test_nothing_is_sent_without_a_provider_and_the_message_waits(self):
		for p in frappe.get_all("Messaging Provider", filters={"channel": "SMS"}, pluck="name"):
			frappe.delete_doc("Messaging Provider", p, force=True)
		with at(NOON):
			(name,) = notify.notify("outage.notice", self.conn, {}, text="Power off", channels=["SMS"])
			self.assertEqual(notify.send_log(name), "Queued")
		row = frappe.get_doc("Message Log", name)
		self.assertEqual(row.attempts, 0)
		self.assertIn("No SMS provider", row.error)

	def test_gateway_settings_are_checked(self):
		with self.assertRaises(frappe.ValidationError):
			self.provider("SMS", "HTTP gateway", endpoint_url="http://insecure.example/send")
		with self.assertRaises(frappe.ValidationError):
			self.provider("SMS", "Frappe Email")
		with self.assertRaises(frappe.ValidationError):
			self.provider("SMS", cost=-1)

	def test_a_societys_own_provider_beats_a_shared_one(self):
		shared = self.provider("SMS", society=None, cost=9)
		own = frappe.get_all("Messaging Provider", filters={"channel": "SMS", "society": SOC}, pluck="name")[0]
		self.assertEqual(notify.provider_for(SOC, "SMS").name, own)
		frappe.db.set_value("Messaging Provider", own, "enabled", 0)
		self.assertEqual(notify.provider_for(SOC, "SMS").name, shared.name)

	def test_email_goes_through_frappe_email_when_no_provider_is_set(self):
		for p in frappe.get_all("Messaging Provider", filters={"channel": "Email"}, pluck="name"):
			frappe.delete_doc("Messaging Provider", p, force=True)
		self.assertEqual(notify.provider_for(SOC, "Email").provider_type, "Frappe Email")

	# ---- delivery reports (NTF-05)
	def test_a_delivery_report_needs_the_providers_token(self):
		for p in frappe.get_all("Messaging Provider", filters={"channel": "SMS"}, pluck="name"):
			frappe.db.set_value("Messaging Provider", p, "enabled", 0)
		self.provider("SMS", "HTTP gateway", endpoint_url=GATEWAY, api_key="k", callback_token="report-token")
		ok = MagicMock(status_code=200)
		ok.json.return_value = {"id": "GW-9"}
		with at(NOON), patch("requests.post", return_value=ok):
			(name,) = notify.notify("outage.notice", self.conn, {}, text="Power off", channels=["SMS"])
			notify.send_log(name)
		with self.assertRaises(frappe.PermissionError):
			notify.delivery_report("GW-9", "Delivered", "wrong")
		with self.assertRaises(frappe.PermissionError):
			notify.delivery_report("GW-9", "Delivered", "")
		with self.assertRaises(frappe.ValidationError):
			notify.delivery_report("GW-9", "Exploded", "report-token")
		self.assertEqual(notify.delivery_report("GW-9", "Delivered", "report-token"), "Delivered")
		self.assertTrue(frappe.db.get_value("Message Log", name, "delivered_at"))
		self.assertEqual(notify.delivery_report("GW-9", "Read", "report-token"), "Read")
		self.assertEqual(notify.delivery_report("GW-9", "Delivered", "report-token"), "Read")  # never goes backwards

	def test_a_failed_delivery_report_tries_the_next_channel(self):
		for p in frappe.get_all("Messaging Provider", filters={"channel": "SMS"}, pluck="name"):
			frappe.db.set_value("Messaging Provider", p, "enabled", 0)
		self.provider("SMS", "HTTP gateway", endpoint_url=GATEWAY, api_key="k", callback_token="t")
		self.society(notification_rules=[{"event": "outage.notice", "channel_1": "SMS", "channel_2": "Email", "critical": 1, "enabled": 1}])
		ok = MagicMock(status_code=200)
		ok.json.return_value = {"id": "GW-5"}
		with at(NOON), patch("requests.post", return_value=ok):
			(name,) = notify.notify("outage.notice", self.conn, {}, text="Power off")
			notify.send_log(name)
		with at(NOON):
			self.assertEqual(notify.delivery_report("GW-5", "Failed", "t"), "Failed")
		self.assertEqual([r.channel for r in self.logs(fallback_of=name)], ["Email"])

	# ---- bill messages and the PDF link (NTF-01, 08)
	def test_bill_messages_carry_a_pdf_link_that_opens_only_that_bill(self):
		self.october_posted()
		log = [m for m in self.logs(event="bill.issued") if m.reference_doctype == "Utility Bill"][-1]  # the October bill (September's run came first)
		bill = log.reference_name
		self.assertEqual(log.channel, "SMS")
		self.assertIn("Rs 23,364 (Rs 25,145 after the due date)", log.body)  # spec test case 1: payable within and after the due date
		self.assertIn(f"name={bill}&token={notify.bill_token(bill)}", log.body)
		self.assertEqual(log.bill_link, notify.bill_link(bill))
		self.assertNotEqual(notify.bill_token(bill), notify.bill_token("another-bill"))
		with self.assertRaises(frappe.PermissionError):
			notify.public_bill(bill, "0" * 32)
		with self.assertRaises(frappe.PermissionError):
			notify.public_bill(bill, notify.bill_token("another-bill"))
		notify.public_bill(bill, notify.bill_token(bill))
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))

	# ---- reminders and overdue notices (NTF-01)
	def test_reminders_and_overdue_notices_go_once_per_bill(self):
		self.october_posted()  # the October bill is due 15-11-2026 and the account still owes it
		with at(NOON):
			self.assertEqual(notify.daily("2026-11-12", SOC), 1)  # three days before the due date
			self.assertEqual(notify.daily("2026-11-12", SOC), 0)  # not twice
			self.assertEqual(notify.daily("2026-11-13", SOC), 0)
			self.assertEqual(notify.daily("2026-11-16", SOC), 1)  # the day after the due date
		events = [m.event for m in self.logs() if m.event in ("reminder.due", "bill.overdue")]
		self.assertEqual(events, ["reminder.due", "bill.overdue"])
		self.assertIn(frappe.utils.formatdate("2026-11-15"), [m for m in self.logs(event="reminder.due")][0].body)

	def test_no_reminder_for_a_bill_that_is_paid(self):
		self.october_posted()
		with at(NOON):
			self.pay(23_364, "2026-11-12")
			self.assertEqual(notify.daily("2026-11-12", SOC), 0)
			self.assertEqual(notify.daily("2026-11-16", SOC), 0)

	def test_reminders_can_be_turned_off_by_days_or_by_rule(self):
		self.october_posted()
		self.society(reminder_days_before_due=0)
		with at(NOON):
			self.assertEqual(notify.daily("2026-11-12", SOC), 0)
		self.society(reminder_days_before_due=3, notification_rules=[{"event": "reminder.due", "channel_1": "SMS", "enabled": 0}])
		with at(NOON):
			self.assertEqual(notify.daily("2026-11-12", SOC), 0)

	# ---- recovery notices by SMS (REC-06)
	def test_a_recovery_sms_follows_its_message_to_sent_or_failed(self):
		s = frappe.get_doc("Society", SOC)
		s.defaulter_min_amount, s.defaulter_min_days = 10_000, 60
		s.set("recovery_stages", [{"stage": "Reminder", "days_overdue": 15, "channel": "SMS", "message": "Dear {consumer}, {owed} is overdue."}])
		s.save()
		self.october_posted()
		run = frappe.get_doc({"doctype": "Recovery Run", "society": SOC, "as_of": "2026-12-20"})
		recovery.prepare_run(run)
		run.insert()
		with at(NOON):
			run.issue()
			run.reload()
			n = run.items[0].notice
			self.assertEqual(frappe.db.get_value("Recovery Notice", n, "status"), "Queued")
			(log,) = self.logs(event="recovery.notice")
			self.assertEqual((log.channel, log.critical, log.body, log.reference_name), ("SMS", 1, "Dear House N 2308 Owner, Rs 23,364 is overdue.", n))
			notify.process_queue()
		self.assertEqual(frappe.db.get_value("Recovery Notice", n, "status"), "Sent")

	def test_a_recovery_sms_to_a_consumer_without_a_mobile_fails_visibly(self):
		frappe.db.set_value("Consumer", self.consumer, {"mobile_no": "", "notify_email": 0})
		s = frappe.get_doc("Society", SOC)
		s.defaulter_min_amount, s.defaulter_min_days = 10_000, 60
		s.set("recovery_stages", [{"stage": "Reminder", "days_overdue": 15, "channel": "SMS", "message": "x {owed}"}])
		s.save()
		self.october_posted()
		run = frappe.get_doc({"doctype": "Recovery Run", "society": SOC, "as_of": "2026-12-20"})
		recovery.prepare_run(run)
		run.insert()
		run.issue()
		run.reload()
		self.assertEqual(frappe.db.get_value("Recovery Notice", run.items[0].notice, "status"), "Failed")

	# ---- broadcast (NTF-09)
	def test_a_broadcast_reaches_the_whole_society_or_a_block(self):
		frappe.db.set_value("Property", self.prop, "block", "N Block")
		b = frappe.get_doc({"doctype": "Broadcast", "society": SOC, "audience": "Whole society", "message": "Power off 20-11 9 am to 1 pm."}).insert()
		with at(NOON):
			self.assertEqual(b.send(), 1)
		b.reload()
		self.assertEqual((b.status, b.recipients, b.messages_queued, b.sent_by), ("Sent", 1, 1, "Administrator"))
		(log,) = self.logs(event="outage.notice")
		self.assertEqual((log.broadcast, log.critical, log.body), (b.name, 1, "Power off 20-11 9 am to 1 pm."))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("Broadcast", b.name).send()  # already sent
		other = frappe.get_doc({"doctype": "Broadcast", "society": SOC, "audience": "Block", "block": "Z Block", "message": "Water cut"}).insert()
		with self.assertRaises(frappe.ValidationError):
			other.send()  # nobody lives in Z Block
		mine = frappe.get_doc({"doctype": "Broadcast", "society": SOC, "audience": "Block", "block": "N Block", "message": "Water cut"}).insert()
		self.assertEqual(mine.send(), 1)

	def test_a_broadcast_needs_its_target_and_a_message(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Broadcast", "society": SOC, "audience": "Feeder", "message": "x"}).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Broadcast", "society": SOC, "audience": "Whole society", "message": " "}).insert()

	def test_only_senders_can_broadcast(self):
		b = frappe.get_doc({"doctype": "Broadcast", "society": SOC, "audience": "Whole society", "message": "x"}).insert()
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Broadcast", b.name).send()

	# ---- cost report (NTF-06)
	def test_the_cost_report_counts_messages_and_cost_per_channel(self):
		with at(NOON):
			self.pay(500, "2026-11-12")
			self.pay(500, "2026-11-12")
			notify.process_queue()
			frappe.db.set_value("Consumer", self.consumer, "notify_sms", 0)
			self.pay(500, "2026-11-12")
			notify.process_queue()
		cols, data, msg, *_rest = mis.run("message_cost", {"society": SOC, "from_date": "2026-01-01", "to_date": "2030-12-31"})
		by = {r["channel"]: r for r in data}
		self.assertEqual((by["SMS"]["total"], by["SMS"]["sent"], by["SMS"]["cost"], by["SMS"]["parts"]), (2, 2, 4, 2))
		self.assertEqual((by["Email"]["total"], by["Email"]["sent"]), (1, 1))
		self.assertEqual(by["Total"]["total"], 3)
		self.assertIn("WhatsApp is switched off", msg)

	def test_message_logs_are_system_written(self):
		with at(NOON):
			self.pay(500, "2026-11-12")
		name = self.logs()[0].name
		frappe.set_user(RECOVERY_OFFICER)
		self.assertTrue(frappe.has_permission("Message Log", "read", name))
		self.assertFalse(frappe.has_permission("Message Log", "write", name))
		self.assertFalse(frappe.has_permission("Message Log", "create"))
		self.assertFalse(frappe.has_permission("Messaging Provider", "read"))
