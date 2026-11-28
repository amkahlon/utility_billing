# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Detection Bills: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("detection_bills", filters)
