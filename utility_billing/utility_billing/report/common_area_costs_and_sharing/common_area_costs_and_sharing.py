# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Common Area Costs and Sharing: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("common_area_allocation", filters)
