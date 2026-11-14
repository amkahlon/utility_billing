# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Collection vs Billing by Category and Block: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("collection_vs_billing", filters)
