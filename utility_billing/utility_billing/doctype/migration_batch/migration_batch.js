// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Migration Batch: check, load, undo; the reconciliation against the previous system's control figures (increment 29).
const MB = "utility_billing.utility_billing.doctype.migration_batch.migration_batch.";

function mb_run(frm, method, message) {
	return frm.save().then(() =>
		frappe.call({ method: MB + method, args: { name: frm.doc.name }, freeze: true, freeze_message: message })
			.then((r) => { frm.reload_doc(); return r.message; }));
}

function mb_reconciliation(frm) {
	const wrap = frm.get_field("reconciliation_html").$wrapper;
	let rows = [];
	try { rows = JSON.parse(frm.doc.reconciliation || "[]"); } catch (e) { rows = []; }
	if (!rows.length) { wrap.html(`<p class="text-muted">${__("Check the workbook to see the accounts and receivable by block and category.")}</p>`); return; }
	const fmt = (v) => format_currency(v, frappe.defaults.get_default("currency"));
	const line = (label, file, control, diff) => `<tr><td>${label}</td><td class="text-right">${file}</td><td class="text-right">${control}</td>
		<td class="text-right ${diff ? "text-danger" : "text-success"}">${diff ? diff : "✓"}</td></tr>`;
	const d = frm.doc;
	let html = `<table class="table table-bordered table-sm"><thead><tr><th>${__("Control")}</th><th class="text-right">${__("In the File")}</th>
		<th class="text-right">${__("Previous System")}</th><th class="text-right">${__("Difference")}</th></tr></thead><tbody>
		${line(__("Accounts"), d.account_count, d.control_accounts || "—", d.control_accounts ? d.accounts_difference : 0)}
		${line(__("Receivable"), fmt(d.receivable_total), d.control_receivable ? fmt(d.control_receivable) : "—", d.control_receivable && d.receivable_difference ? fmt(d.receivable_difference) : 0)}
		</tbody></table><table class="table table-bordered table-sm"><thead><tr><th>${__("Block")}</th><th>${__("Category")}</th>
		<th class="text-right">${__("Accounts")}</th><th class="text-right">${__("Receivable")}</th></tr></thead><tbody>`;
	for (const r of rows) {
		html += `<tr><td>${frappe.utils.escape_html(r.block || "—")}</td><td>${frappe.utils.escape_html(r.category || "")}</td>
			<td class="text-right">${r.accounts}</td><td class="text-right">${fmt(r.receivable)}</td></tr>`;
	}
	wrap.html(html + "</tbody></table>");
}

frappe.ui.form.on("Migration Batch", {
	refresh(frm) {
		mb_reconciliation(frm);
		frm.add_custom_button(__("Download Template"), () => window.open(`/api/method/${MB}template`), __("Workbook"));
		const editable = !frm.is_new() && frm.doc.docstatus === 0 && ["Draft", "Rejected", undefined, null, ""].includes(frm.doc.workflow_state);
		if (editable && frm.doc.load_status !== "Loaded") {
			frm.add_custom_button(__("Check"), () => mb_run(frm, "check", __("Checking the workbook…")).then((m) =>
				frappe.show_alert({ message: __("{0} accounts: {1} errors, {2} warnings", [m.accounts, m.errors, m.warnings]), indicator: m.errors ? "orange" : "green" })),
			__("Workbook"));
			frm.add_custom_button(__("Load"), () => frappe.confirm(__("Create the accounts, meters, history and opening balances from the workbook?"), () =>
				mb_run(frm, "load", __("Loading…")).then((m) => frappe.show_alert(m.loaded
					? { message: __("{0} accounts loaded ({1} records)", [m.accounts, m.records]), indicator: "green" }
					: { message: __("Not loaded: {0} errors. See Problems.", [m.errors]), indicator: "red" }))),
			__("Workbook"));
		}
		if (editable && frm.doc.load_status === "Loaded") {
			frm.add_custom_button(__("Undo Load"), () => frappe.confirm(__("Remove everything this batch created?"), () =>
				mb_run(frm, "undo", __("Undoing…")).then((m) => frappe.show_alert({ message: __("{0} records removed", [m.removed]), indicator: "green" }))),
			__("Workbook"));
		}
		if (frm.doc.error_count) {
			frm.dashboard.set_headline(__("{0} errors in the workbook: fix them at the source (see Problems) and check again.", [frm.doc.error_count]), "red");
		} else if (frm.doc.load_status === "Checked") {
			frm.dashboard.set_headline(__("No errors ({0} warnings). Compare the figures with the previous system, then Load.", [frm.doc.warning_count]), "green");
		} else if (frm.doc.load_status === "Loaded" && frm.doc.docstatus === 0) {
			frm.dashboard.set_headline(__("Loaded. Check a few accounts in Consumer 360, then submit for approval; until then the load can be undone."), "blue");
		}
	},
});
