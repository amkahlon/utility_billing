# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Message Cost by Channel: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("message_cost", filters)
