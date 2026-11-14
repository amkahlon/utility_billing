# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Refunds Register: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("refunds_register", filters)
