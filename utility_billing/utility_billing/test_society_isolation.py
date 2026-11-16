# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Society isolation (review 2026-11-16, finding 1): a user limited to some societies can't see or reach another society's data
through reports, pages, search or a request that names the other society (FS-ACC-01)."""

from types import SimpleNamespace

import frappe

from utility_billing import scope
from utility_billing.utility_billing import access, choices, consumer360, dashboard, mis
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER

OTHER = "TST-ISO"


class IntegrationTestSocietyIsolation(BillingCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("Society", OTHER):
			frappe.get_doc({"doctype": "Society", "society_name": "Isolation Test Society", "short_name": OTHER}).insert()
		self.consumer_name = frappe.db.get_value("Consumer", frappe.db.get_value("Connection", self.conn, "consumer"), "consumer_name")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		super().tearDown()

	def limit_to(self, society):
		frappe.set_user("Administrator")
		name = frappe.db.get_value("User Access", {"user": BILLING_MANAGER})
		ua = frappe.get_doc("User Access", name) if name else frappe.new_doc("User Access")
		ua.user, ua.all_societies = BILLING_MANAGER, 0
		ua.set("societies", [{"society": society}])
		ua.save()
		frappe.set_user(BILLING_MANAGER)

	def test_check_society(self):
		self.limit_to(SOC)
		access.check_society(SOC)
		access.check_society(None)
		with self.assertRaises(frappe.PermissionError):
			access.check_society(OTHER)
		with self.assertRaises(frappe.PermissionError):
			access.check_society([SOC, OTHER])
		self.assertEqual([s.name for s in access.visible_societies()], [SOC])

	def test_reports_accept_only_own_society(self):
		self.limit_to(SOC)
		self.assertEqual(mis.society_of(frappe._dict()), SOC)  # several societies exist; the user's own is the default
		self.assertEqual(mis.society_of(frappe._dict(society=SOC)), SOC)
		with self.assertRaises(frappe.PermissionError):
			mis.society_of(frappe._dict(society=OTHER))

	def test_dashboard_lists_and_opens_only_own_society(self):
		self.limit_to(SOC)
		d = dashboard.get_dashboard()
		self.assertEqual({s["name"] for s in d["societies"]}, {SOC})
		with self.assertRaises(frappe.PermissionError):
			dashboard.get_dashboard(society=OTHER)

	def test_consumer_search_and_view(self):
		consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		self.limit_to(SOC)
		self.assertTrue(any(r.account == self.conn for r in consumer360.search(self.consumer_name)))
		consumer360.view(consumer)
		self.limit_to(OTHER)
		self.assertFalse(any(r.account == self.conn for r in consumer360.search(self.consumer_name)))
		with self.assertRaises(frappe.PermissionError):
			consumer360.view(consumer)

	def test_choices_only_own_society(self):
		frappe.db.set_value("Property", self.prop, "block", "ISO-BLOCK")
		self.limit_to(OTHER)
		self.assertNotIn("ISO-BLOCK", choices.values("block"))
		with self.assertRaises(frappe.PermissionError):
			choices.values("block", SOC)
		self.limit_to(SOC)
		self.assertIn("ISO-BLOCK", choices.values("block"))

	def test_request_naming_another_society_is_refused(self):
		self.limit_to(SOC)
		for path, form in (("/api/method/utility_billing.utility_billing.dashboard.get_dashboard", {"society": OTHER}),
		                   ("/api/method/frappe.desk.query_report.run", {"report_name": "Billing Summary by Cycle",
		                                                                 "filters": frappe.as_json({"society": OTHER})})):
			frappe.local.request = SimpleNamespace(path=path)
			frappe.local.form_dict = frappe._dict(form)
			with self.assertRaises(frappe.PermissionError):
				scope.guard()
		# own society, and methods outside the app and reports, pass
		for path, form in (("/api/method/utility_billing.utility_billing.dashboard.get_dashboard", {"society": SOC}),
		                   ("/api/method/frappe.client.get_list", {"society": OTHER})):
			frappe.local.request = SimpleNamespace(path=path)
			frappe.local.form_dict = frappe._dict(form)
			scope.guard()

	def test_unlimited_user_sees_every_society(self):
		frappe.set_user(BILLING_MANAGER)
		if access.societies_of() is None:
			access.check_society(OTHER)
			self.assertTrue({SOC, OTHER} <= {s.name for s in access.visible_societies()})
