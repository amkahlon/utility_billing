# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Branding history and signatories (FS-CLT-07, 09; gap 4): what a document printed on a given day carries.

- Branding: the latest Branding Version from on or before the day, falling back field by field to the Society's Logo and Branding.
- Signatory: the first signatory of the society for that kind of document whose dates cover the day, or None.
Bills keep both in their print snapshot, so a reprint shows what was issued."""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

KINDS = {"Bill": "for_bill", "Receipt": "for_receipt", "Notice": "for_notice", "Certificate": "for_certificate"}


def effective(society, on=None) -> dict:
	s = frappe.get_cached_doc("Society", society) if isinstance(society, str) else society
	on = getdate(on or nowdate())
	versions = sorted((v for v in s.get("branding_versions") or [] if v.effective_from and getdate(v.effective_from) <= on),
	                  key=lambda v: getdate(v.effective_from))
	v = versions[-1] if versions else None
	pick = lambda field: (v.get(field) if v and v.get(field) else None) or s.get(field)  # noqa: E731
	return {"logo": pick("logo"), "colour": pick("brand_colour") or "#1F5F8B", "letterhead": pick("letterhead_text"), "footer": pick("footer_text"),
	        "version_from": str(v.effective_from) if v else None}


def signatory(society, kind, on=None) -> dict | None:
	s = frappe.get_cached_doc("Society", society) if isinstance(society, str) else society
	on = getdate(on or nowdate())
	field = KINDS[kind]
	for r in s.get("signatories") or []:
		if r.get(field) and getdate(r.valid_from) <= on and (not r.valid_to or getdate(r.valid_to) >= on):
			return {"name": r.signatory_name, "designation": r.designation, "signature": r.signature}
	return None


def validate_society(doc):
	"""Called from Society.validate."""
	seen = set()
	for v in doc.get("branding_versions") or []:
		if str(v.effective_from) in seen:
			frappe.throw(_("Branding history row {0}: another version already starts on {1}.").format(v.idx, frappe.utils.formatdate(v.effective_from)))
		seen.add(str(v.effective_from))
		if not any(v.get(f) for f in ("logo", "brand_colour", "letterhead_text", "footer_text")):
			frappe.throw(_("Branding history row {0}: set at least a logo, colour, letterhead or footer.").format(v.idx))
	for r in doc.get("signatories") or []:
		if not any(r.get(f) for f in KINDS.values()):
			frappe.throw(_("Signatories row {0}: tick the documents {1} signs.").format(r.idx, r.signatory_name))
		if r.valid_to and getdate(r.valid_to) < getdate(r.valid_from):
			frappe.throw(_("Signatories row {0}: To is before From.").format(r.idx))
	for kind, field in KINDS.items():  # two signatories for the same document on the same day would be ambiguous
		rows = [r for r in doc.get("signatories") or [] if r.get(field)]
		for i, a in enumerate(rows):
			for b in rows[i + 1:]:
				a_end, b_end = getdate(a.valid_to or "2999-12-31"), getdate(b.valid_to or "2999-12-31")
				if getdate(a.valid_from) <= b_end and getdate(b.valid_from) <= a_end:
					frappe.throw(_("Signatories: {0} and {1} both sign {2} on overlapping dates. End one before the other starts.").format(
						a.signatory_name, b.signatory_name, _(kind.lower() + "s")))
