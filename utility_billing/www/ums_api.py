# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""/ums-api: the billing API documentation for the other teams (FS-API-08), generated from the same OpenAPI document."""

import json

from utility_billing.api import v1

no_cache = 1


def get_context(context):
	spec = v1.openapi()
	ops = []
	for path, methods in spec["paths"].items():
		for verb, op in methods.items():
			body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema", {})
			params = [{"name": p["name"], "where": p["in"], "required": p.get("required"), "description": p.get("description", ""),
			           "type": p["schema"]["type"]} for p in op.get("parameters", [])]
			params += [{"name": n, "where": "body", "required": n in body.get("required", []), "description": s.get("description", ""), "type": s["type"]}
			           for n, s in body.get("properties", {}).items()]
			example = op["responses"]["200"]["content"]["application/json"]["example"]["message"]
			ops.append({"verb": verb.upper(), "path": path, "summary": op["summary"], "scope": op["x-scope"], "tag": op["tags"][0],
			            "params": params, "example": json.dumps(example, indent=2, ensure_ascii=False)})
	context.info = spec["info"]
	context.groups = [(tag, [o for o in ops if o["tag"] == tag]) for tag in ("Billing v1", "Channels")]
	context.title = "Billing API"
	return context
