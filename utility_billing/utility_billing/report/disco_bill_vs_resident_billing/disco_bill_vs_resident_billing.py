# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""DISCO Bill vs Resident Billing: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("disco_reconciliation", filters)
