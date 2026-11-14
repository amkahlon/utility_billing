# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Approval notifications are cleared when the document is acted on (user request, 2026-11-14): approvers' "waiting
for your approval" notifications become read when someone approves, rejects or withdraws, the notification about the
new state stays unread until it is opened, and the affected users' browsers are told to refresh their counter."""

from unittest.mock import patch

import frappe

from utility_billing.utility_billing.approval import set_rejection_reason
from utility_billing.utility_billing.test_billing import BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, BM_AND_GM, GENERAL_MANAGER, act

APPROVERS = (GENERAL_MANAGER, BM_AND_GM)  # both hold the General Manager role


class IntegrationTestNotificationsCleared(BillingCase):
	def adjustment(self):
		adj = self.fpa("2026-12-01", 2, approve_it=False)
		act(adj, "Submit for Approval", BILLING_MANAGER)
		return adj

	def unread(self, user, doc):
		return frappe.db.count("Notification Log", {"for_user": user, "document_name": doc.name, "read": 0})

	def total_unread(self, user):
		return frappe.db.count("Notification Log", {"for_user": user, "read": 0})

	def test_approving_clears_the_approvers_notifications_but_not_the_makers(self):
		adj = self.adjustment()
		self.assertEqual([self.unread(u, adj) for u in APPROVERS], [1, 1])
		before = self.total_unread(GENERAL_MANAGER)
		act(adj, "Approve", GENERAL_MANAGER)
		self.assertEqual([self.unread(u, adj) for u in APPROVERS], [0, 0])  # also for the approver who didn't act
		self.assertEqual(self.total_unread(GENERAL_MANAGER), before - 1)  # the counter goes down
		self.assertEqual(self.unread(BILLING_MANAGER, adj), 1)  # "was approved" waits for the maker to open it

	def test_withdrawing_clears_and_resubmitting_notifies_once(self):
		adj = self.adjustment()
		act(adj, "Withdraw", BILLING_MANAGER)
		self.assertEqual([self.unread(u, adj) for u in APPROVERS], [0, 0])
		act(adj, "Submit for Approval", BILLING_MANAGER)
		self.assertEqual([self.unread(u, adj) for u in APPROVERS], [1, 1])  # fresh, not stacked on the old one

	def test_rejecting_clears_waiting_and_resubmitting_clears_the_rejection(self):
		adj = self.adjustment()
		frappe.set_user(GENERAL_MANAGER)
		set_rejection_reason(adj.doctype, adj.name, "Rate is wrong")
		frappe.set_user("Administrator")
		act(adj, "Reject", GENERAL_MANAGER)
		self.assertEqual([self.unread(u, adj) for u in APPROVERS], [0, 0])
		self.assertEqual(self.unread(BILLING_MANAGER, adj), 1)  # "was rejected: Rate is wrong"
		act(adj, "Submit for Approval", BILLING_MANAGER)  # the maker fixed it and resubmitted
		self.assertEqual(self.unread(BILLING_MANAGER, adj), 0)  # the rejection has been dealt with
		self.assertEqual([self.unread(u, adj) for u in APPROVERS], [1, 1])

	def test_other_documents_notifications_are_left_alone(self):
		first = self.adjustment()
		second = self.fpa("2027-01-01", 3, approve_it=False)
		act(second, "Submit for Approval", BILLING_MANAGER)
		act(first, "Approve", GENERAL_MANAGER)
		self.assertEqual(self.unread(GENERAL_MANAGER, first), 0)
		self.assertEqual(self.unread(GENERAL_MANAGER, second), 1)

	def test_affected_browsers_are_told_to_refresh(self):
		adj = self.adjustment()
		with patch("frappe.publish_realtime") as publish:
			act(adj, "Approve", GENERAL_MANAGER)
		told = {c.kwargs["user"] for c in publish.call_args_list if c.args and c.args[0] == "ub_notifications_changed"}
		self.assertTrue(set(APPROVERS) <= told, told)  # every approver (a site may have more of them) is told
		self.assertNotIn(BILLING_MANAGER, told)  # the maker's own notification isn't touched yet
		with patch("frappe.publish_realtime") as publish:  # nothing unread about it any more: nobody is disturbed
			act(adj, "Cancel", GENERAL_MANAGER)
		told = {c.kwargs["user"] for c in publish.call_args_list if c.args and c.args[0] == "ub_notifications_changed"}
		self.assertEqual(told, {BILLING_MANAGER})  # only the maker's "was approved" is still unread
