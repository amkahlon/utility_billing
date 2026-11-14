# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Adds the browser files' version to the page's boot information (see ui_version.py)."""

from utility_billing.ui_version import ui_version


def boot_session(bootinfo):
	bootinfo.ub_ui_version = ui_version()
