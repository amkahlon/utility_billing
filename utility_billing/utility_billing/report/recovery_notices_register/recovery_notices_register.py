# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Recovery Notices Register: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("recovery_notices", filters)
