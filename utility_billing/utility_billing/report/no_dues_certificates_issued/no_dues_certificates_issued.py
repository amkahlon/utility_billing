# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""No-Dues Certificates Issued: see mis.py."""

from utility_billing.utility_billing import mis


def execute(filters=None):
	return mis.run("nodues_certificates", filters)
