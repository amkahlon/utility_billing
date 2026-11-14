# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Highest-Loss Feeders and Loss Trend: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("loss_ranking", filters)
