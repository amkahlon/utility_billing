# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""FPA and QTA Register: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("fpa_qta_register", filters)
