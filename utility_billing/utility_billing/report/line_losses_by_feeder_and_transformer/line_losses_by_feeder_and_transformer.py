# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Line Losses by Feeder and Transformer: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("line_losses", filters)
