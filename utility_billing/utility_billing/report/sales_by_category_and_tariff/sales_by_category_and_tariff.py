# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Sales by Category and Tariff: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("sales", filters)
