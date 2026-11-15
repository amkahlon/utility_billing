# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Changes inside a billing period, and what a former holder still owes (increment 22; FS-BIL-02, FS-MST-03, FS-WF tenant and
category changes, SC-13). User decisions, 2026-11-16:

1. A tenant or owner leaving mid-month gets a **final bill** for their days plus everything the account owed up to the change; the
   incoming occupant's bills show only what arises from their start date. What the account owes after the final bill is kept as a
   **Former Occupant Balance**, recovered from the former holder (or the owner, where the tenancy made the owner liable). The account's
   ledger stays one ledger: the current holder's arrears are the account balance less what former holders still owe.
2. Units of a month split by a change are shared by the reading taken on the change day when there is one, otherwise by days
   (Society → Split Units, "By the change-day reading, else by days").

The changes themselves are rows on the connection (Connection Change), written when a service request completes."""

from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from utility_billing.utility_billing import split_engine

CATEGORY, HOLDER = "Category", "Holder"
BY_READING = "By the change-day reading, else by days"


# ---------------------------------------------------------------- recording a change
def record(conn, change_type, day, old_value=None, new_value=None, old_consumer=None, new_consumer=None, reading=None, sr=None,
           owner_liable=0):
	"""A change on the connection (call before conn.save())."""
	conn.append("changes", {"change_date": getdate(day), "change_type": change_type, "old_value": old_value, "new_value": new_value,
	                        "old_consumer": old_consumer, "new_consumer": new_consumer, "owner_liable": cint(owner_liable),
	                        "reading": flt(reading), "has_reading": 1 if reading not in (None, "") and flt(reading) > 0 else 0,
	                        "service_request": sr})


def changes_in(connection, start, end, change_type):
	"""Changes of a type that fall inside a reading period: after its first day and on or before its last."""
	if not (start and end):
		return []
	start, end = getdate(start), getdate(end)
	rows = frappe.get_all("Connection Change", filters={"parent": connection, "parenttype": "Connection", "change_type": change_type},
	                      fields=["*"], order_by="change_date, idx")
	return [r for r in rows if start < getdate(r.change_date) <= end]


# ---------------------------------------------------------------- splitting the period
def split(units, start, end, change, method, previous_reading=0, multiplier=1) -> dict:
	"""The two parts of a reading period cut by `change`: {"before": (units, days), "after": (units, days), "by": "reading"|"days"}.
	By the change-day reading when the society says so and one was taken (and it lies between the two readings), else by days."""
	start, end, day = getdate(start), getdate(end), getdate(change.change_date)
	days_before, days_after = (day - start).days, (end - day).days
	units = Decimal(str(flt(units)))
	if method == BY_READING and cint(change.has_reading):
		before = (Decimal(str(flt(change.reading))) - Decimal(str(flt(previous_reading)))) * Decimal(str(flt(multiplier) or 1))
		if 0 <= before <= units:
			return {"before": (before, days_before), "after": (units - before, days_after), "by": "reading"}
	shares = split_engine.split_units(units, [days_before, days_after])
	return {"before": (shares[0], days_before), "after": (shares[1], days_after), "by": "days"}


# ---------------------------------------------------------------- former holders' balances
def former_outstanding(connection, as_of=None) -> float:
	"""What former holders of the account still owe (negative: owed back to them); `as_of` counts payments made by that day."""
	total = 0.0
	for b in frappe.get_all("Former Occupant Balance", filters={"connection": connection, "status": ["!=", "Written Off"]},
	                        fields=["name", "amount", "surcharge", "paid", "written_off", "change_date"]):
		if as_of and b.change_date and getdate(b.change_date) > getdate(as_of):
			continue
		paid = flt(b.paid) if not as_of else paid_by(b.name, as_of)
		total += flt(b.amount) + flt(b.surcharge) - paid - flt(b.written_off)
	return round(total, 2)


def former_by_connection(society) -> dict:
	"""{connection: what former holders still owe} for a society, in one query (recovery lists every account)."""
	return {r.connection: flt(r.o) for r in frappe.db.sql(
		"""select connection, sum(outstanding) o from `tabFormer Occupant Balance` where society = %s and status = 'Open' group by connection""",
		society, as_dict=True)}


def paid_by(balance, as_of=None) -> float:
	filters = {"former_balance": balance, "docstatus": 1}
	if as_of:
		filters["payment_date"] = ["<=", getdate(as_of)]
	return flt(sum(flt(x) for x in frappe.get_all("Payment Receipt", filters=filters, pluck="amount")))


def refresh(name):
	b = frappe.get_doc("Former Occupant Balance", name)
	b.paid = paid_by(name)
	b.outstanding = round(flt(b.amount) + flt(b.surcharge) - flt(b.paid) - flt(b.written_off), 2)
	if b.status != "Written Off":
		b.status = "Settled" if abs(b.outstanding) < 0.005 else "Open"
	b.flags.ignore_permissions = True
	b.save()
	return b


def after_final_bill(bill, change):
	"""Called when a final bill is posted: what the account owes now is the former holder's (user decision 1)."""
	from utility_billing.utility_billing import account  # noqa: PLC0415

	owed = round(account.balance(bill.connection) - former_outstanding(bill.connection), 2)
	liable = bill.consumer
	if cint(change.owner_liable):
		owner = frappe.db.sql("""select o.consumer from `tabProperty Occupancy` o join `tabConnection` c on c.property = o.parent
		                         where c.name = %s and o.occupant_role = 'Owner' and o.consumer != %s order by o.from_date desc limit 1""",
		                      (bill.connection, bill.consumer))
		liable = owner[0][0] if owner else liable
	b = frappe.get_doc({"doctype": "Former Occupant Balance", "connection": bill.connection, "society": bill.society, "consumer": bill.consumer,
	                    "consumer_name": bill.consumer_name, "liable_consumer": liable, "change_date": change.change_date, "final_bill": bill.name,
	                    "amount": owed, "outstanding": owed, "status": "Open" if abs(owed) >= 0.005 else "Settled"})
	b.insert(ignore_permissions=True)
	return b.name


def surcharge_added(bill, amount):
	"""A final bill's late payment surcharge belongs to the former holder."""
	name = frappe.db.get_value("Former Occupant Balance", {"final_bill": bill.name})
	if name:
		frappe.db.set_value("Former Occupant Balance", name, "surcharge", flt(frappe.db.get_value("Former Occupant Balance", name, "surcharge")) + flt(amount))
		refresh(name)


def check_payment(receipt):
	"""Payment Receipt validate: a payment for a former holder's balance is for this account's open balance."""
	if not receipt.get("former_balance"):
		return
	b = frappe.db.get_value("Former Occupant Balance", receipt.former_balance, ["connection", "status"], as_dict=True)
	if not b or b.connection != receipt.connection:
		frappe.throw(_("{0} isn't a former occupant's balance of account {1}.").format(receipt.former_balance, receipt.connection))
	if b.status != "Open" and receipt.docstatus == 0:
		frappe.throw(_("{0} is already {1}.").format(receipt.former_balance, _(b.status).lower()))
