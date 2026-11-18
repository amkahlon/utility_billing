# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Net Metering Register: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("net_metering_register", filters)
