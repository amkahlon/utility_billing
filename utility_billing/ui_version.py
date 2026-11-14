# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""A version number for the app's browser files (styles and scripts): the latest modification time among them. Browsers are told to
fetch a file again whenever this changes, so an update never needs anyone to clear a cache or raise a number by hand."""

import os

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")


def ui_version() -> str:
	times = [os.path.getmtime(os.path.join(folder, name)) for folder, _dirs, names in os.walk(_ROOT) for name in names
	         if name.endswith((".js", ".css", ".woff2", ".svg"))]
	return str(int(max(times))) if times else "1"
