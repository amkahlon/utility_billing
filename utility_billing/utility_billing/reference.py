# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Consumer reference numbers (FS-MST-05).

Format: 10 digits = 1-digit utility code + 8-digit serial + 1 Luhn check digit.
Example: utility 1 (electricity), serial 1 -> "100000001" + check digit 6 -> "1000000016".
The check digit lets banks, 1Link and wallets reject a mistyped number before it is posted.
"""

SERIAL_DIGITS = 8


def luhn_check_digit(digits: str) -> int:
	"""Luhn (mod 10) check digit for a string of digits."""
	if not digits.isdigit():
		raise ValueError("Only digits are allowed.")
	total = 0
	# Double every second digit from the right, starting with the rightmost payload digit.
	for i, ch in enumerate(reversed(digits)):
		d = int(ch)
		if i % 2 == 0:
			d *= 2
			if d > 9:
				d -= 9
		total += d
	return (10 - total % 10) % 10


def make_reference(utility_code: int, serial: int) -> str:
	if not 1 <= int(utility_code) <= 9:
		raise ValueError("Utility code must be a single digit from 1 to 9.")
	if not 1 <= int(serial) < 10**SERIAL_DIGITS:
		raise ValueError(f"Serial must be between 1 and {10**SERIAL_DIGITS - 1}.")
	payload = f"{int(utility_code)}{int(serial):0{SERIAL_DIGITS}d}"
	return payload + str(luhn_check_digit(payload))


def is_valid_reference(ref: str) -> bool:
	ref = (ref or "").strip()
	if len(ref) != 1 + SERIAL_DIGITS + 1 or not ref.isdigit() or ref[0] == "0":
		return False
	return luhn_check_digit(ref[:-1]) == int(ref[-1])
