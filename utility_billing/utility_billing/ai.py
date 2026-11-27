# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""AI analytics through the Claude API (increment 24; notes/ai-analytics-roadmap.md). User decisions 2026-11-16: insights summary, ask your
data and explanations for theft suspicion first; the Claude API, switched on per society with the society's own key.

Principles kept here:
- **Off by default.** Each feature is off until a society's AI Settings switch it on; with no key, nothing is sent anywhere.
- **Explainable.** The model only words what the system has already worked out: insights come from numbered facts the rules produced,
  answers from the existing reports run with the asking user's own rights. Every figure shown links back to its report.
- **Minimal data.** Account numbers, not names, phone or ID numbers, unless the society allows names; the model never changes data.
- **Logged and limited.** Every call is an AI Log row (who, what, tokens, cost); a monthly call limit per society."""

import hashlib
import json
import time

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate
from utility_billing.utility_billing import rights

FEATURES = {"Insights": "enable_insights", "Ask": "enable_ask", "Anomaly": "enable_anomaly"}
PRICE = {"claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10), "claude-haiku-5-5": (0.10, 0.50), "claude-fable-5-2": (8, 40)}  # USD per 1M tokens
PRIVATE_FIELDS = {"consumer_name", "owner_name", "mobile_no", "phone", "email", "id_number", "cnic", "address", "assigned_to"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AIError(frappe.ValidationError):
	pass


def settings_for(society):
	name = frappe.db.get_value("AI Settings", {"society": society})
	return frappe.get_doc("AI Settings", name) if name else None


def is_on(society, feature) -> bool:
	s = settings_for(society)
	return bool(s and cint(s.get(FEATURES[feature])) and s.api_key)


def month_usage(society):
	start = getdate(nowdate()).replace(day=1)
	row = frappe.db.sql("""select count(*), ifnull(sum(cost_usd), 0) from `tabAI Log` where society = %s and creation >= %s
	                       and status != 'Limit reached'""", (society, start))[0]
	return cint(row[0]), flt(row[1])


def log(society, feature, status, **kw):
	doc = frappe.get_doc({"doctype": "AI Log", "society": society, "feature": feature, "status": status, "user": frappe.session.user,
	                      **{k: (v[:5000] if isinstance(v, str) else v) for k, v in kw.items()}})
	doc.insert(ignore_permissions=True)
	calls, cost = month_usage(society)
	s = settings_for(society)
	if s:
		frappe.db.set_value("AI Settings", s.name, {"calls_this_month": calls, "cost_this_month": round(cost, 2)}, update_modified=False)
	return doc


def client_for(s):
	import anthropic  # noqa: PLC0415

	return anthropic.Anthropic(api_key=s.get_password("api_key"), timeout=90.0, max_retries=2)


def call(society, feature, system, messages, tools=None, max_tokens=4000, question=None):
	"""One request to Claude for a society, within its settings and limit; returns the response. Logs every outcome."""
	import anthropic  # noqa: PLC0415

	s = settings_for(society)
	if not (s and cint(s.get(FEATURES[feature]))):
		raise AIError(_("{0} isn't switched on for this society (AI Settings).").format(_(feature)))
	if not s.api_key:
		raise AIError(_("Enter the society's Anthropic API key in AI Settings first."))
	calls, _cost = month_usage(society)
	if cint(s.monthly_call_limit) and calls >= cint(s.monthly_call_limit):
		log(society, feature, "Limit reached", question=question)
		raise AIError(_("This month's limit of {0} AI calls is used up.").format(cint(s.monthly_call_limit)))
	kwargs = {"model": s.model or "claude-opus-5-5", "max_tokens": max_tokens, "system": system, "messages": messages,
	          "output_config": {"effort": "low"}, "betas": [FALLBACK_BETA], "fallbacks": "default"}
	if tools:
		kwargs["tools"] = tools
	started = time.time()
	try:
		response = client_for(s).beta.messages.create(**kwargs)
	except anthropic.AuthenticationError:
		log(society, feature, "Failed", question=question, model=kwargs["model"], error="The API key was refused.")
		raise AIError(_("The Anthropic API key was refused; check it in AI Settings.")) from None
	except anthropic.RateLimitError:
		log(society, feature, "Failed", question=question, model=kwargs["model"], error="Rate limited")
		raise AIError(_("The AI service is busy; try again in a minute.")) from None
	except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
		log(society, feature, "Failed", question=question, model=kwargs["model"], error=str(e)[:500])
		raise AIError(_("The AI service couldn't be reached: {0}").format(str(e)[:200])) from None
	inp, out = cint(response.usage.input_tokens), cint(response.usage.output_tokens)
	price = PRICE.get(response.model or kwargs["model"], PRICE["claude-opus-5-5"])
	meta = {"model": response.model, "input_tokens": inp, "output_tokens": out, "seconds": round(time.time() - started, 1),
	                    "cost_usd": round((inp * price[0] + out * price[1]) / 1_000_000, 4)}
	if response.stop_reason == "refusal":
		log(society, feature, "Refused", question=question, **meta)
		raise AIError(_("The AI declined this request."))
	return response, meta


def text_of(response) -> str:
	return "\n".join(b.text for b in response.content if b.type == "text").strip()


def private(row: dict, allow_names: bool) -> dict:
	return row if allow_names else {k: v for k, v in row.items() if k not in PRIVATE_FIELDS}


# ---------------------------------------------------------------- 1. insights summary
def facts(society, month) -> list[dict]:
	"""What changed this month, as numbered facts worked out by rules from the dashboard's own figures, each with where it comes from."""
	from utility_billing.utility_billing import dashboard, mis  # noqa: PLC0415

	# the dashboard's own figures, kept for a while (increment 61: worked out a second time here, 13.6 s on 21,446 accounts)
	figures = dashboard.figures_for(society, mis.month_start(month))
	out = []
	for k in figures["kpis"]:
		if k["value"] not in ("—", None):
			out.append({"text": f"{k['label']}: {k['value']} ({k['note']})", "route": ["ub-dashboard"]})
	for a in figures["attention"][:6]:
		out.append({"text": f"{a['title']} — {a['detail']}", "route": a["route"], "options": a.get("options") or {}})
	for i, f in enumerate(out, start=1):
		f["n"] = i
	return out


SUMMARY_SYSTEM = (
	"You write the monthly summary that a utility billing society's managers read on their dashboard. Use only the numbered facts you "
	"are given; never add a figure, cause or comparison that isn't in them. Write three to five short, plain sentences, most important "
	"first, and put the fact numbers you used in square brackets after each sentence, like [2][5]. If the facts show nothing notable, "
	"say so in one sentence.")


@frappe.whitelist()
def insights(society: str, month: str | None = None):
	"""The dashboard's Insights card: the facts always; a written summary when the society has switched it on."""
	from utility_billing.utility_billing import dashboard  # noqa: PLC0415

	if not rights.can("dashboard.view"):
		raise frappe.PermissionError(_("You don't have access to the dashboard."))
	month = month or dashboard.latest_billing_month(society)
	found = facts(society, month)
	result = {"facts": found, "summary": None, "ai": is_on(society, "Insights"), "month": str(month)}
	if not result["ai"] or not found:
		return result
	key = f"ub-insights:{society}:{month}:{hashlib.md5(json.dumps([f["text"] for f in found]).encode()).hexdigest()}"
	cached = frappe.cache.get_value(key)
	if cached:
		return {**result, "summary": cached}
	listing = "\n".join(f"[{f['n']}] {f['text']}" for f in found)
	try:
		r, meta = call(society, "Insights", SUMMARY_SYSTEM, [{"role": "user", "content": f"Facts for {getdate(month):%B %Y}:\n{listing}"}],
		         max_tokens=1500, question=f"Insights {getdate(month):%B %Y}")
	except AIError as e:
		return {**result, "error": str(e)}
	summary = text_of(r)
	log(society, "Insights", "Answered", question=f"Insights {getdate(month):%B %Y}", answer=summary, **meta)
	frappe.cache.set_value(key, summary, expires_in_sec=6 * 3600)
	return {**result, "summary": summary}


# ---------------------------------------------------------------- 2. ask your data
ASK_SYSTEM = (
	"You answer questions from the managers of a utility billing society (electricity, water, gas, maintenance) about their own data. "
	"You can't see the data directly: call run_report to run one of the society's reports, then answer only from the rows it returns. "
	"Never state a figure that no report returned. If no report fits the question, say which reports exist that come closest. Keep "
	"answers short: a sentence or two, then the key figures. Dates are YYYY-MM-DD; a month filter is the first day of the month. Money is "
	"in the society's currency (Rs).")


def report_catalogue(roles) -> list[dict]:
	from utility_billing.utility_billing import mis  # noqa: PLC0415

	out = []
	for key, spec in mis.REPORTS.items():
		if not (set(spec["roles"]) & roles or "System Manager" in roles or frappe.session.user == "Administrator"):
			continue
		filters = [f"{x['fieldname']} ({x.get('fieldtype', 'Data')}{': ' + x['options'].strip() if x.get('fieldtype') == 'Select' else ''})"
		           for x in spec["filters"] if x["fieldname"] != "society"]
		out.append({"key": key, "name": spec["name"], "description": spec.get("description") or "", "filters": filters})
	return out


def run_tool(society, catalogue, args, allow_names) -> tuple[dict, dict]:
	"""(what the model reads, what the page shows) for one run_report call."""
	from utility_billing.utility_billing import mis  # noqa: PLC0415

	by_name = {c["name"]: c for c in catalogue}
	spec = by_name.get((args or {}).get("report"))
	if not spec:
		return {"error": "Unknown report or not allowed for this user. Use one of: " + ", ".join(by_name)}, None
	filters = {k: str(v) for k, v in ((args or {}).get("filters") or {}).items() if isinstance(k, str)}
	filters["society"] = society  # always the asking user's society
	try:
		columns, data, _msg, _chart, summary = mis.run(spec["key"], filters)
	except Exception as e:  # noqa: BLE001
		return {"error": f"The report couldn't run with those filters: {str(e)[:200]}"}, None
	rows = [private({k: v for k, v in r.items() if not k.startswith("_")}, allow_names) for r in (data or []) if isinstance(r, dict)]
	labels = {c.get("fieldname"): c.get("label") for c in columns or [] if isinstance(c, dict)}
	read = {"report": spec["name"], "filters": filters, "columns": {k: v for k, v in labels.items() if allow_names or k not in PRIVATE_FIELDS},
	        "row_count": len(rows), "rows": rows[:30], "summary": [{"label": s.get("label"), "value": s.get("value")} for s in (summary or [])]}
	shown = {"report": spec["name"], "filters": filters, "rows": len(rows)}
	return json.loads(json.dumps(read, default=str)), shown


@frappe.whitelist(methods=["POST"])
def ask(society: str, question: str):
	"""Ask your data: Claude picks a report and its filters; the report runs with the asking user's rights; the answer quotes its rows."""
	question = (question or "").strip()
	if len(question) < 5:
		frappe.throw(_("Ask a question in a few words."))
	s = settings_for(society)
	catalogue = report_catalogue(set(frappe.get_roles()))
	if not catalogue:
		raise frappe.PermissionError(_("You can't run any reports."))
	tools = [{"name": "run_report", "description": "Run one of the society's reports and get its rows. Reports:\n" + "\n".join(
		f"- {c['name']}: {c['description']} Filters: {', '.join(c['filters']) or 'none'}" for c in catalogue),
		"input_schema": {"type": "object", "properties": {
			"report": {"type": "string", "enum": [c["name"] for c in catalogue], "description": "The report's name."},
			"filters": {"type": "object", "description": "Filter values by filter name (strings).", "additionalProperties": {"type": "string"}}},
			"required": ["report", "filters"]}}]
	messages = [{"role": "user", "content": f"Today is {nowdate()}. {question}"}]
	used, totals = [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0}
	allow_names = bool(s and cint(s.send_names))
	response = None
	for _turn in range(4):  # at most three reports per question
		try:
			response, meta = call(society, "Ask", ASK_SYSTEM, messages, tools=tools, max_tokens=4000, question=question)
		except AIError as e:
			frappe.throw(str(e))
		for k in totals:
			totals[k] += meta[k]
		if response.stop_reason != "tool_use":
			break
		messages.append({"role": "assistant", "content": response.content})
		results = []
		for b in response.content:
			if b.type != "tool_use":
				continue
			read, shown = run_tool(society, catalogue, b.input, allow_names)
			if shown:
				used.append(shown)
			results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(read, default=str), "is_error": "error" in read})
		messages.append({"role": "user", "content": results})
	answer = text_of(response) if response else ""
	if not used:
		answer = answer or _("No report answered this question.")
	log(society, "Ask", "Answered", question=question, answer=answer, model=response.model if response else None,
	    reports_used="; ".join(f"{u['report']} {json.dumps({k: v for k, v in u['filters'].items() if k != 'society'})}" for u in used),
	    **{k: round(v, 4) for k, v in totals.items()})
	return {"answer": answer, "reports": used}


# ---------------------------------------------------------------- 3. theft suspicion, explained
EXPLAIN_SYSTEM = (
	"You explain to a utility's revenue protection officer why an account was flagged by the system's rules. Use only the reasons and "
	"monthly units given. Write two or three plain sentences: what stands out, and what an inspector should check on site. The score is a "
	"reason to look, not proof of theft: never say or imply the consumer stole anything.")


@frappe.whitelist(methods=["POST"])
def explain_account(society: str, connection: str):
	from utility_billing.utility_billing import anomaly  # noqa: PLC0415

	anomaly.require_viewer()
	if frappe.db.get_value("Connection", connection, "society") != society:
		frappe.throw(_("Account {0} isn't an account of {1}.").format(connection, society))
	row = anomaly.score_account(connection)
	if not row or not row["reasons"]:
		return {"explanation": _("Nothing stands out on this account.")}
	content = (f"Account {connection} (category {row['category']}). Score {row['score']}/100. Reasons: "
	           + "; ".join(row["reasons"]) + f". Monthly units, oldest first: {row['history']}.")
	try:
		r, meta = call(society, "Anomaly", EXPLAIN_SYSTEM, [{"role": "user", "content": content}], max_tokens=1000, question=f"Explain {connection}")
	except AIError as e:
		frappe.throw(str(e))
	text = text_of(r)
	log(society, "Anomaly", "Answered", question=f"Explain {connection}", answer=text, **meta)
	return {"explanation": text}


def months_back(n):
	return add_months(getdate(nowdate()).replace(day=1), -n)
