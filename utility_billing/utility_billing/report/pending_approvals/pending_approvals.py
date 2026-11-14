# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Pending Approvals: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("pending_approvals", filters)
