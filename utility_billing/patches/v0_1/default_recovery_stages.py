# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Give societies that have no recovery stages the default four (reminder 15, notice 30, final notice 45, disconnection order 60 days)."""

import frappe

from utility_billing.utility_billing.recovery import DEFAULT_STAGES


def execute():
	for name in frappe.get_all("Society", pluck="name"):
		society = frappe.get_doc("Society", name)
		if society.recovery_stages:
			continue
		for stage, days, channel, message in DEFAULT_STAGES:
			society.append("recovery_stages", {"stage": stage, "days_overdue": days, "channel": channel, "message": message})
		society.flags.ignore_permissions = True
		society.save()
