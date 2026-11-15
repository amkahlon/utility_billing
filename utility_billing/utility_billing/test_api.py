# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for the billing API, 1Link/Kuickpay services and events (FS-API-01 to 03, 06 to 10).
House N 2308 (spec test case 1) owes Rs 1,104 after September; the API is called as the channel's integration user."""

import json
import random
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import nowdate

from utility_billing.api import channels, guard, v1
from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import events
from utility_billing.utility_billing.test_billing import SOC, BillingCase

API_USER = "tst.api.1link@example.com"
OTHER_USER = "tst.api.portal@example.com"


def integration_user(email):
	if not frappe.db.exists("User", email):
		u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0, "user_type": "System User"})
		u.append("roles", {"role": "API Integration"})
		u.insert(ignore_permissions=True)
	return email


class UnitTestChannelFormats(UnitTestCase):
	def test_amounts_and_dates(self):
		self.assertEqual(channels.amount_out(23364.5), "+0000002336450")
		self.assertEqual(channels.amount_out(0), "+0000000000000")
		self.assertEqual(channels.amount_out(-300), "-0000000030000")
		for text in ("+0000002336450", "0000002336450", "23364.50", "23364.5"):
			self.assertEqual(channels.amount_in(text), 23364.5, text)
		self.assertRaises(ValueError, channels.amount_in, "")
		self.assertEqual(str(channels.date_in("20261115")), "2026-11-15")
		self.assertEqual(channels.date_out("2026-11-05"), "20261105")


class IntegrationTestBillingAPI(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		self.sep = self.september_posted_with_arrears()  # owes 1,104
		self.client = self.make_client("TST 1Link", API_USER, channel="1Link", payment_mode="1Link")

	def make_client(self, name, user, **kw):
		values = {"doctype": "API Client", "client_name": name, "user": integration_user(user), "enabled": 1, "rate_limit": 100,
		          "scope_account_read": 1, "scope_bill_read": 1, "scope_payment_write": 1, "scope_payment_reverse": 1, "scope_events": 1,
		          "societies": [{"society": SOC}], **kw}
		return frappe.get_doc(values).insert()

	def as_api(self, fn, *a, user=API_USER, **kw):
		frappe.set_user(user)
		try:
			return fn(*a, **kw)
		finally:
			frappe.set_user("Administrator")

	# ---- reading
	def test_account_enquiry_bills_and_statement(self):
		e = self.as_api(v1.account_enquiry, reference_no=self.conn)
		self.assertEqual((e["reference_no"], e["balance"], e["amount_due"], e["society"]), (self.conn, 1104, 1104, SOC))
		self.assertEqual(e["latest_bill"]["billing_month"], "September 2026")
		b = self.as_api(v1.bills, reference_no=self.conn)
		self.assertEqual([x["billing_month"] for x in b["bills"]], ["2026-09"])
		s = self.as_api(v1.statement, reference_no=self.conn)
		self.assertEqual((s["closing"], s["entries"][-1]["type"]), (1104, "Payment"))
		sr = frappe.get_all("API Call Log", filters={"client": "TST 1Link"}, fields=["endpoint", "status_code"], order_by="creation")
		self.assertEqual([(x.endpoint, x.status_code) for x in sr], [("account_enquiry", 200), ("bills", 200), ("statement", 200)])

	def test_scopes_societies_and_unknown_users_are_refused(self):
		frappe.db.set_value("API Client", "TST 1Link", "scope_bill_read", 0)
		frappe.clear_document_cache("API Client", "TST 1Link")
		self.assertRaises(frappe.PermissionError, self.as_api, v1.bills, reference_no=self.conn)
		self.make_client("TST Portal", OTHER_USER, societies=[{"society": "TST-RA"}] if frappe.db.exists("Society", "TST-RA") else [])
		self.assertRaises(frappe.PermissionError, self.as_api, v1.account_enquiry, reference_no=self.conn, user=OTHER_USER)
		self.assertRaises(frappe.PermissionError, v1.account_enquiry, reference_no=self.conn)  # Administrator isn't an API client
		self.assertRaises(frappe.DoesNotExistError, self.as_api, v1.account_enquiry, reference_no="9999999999")
		log = frappe.get_all("API Call Log", filters={"client": "TST 1Link"}, pluck="status_code")
		self.assertIn(403, log)
		self.assertIn(404, log)

	def test_rate_limit_and_ip_allow_list(self):
		frappe.db.set_value("API Client", "TST 1Link", {"rate_limit": 2, "ip_allow_list": ""})
		frappe.clear_document_cache("API Client", "TST 1Link")
		minute = random.randint(10**6, 10**7) * 60.0  # a minute of its own, so earlier runs' counts don't matter
		with patch.object(guard.time, "time", return_value=minute):
			self.as_api(v1.account_enquiry, reference_no=self.conn)
			self.as_api(v1.account_enquiry, reference_no=self.conn)
			self.assertRaises(guard.RateLimited, self.as_api, v1.account_enquiry, reference_no=self.conn)
		frappe.db.set_value("API Client", "TST 1Link", {"rate_limit": 100, "ip_allow_list": "10.9.9.9"})
		frappe.clear_document_cache("API Client", "TST 1Link")
		frappe.local.request_ip = "10.1.1.1"
		try:
			self.assertRaises(frappe.PermissionError, self.as_api, v1.account_enquiry, reference_no=self.conn)
		finally:
			frappe.local.request_ip = None

	# ---- payments, idempotency, reversal
	def test_a_payment_posts_once_per_idempotency_key(self):
		self.assertRaises(frappe.ValidationError, self.as_api, v1.post_payment, reference_no=self.conn, amount=1104, paid_on=nowdate(), transaction_id="T1")
		first = self.as_api(v1.post_payment, reference_no=self.conn, amount=1104, paid_on=nowdate(), transaction_id="T1", idempotency_key="K1")
		again = self.as_api(v1.post_payment, reference_no=self.conn, amount=1104, paid_on=nowdate(), transaction_id="T1", idempotency_key="K1")
		self.assertEqual((first, first["balance_after"]), (again, 0))
		self.assertEqual(frappe.db.count("Payment Receipt", {"channel_txn_id": "T1"}), 1)
		r = frappe.get_doc("Payment Receipt", first["receipt"])
		self.assertEqual((r.payment_channel, r.mode, r.status, r.owner), ("Bank / Channel", "1Link", "Posted", API_USER))
		self.assertRaises(guard.KeyReused, self.as_api, v1.post_payment, reference_no=self.conn, amount=1000, paid_on=nowdate(),
		                  transaction_id="T2", idempotency_key="K1")
		self.assertRaises(frappe.DuplicateEntryError, self.as_api, v1.post_payment, reference_no=self.conn, amount=1104, paid_on=nowdate(),
		                  transaction_id="T1", idempotency_key="K2")

	def test_full_payment_unless_part_payments_are_accepted(self):
		self.assertRaises(frappe.ValidationError, self.as_api, v1.post_payment, reference_no=self.conn, amount=500, paid_on=nowdate(),
		                  transaction_id="P1", idempotency_key="P1")
		self.assertFalse(frappe.db.exists("Payment Receipt", {"channel_txn_id": "P1"}))
		frappe.db.set_value("API Client", "TST 1Link", "partial_payments", 1)
		frappe.clear_document_cache("API Client", "TST 1Link")
		out = self.as_api(v1.post_payment, reference_no=self.conn, amount=500, paid_on=nowdate(), transaction_id="P1", idempotency_key="P1b")
		self.assertEqual(out["balance_after"], 604)

	def test_a_channel_reverses_only_its_own_payment_the_same_day(self):
		out = self.as_api(v1.post_payment, reference_no=self.conn, amount=1104, paid_on=nowdate(), transaction_id="R1", idempotency_key="R1")
		self.make_client("TST Portal", OTHER_USER)
		self.assertRaises(frappe.PermissionError, self.as_api, v1.reverse_payment, transaction_id="R1", idempotency_key="X1", user=OTHER_USER)
		rev = self.as_api(v1.reverse_payment, transaction_id="R1", reason="Transfer failed", idempotency_key="R1-rev")
		self.assertEqual(rev["status"], "Reversed")
		r = frappe.get_doc("Payment Receipt", out["receipt"])
		self.assertEqual((r.status, r.reversal_reason), ("Reversed", "CHANNEL-REV"))
		from utility_billing.utility_billing import account

		self.assertEqual(account.balance(self.conn), 1104)

	# ---- 1Link / Kuickpay
	def test_1link_inquiry_payment_and_reversal(self):
		bad = self.as_api(channels.bill_inquiry, consumer_number="9999999999")
		self.assertEqual(bad["response_Code"], "01")
		inq = self.as_api(channels.bill_inquiry, consumer_number=self.conn, bank_mnemonic="MCB")
		self.assertEqual((inq["response_Code"], inq["bill_status"], inq["billing_month"], inq["amount_within_dueDate"]), ("00", "U", "2609", "+0000000110400"))
		self.assertEqual(inq["consumer_Detail"], "HOUSE N 2308 OWNER")
		wrong = self.as_api(channels.bill_payment, consumer_number=self.conn, tran_auth_id="AUTH1", transaction_amount="+0000000050000", tran_date=nowdate().replace("-", ""))
		self.assertEqual(wrong["response_Code"], "04")
		self.assertFalse(frappe.db.exists("Payment Receipt", {"channel_txn_id": "AUTH1"}))
		ok = self.as_api(channels.bill_payment, consumer_number=self.conn, tran_auth_id="AUTH1", transaction_amount="+0000000110400",
		                 tran_date=nowdate().replace("-", ""), tran_time="101500", bank_mnemonic="MCB")
		self.assertEqual(ok["response_Code"], "00")
		self.assertEqual(frappe.db.get_value("Payment Receipt", ok["Identification_parameter"], "mode"), "1Link")
		dup = self.as_api(channels.bill_payment, consumer_number=self.conn, tran_auth_id="AUTH1", transaction_amount="+0000000110400", tran_date=nowdate().replace("-", ""))
		self.assertEqual(dup["response_Code"], "03")
		paid = self.as_api(channels.bill_inquiry, consumer_number=self.conn)
		self.assertEqual((paid["bill_status"], paid["tran_auth_Id"], paid["amount_paid"]), ("P", "AUTH1", "+0000000110400"))
		rev = self.as_api(channels.bill_reversal, consumer_number=self.conn, tran_auth_id="AUTH1")
		self.assertEqual(rev["response_Code"], "00")
		self.assertEqual(self.as_api(channels.bill_reversal, consumer_number=self.conn, tran_auth_id="AUTH1")["response_Code"], "03")
		codes = frappe.get_all("API Call Log", filters={"client": "TST 1Link", "endpoint": "bill_payment"}, pluck="response_code", order_by="creation")
		self.assertEqual(codes, ["04", "00", "03"])

	def test_channel_services_are_for_channel_clients_and_blocked_accounts(self):
		self.make_client("TST Portal", OTHER_USER, channel="API")
		self.assertRaises(frappe.PermissionError, self.as_api, channels.bill_inquiry, consumer_number=self.conn, user=OTHER_USER)
		frappe.db.set_value("Connection", self.conn, "status", "Closed")
		inq = self.as_api(channels.bill_inquiry, consumer_number=self.conn)
		self.assertEqual((inq["response_Code"], inq["bill_status"]), ("02", "B"))

	# ---- events
	def subscribe(self, types):
		return frappe.get_doc({"doctype": "Event Subscription", "client": "TST 1Link", "url": "https://receiver.example.test/ums", "secret": "s3cret",
		                       "event_types": "\n".join(types)}).insert()

	def test_events_are_written_only_for_subscribers_and_delivered_signed(self):
		self.pay(100, nowdate())
		self.assertEqual(frappe.db.count("API Event"), 0)  # nobody subscribed
		self.subscribe(["payment.posted", "payment.reversed"])
		r = self.pay(200, nowdate(), txn="EV-1")
		(ev,) = frappe.get_all("API Event", filters={"document_name": r.name}, fields=["name", "event_type", "reference_no", "data"])
		self.assertEqual((ev.event_type, ev.reference_no, json.loads(ev.data)["transaction_id"]), ("payment.posted", self.conn, "EV-1"))
		ok = MagicMock(status_code=200, text="ok")
		with patch("requests.post", return_value=ok) as post, patch.object(frappe.db, "commit"):
			events.deliver()
		body = post.call_args.kwargs["data"]
		headers = post.call_args.kwargs["headers"]
		self.assertEqual(headers["X-UMS-Event-Id"], ev.name)
		self.assertEqual(headers["X-UMS-Signature"], "sha256=" + events.signature("s3cret", body))
		self.assertEqual(json.loads(body)["type"], "payment.posted")
		self.assertEqual(frappe.db.get_value("API Event Delivery", {"event": ev.name}, "status"), "Delivered")

	def test_failed_deliveries_are_retried_then_given_up(self):
		self.subscribe(["payment.posted"])
		self.pay(200, nowdate(), txn="EV-2")
		d = frappe.db.get_value("API Event Delivery", {"event_type": "payment.posted"}, "name")
		down = MagicMock(status_code=503, text="down")
		with patch("requests.post", return_value=down), patch.object(frappe.db, "commit"):
			events.deliver()
			self.assertEqual(frappe.db.get_value("API Event Delivery", d, ["status", "attempts"]), ("Pending", 1))
			for _ in range(len(events.BACKOFF_MINUTES)):
				frappe.db.set_value("API Event Delivery", d, "next_attempt", frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-1))
				events.deliver()
		self.assertEqual(frappe.db.get_value("API Event Delivery", d, ["status", "attempts"]), ("Failed", len(events.BACKOFF_MINUTES) + 1))
		self.assertIn("HTTP 503", frappe.db.get_value("API Event Delivery", d, "last_error"))

	def test_bill_and_disconnection_events(self):
		self.subscribe(["bill.issued", "connection.disconnected", "connection.reconnected"])
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		self.post(self.make_run("2026-10-01", "2026-11-01"))
		self.assertEqual(frappe.db.count("API Event", {"event_type": "bill.issued"}), 1)
		c = frappe.get_doc("Connection", self.conn)
		c.status = "Disconnected"
		c.disconnected_on = nowdate()
		c.flags.ignore_validate = True
		c.save()
		self.assertEqual(frappe.db.count("API Event", {"event_type": "connection.disconnected"}), 1)

	# ---- documentation
	def test_openapi_document_lists_every_method(self):
		spec = v1.openapi()
		paths = spec["paths"]
		for p in ("utility_billing.api.v1.account_enquiry", "utility_billing.api.v1.post_payment", "utility_billing.api.channels.bill_payment"):
			self.assertIn(f"/api/method/{p}", paths)
		post = paths["/api/method/utility_billing.api.v1.post_payment"]["post"]
		self.assertEqual(post["parameters"][0]["name"], "Idempotency-Key")
		self.assertEqual(post["x-scope"], "payment.write")
		self.assertEqual(paths["/api/method/utility_billing.api.channels.bill_payment"]["post"]["parameters"], [])
		self.assertEqual(spec["info"]["version"], v1.VERSION)
