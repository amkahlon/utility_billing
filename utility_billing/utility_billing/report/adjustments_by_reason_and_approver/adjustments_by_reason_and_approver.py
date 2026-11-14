# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Adjustments by Reason and Approver: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("adjustments_register", filters)
