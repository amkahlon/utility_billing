# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""What every API call goes through (FS-API-07, 10): the calling system's API Client (found from the signed-in integration user),
its scopes, societies, allowed IP addresses and rate limit; a log line per call (no personal data); and, for writes, an
idempotency key so a retried call returns the first answer instead of posting twice. A write is one database transaction: it
fully succeeds or leaves nothing behind."""

import functools
import hashlib
import json
import time

import frappe
from frappe import _
from frappe.utils import cint, cstr

SCOPES = {"account.read": "scope_account_read", "bill.read": "scope_bill_read", "payment.write": "scope_payment_write",
          "payment.reverse": "scope_payment_reverse", "events": "scope_events",
          "reading.write": "scope_reading_write"}

# Every method the API offers, for the OpenAPI document (v1.openapi) and the docs page: name -> spec.
REGISTRY: dict[str, dict] = {}


class RateLimited(frappe.ValidationError):
	http_status_code = 429


class KeyReused(frappe.ValidationError):
	http_status_code = 409


def current_client():
	"""The enabled API Client of the signed-in user, or a PermissionError."""
	name = frappe.db.get_value("API Client", {"user": frappe.session.user, "enabled": 1})
	if not name:
		raise frappe.PermissionError(_("This user isn't an enabled API client."))
	return frappe.get_cached_doc("API Client", name)


def societies_of(client) -> set | None:
	"""The societies the client may see, or None for all."""
	return None if cint(client.all_societies) else {r.society for r in client.societies}


def check_society(client, society):
	allowed = societies_of(client)
	if allowed is not None and society not in allowed:
		raise frappe.PermissionError(_("This client may not see that society's accounts."))


def _ip_allowed(client):
	allowed = [x.strip() for x in cstr(client.ip_allow_list).splitlines() if x.strip()]
	return not allowed or (frappe.local.request_ip or "") in allowed


def _rate_ok(client):
	limit = cint(client.rate_limit) or 60
	key = f"ums_api_rate|{frappe.local.site}|{client.name}|{int(time.time() // 60)}"
	count = frappe.cache.incr(key)
	if count == 1:
		frappe.cache.expire(key, 70)
	return count <= limit


def _log(client, endpoint, status, started, key=None, error=None, response_code=None):
	try:
		frappe.get_doc({"doctype": "API Call Log", "client": client, "endpoint": endpoint, "status_code": status,
		                "duration_ms": round((time.time() - started) * 1000), "idempotency_key": key, "response_code": response_code,
		                "ip_address": frappe.local.request_ip if getattr(frappe.local, "request_ip", None) else None,
		                "error": (error or "")[:500] or None}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="API call log failed")


def _request_hash(kwargs):
	return hashlib.sha256(json.dumps(kwargs, sort_keys=True, default=str).encode()).hexdigest()


def endpoint(scope, write=False, summary="", params=(), returns=None, name=None, response_code=None, post_only=False):
	"""Make a function an API method: whitelisted (POST for writes), guarded, logged and, for writes, idempotent.
	`params` is [(name, type, required, description)] for the OpenAPI document; `response_code(result)` gives a channel's
	response code for the log."""

	def wrap(fn):
		method = name or fn.__name__
		REGISTRY[f"{fn.__module__}.{fn.__name__}"] = {"name": method, "scope": scope, "write": write or post_only, "summary": summary,
		                                             "idempotent": write,
		                                             "params": list(params), "returns": returns, "path": f"{fn.__module__}.{fn.__name__}"}

		@functools.wraps(fn)
		def guarded(**kwargs):
			started = time.time()
			kwargs.pop("cmd", None)
			client = None
			key = None
			try:
				client = current_client()
				if not _ip_allowed(client):
					raise frappe.PermissionError(_("Calls from this address aren't allowed for {0}.").format(client.name))
				if not _rate_ok(client):
					raise RateLimited(_("Too many calls: {0} a minute are allowed.").format(cint(client.rate_limit) or 60))
				if scope and not cint(client.get(SCOPES[scope])):
					raise frappe.PermissionError(_("{0} doesn't have the {1} scope.").format(client.name, scope))
				if write:
					key = (frappe.get_request_header("Idempotency-Key") if getattr(frappe.local, "request", None) else None) or kwargs.pop("idempotency_key", None)
					kwargs.pop("idempotency_key", None)
					if not key:
						frappe.throw(_("Send an Idempotency-Key header with every write, so a retry can't post twice."))
					earlier = frappe.db.get_value("API Idempotency Key", {"unique_key": f"{client.name}|{key}"}, ["request_hash", "response", "endpoint"], as_dict=True)
					if earlier:
						if earlier.request_hash != _request_hash(kwargs) or earlier.endpoint != method:
							raise KeyReused(_("Idempotency key {0} was already used for a different request.").format(key))
						result = json.loads(earlier.response)
						_log(client.name, method, 200, started, key, error="replayed")
						return result
				result = fn(client, **kwargs)
				if write:
					frappe.get_doc({"doctype": "API Idempotency Key", "client": client.name, "idempotency_key": key, "unique_key": f"{client.name}|{key}",
					                "endpoint": method, "request_hash": _request_hash(kwargs), "status_code": 200,
					                "response": json.dumps(result, default=str)}).insert(ignore_permissions=True)
				_log(client.name, method, 200, started, key, response_code=response_code(result) if response_code else None)
				return result
			except Exception as e:
				status = getattr(e, "http_status_code", 500)
				in_request = getattr(frappe.local, "request", None) is not None and not frappe.flags.in_test
				if in_request:
					frappe.db.rollback()  # the write leaves nothing behind (FS-API-10); the log line is kept
				_log(client.name if client else None, method, status, started, key, error=f"{type(e).__name__}: {e}")
				if in_request:
					frappe.db.commit()
				raise

		return frappe.whitelist(methods=["POST"] if write or post_only else ["GET", "POST"])(guarded)

	return wrap
