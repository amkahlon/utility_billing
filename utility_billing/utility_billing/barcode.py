# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Code 128 barcodes for the bill (gap 12): bank counters scan the reference number instead of typing it.

Code set C (digit pairs) for an even run of digits, like the 10-digit reference number, else code set B. Drawn as a PNG with Pillow
and embedded in the bill as a data URI, so the PDF engine needs nothing else. The encoding is the standard one (ISO/IEC 15417):
start symbol, data symbols, a modulo-103 check symbol and the stop pattern; `widths` returns the bar and space widths in modules."""

import base64
import io

# bar/space widths (modules) of the 107 symbols, value 0 to 106; 103/104/105 start A/B/C, 106 stop
PATTERNS = (
	"212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 122231 113222 123122 123221 "
	"223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 212123 212321 232121 111323 131123 131321 "
	"112313 132113 132311 211313 231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 213311 213131 "
	"311123 311321 331121 312113 312311 332111 314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 112412 122114 "
	"122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
	"214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 114131 311141 411131 211412 211214 211232 2331112"
).split()
START_B, START_C, STOP = 104, 105, 106


def symbols(text: str) -> list[int]:
	"""The symbol values for `text`, start and check symbol included, stop excluded."""
	text = str(text)
	if not text or any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
		raise ValueError("Code 128 here takes printable ASCII text.")
	if text.isdigit() and len(text) % 2 == 0:
		values = [START_C] + [int(text[i:i + 2]) for i in range(0, len(text), 2)]
	else:
		values = [START_B] + [ord(ch) - 32 for ch in text]
	check = (values[0] + sum(v * i for i, v in enumerate(values[1:], start=1))) % 103
	return values + [check]


def widths(text: str) -> list[int]:
	"""Bar and space widths in modules (bar first), with the stop pattern."""
	out = []
	for v in symbols(text) + [STOP]:
		out += [int(c) for c in PATTERNS[v]]
	return out


def png_data_uri(text: str, module_px=3, height_px=90, quiet_modules=10) -> str:
	from PIL import Image, ImageDraw  # noqa: PLC0415

	w = widths(text)
	total = (sum(w) + 2 * quiet_modules) * module_px
	img = Image.new("1", (total, height_px), 1)
	draw = ImageDraw.Draw(img)
	x = quiet_modules * module_px
	for i, mod in enumerate(w):
		if i % 2 == 0:  # bars at even positions
			draw.rectangle([x, 0, x + mod * module_px - 1, height_px - 1], fill=0)
		x += mod * module_px
	buf = io.BytesIO()
	img.save(buf, format="PNG")
	return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
