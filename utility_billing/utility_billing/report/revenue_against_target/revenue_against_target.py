# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Revenue against Target: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("revenue_vs_target", filters)
