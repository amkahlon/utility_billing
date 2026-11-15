# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 24 part B: AI analytics (insights, ask your data, theft suspicion explained) - off by default, explainable, logged, limited.
The Claude API is never called here: a fake client returns scripted responses, so these tests check what is sent and what is done
with the answer. House N 2308 (spec test case 1) owes Rs 1,104 after September."""

import json
from types import SimpleNamespace as NS
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import add_months, getdate

from utility_billing.utility_billing import ai, anomaly
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, CASHIER


def text(t, stop="end_turn"):
	return NS(content=[NS(type="text", text=t)], stop_reason=stop, model="claude-opus-5-5", usage=NS(input_tokens=1000, output_tokens=200))


def tool_call(report, filters):
	return NS(content=[NS(type="tool_use", id="tu1", name="run_report", input={"report": report, "filters": filters})], stop_reason="tool_use",
	          model="claude-opus-5-5", usage=NS(input_tokens=2000, output_tokens=100))


class FakeClient:
	def __init__(self, *responses):
		self.responses, self.sent = list(responses), []
		self.beta = NS(messages=NS(create=self.create))

	def create(self, **kw):
		self.sent.append(json.loads(json.dumps(kw, default=lambda o: getattr(o, "__dict__", str(o)))))
		return self.responses.pop(0)


class UnitTestTheftRules(UnitTestCase):
	def hist(self, units, statuses=None):
		start = getdate("2026-01-01")
		return [(add_months(start, i), u, (statuses or {}).get(i, "Valid")) for i, u in enumerate(units)]

	def test_each_rule(self):
		self.assertEqual(anomaly.reasons_for(self.hist([300] * 9 + [90, 80, 100]))[0], 30)  # a fall to under half
		self.assertEqual(anomaly.reasons_for(self.hist([300] * 9 + [0, 0, 0]))[0], 55)  # the fall, and stuck at zero
		self.assertEqual(anomaly.reasons_for(self.hist([200, 210, 150, 150, 150, 150]))[0], 20)  # flat
		self.assertEqual(anomaly.reasons_for(self.hist([200] * 12), peer_median=800)[0], 45)  # flat and far below similar properties
		score, why = anomaly.reasons_for(self.hist([210, 190, 205, 220, 180, 200], {3: "Estimated", 4: "Estimated", 5: "Estimated"}))
		self.assertEqual((score, len(why)), (15, 1))
		self.assertEqual(anomaly.reasons_for(self.hist([210, 190, 205, 220, 180, 200]))[0], 0)  # an ordinary account


class IntegrationTestAI(BillingCase):
	def setUp(self):
		super().setUp()
		frappe.db.delete("AI Settings", {"society": SOC})
		frappe.cache.delete_keys("ub-insights:")  # the cache isn't rolled back between tests

	def switch_on(self, **kw):
		s = frappe.get_doc({"doctype": "AI Settings", "society": SOC, "enable_insights": 1, "enable_ask": 1, "enable_anomaly": 1,
		                    "model": "claude-opus-5-5", "monthly_call_limit": 100, **kw})
		s.api_key = "sk-ant-test-not-a-real-key"
		s.insert()
		return s

	def logs(self, feature):
		return frappe.get_all("AI Log", filters={"society": SOC, "feature": feature}, fields=["status", "question", "answer", "reports_used"],
		                      order_by="creation")

	def test_off_by_default_nothing_is_sent(self):
		self.september_posted_with_arrears()
		fake = FakeClient()
		with patch.object(ai, "client_for", return_value=fake):
			r = ai.insights(SOC, "2026-09-01")
			self.assertTrue(r["facts"])
			self.assertEqual((r["ai"], r["summary"]), (False, None))
			self.assertRaises(frappe.ValidationError, ai.ask, SOC, "What is owed in total?")
		self.assertEqual(fake.sent, [])

	def test_insights_are_written_from_the_numbered_facts_only(self):
		self.september_posted_with_arrears()
		self.switch_on()
		fake = FakeClient(text("Collection was strong in September [2]."))
		with patch.object(ai, "client_for", return_value=fake):
			r = ai.insights(SOC, "2026-09-01")
			self.assertEqual(r["summary"], "Collection was strong in September [2].")
			ai.insights(SOC, "2026-09-01")  # the same facts: kept, not asked again
		self.assertEqual(len(fake.sent), 1)
		sent = fake.sent[0]
		self.assertIn("only the numbered facts", sent["system"])
		self.assertIn("[1] ", sent["messages"][0]["content"])
		self.assertEqual((sent["fallbacks"], sent["output_config"]), ("default", {"effort": "low"}))
		(log,) = self.logs("Insights")
		self.assertEqual(log.status, "Answered")

	def test_ask_runs_a_report_with_the_users_rights_and_keeps_names_out(self):
		self.september_posted_with_arrears()
		self.switch_on(send_names=0)
		fake = FakeClient(tool_call("Defaulters", {"as_of": "2026-12-31"}), text("One account owes Rs 1,104."))
		frappe.set_user(BILLING_MANAGER)
		try:
			with patch.object(ai, "client_for", return_value=fake):
				r = ai.ask(SOC, "Who owes us money?")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual((r["answer"], [u["report"] for u in r["reports"]]), ("One account owes Rs 1,104.", ["Defaulters"]))
		tool_result = fake.sent[1]["messages"][-1]["content"][0]
		read = json.loads(tool_result["content"])
		self.assertEqual(read["filters"]["society"], SOC)  # always the asker's society
		self.assertNotIn("House N 2308 Owner", tool_result["content"])  # no names unless the society allows them
		(log,) = self.logs("Ask")
		self.assertIn("Defaulters", log.reports_used)

	def test_a_report_the_user_cant_open_is_refused_to_the_model(self):
		self.switch_on()
		fake = FakeClient(tool_call("Management Dashboard", {}), text("I can't see that report."))
		frappe.set_user(CASHIER)
		try:
			with patch.object(ai, "client_for", return_value=fake):
				r = ai.ask(SOC, "How are we doing this month?")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(r["reports"], [])
		result = fake.sent[1]["messages"][-1]["content"][0]
		self.assertTrue(result["is_error"])
		self.assertNotIn("Management Dashboard", [t for t in fake.sent[0]["tools"][0]["input_schema"]["properties"]["report"]["enum"]])

	def test_refusals_and_the_monthly_limit_are_logged(self):
		self.september_posted_with_arrears()
		self.switch_on(monthly_call_limit=1)
		with patch.object(ai, "client_for", return_value=FakeClient(text("", stop="refusal"))):
			r = ai.insights(SOC, "2026-09-01")
		self.assertIn("declined", r["error"])
		with patch.object(ai, "client_for", return_value=FakeClient(text("x"))):
			self.assertRaises(frappe.ValidationError, ai.ask, SOC, "What is owed in total?")
		self.assertEqual([x.status for x in self.logs("Insights") + self.logs("Ask")], ["Refused", "Limit reached"])

	def test_a_score_is_explained_from_its_reasons(self):
		self.switch_on()
		fake = FakeClient(text("Units fell sharply; check the meter seal."))
		row = {"connection": self.conn, "category": self.cat, "score": 55, "reasons": ["No units for the last 3 months while active"], "history": "300, 0, 0, 0"}
		with patch.object(ai, "client_for", return_value=fake), patch.object(anomaly, "score_account", return_value=row):
			r = ai.explain_account(SOC, self.conn)
		self.assertEqual(r["explanation"], "Units fell sharply; check the meter seal.")
		self.assertIn("not proof", fake.sent[0]["system"])
		self.assertIn("No units for the last 3 months", fake.sent[0]["messages"][0]["content"])
		self.assertRaises(frappe.ValidationError, ai.explain_account, "Some Other Society", self.conn)
