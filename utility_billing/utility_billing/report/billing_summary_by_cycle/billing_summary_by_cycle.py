# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Billing Summary by Cycle: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("billing_summary", filters)
