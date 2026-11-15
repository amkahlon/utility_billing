app_name = "utility_billing"
app_title = "Utility Management System"
app_publisher = "SoftVerx"
app_description = "Utility management and billing for housing societies and estates"
app_email = "dev@softverx.example"
app_license = "Proprietary"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "utility_billing",
		"logo": "/assets/utility_billing/images/utility-billing-logo.svg",
		"title": "Utility Management System",
		"route": "/desk/ub-dashboard",
	}
]

# The dock, the rail down the left of the desk, is a document rather than a hook. Author it in
# Manage Dock on a developer-mode site and press Export to App, and it is written to
# `utility_billing/dock/utility_billing/utility_billing.json` for git to carry. An app that ships none has no
# rail: its sidebar gets a switcher in the header instead.
#
# A companion app, one that extends a host app rather than standing on its own, says so with
# `mount_on` on that same record, and its entries are appended to the host's rail. Mounting keeps
# the companion off the apps screen, so it takes precedence over any add_to_apps_screen above.

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/utility_billing/css/utility_billing.css"
# The ?v= number is worked out from the files' modification times, so browsers fetch a changed file again by themselves.
from utility_billing.ui_version import ui_version  # noqa: E402

_V = ui_version()
app_include_css = f"/assets/utility_billing/css/ub_theme.css?v={_V}"
web_include_css = f"/assets/utility_billing/css/ub_theme.css?v={_V}"
app_include_js = [f"/assets/utility_billing/js/approval.js?v={_V}", f"/assets/utility_billing/js/ub_map.js?v={_V}"]
boot_session = "utility_billing.boot.boot_session"

# include js, css files in header of web template
# web_include_js = "/assets/utility_billing/js/utility_billing.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "utility_billing/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_kanban_js = {"doctype" : "public/js/doctype_kanban.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "utility_billing/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "utility_billing.utils.jinja_methods",
# 	"filters": "utility_billing.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "utility_billing.install.before_install"
after_install = "utility_billing.install.after_install"
after_migrate = ["utility_billing.install.after_migrate"]

# Bill print format helper (FS-BIL-08)
jinja = {"methods": [
	"utility_billing.utility_billing.bill_print.utility_bill_html",
	"utility_billing.utility_billing.account_print.payment_receipt_html",
]}

# Events for other systems (FS-API-02): written with the change, delivered by the scheduler
doc_events = {
	"Utility Bill": {"on_submit": "utility_billing.utility_billing.events.bill_issued"},
	"Payment Receipt": {"on_submit": "utility_billing.utility_billing.events.payment_posted",
	                    "on_cancel": "utility_billing.utility_billing.events.payment_reversed"},
	"Connection": {"on_update": "utility_billing.utility_billing.events.connection_changed"},
	"Service Request": {"on_update": "utility_billing.utility_billing.events.service_request_changed"},
}

# Numbers and codes that identify something are refused when already used, ignoring case, spaces and dashes (user, 2026-11-15).
# Keep this list the same as uniqueness.DOCTYPES (a test checks it).
UNIQUE_CHECKED = ("API Client", "Cash Counter", "Charge Type", "Common Area Facility", "Connection Flag Type", "Consumer", "Consumer Category",
                  "DISCO Bill", "Fee Type", "Feeder", "Grid Station", "Journal Export Layout", "KPI Definition", "Ledger Account", "Line Section",
                  "Meter", "Network Asset", "Property", "Reading Exception Code", "Reading Route", "Reason Code", "Service Request Type",
                  "Society", "Tax", "Transformer")
for _dt in UNIQUE_CHECKED:
	doc_events.setdefault(_dt, {})["validate"] = "utility_billing.utility_billing.uniqueness.validate"

# Late payment surcharge the day after the due date + grace days (FS-REC-03)
scheduler_events = {
	"all": [
		# Send the messages that are due: retries, and those held by quiet hours (FS-NTF-01, 02, 04)
		"utility_billing.utility_billing.notify.process_queue",
		# Deliver events to subscribed systems, with retries (FS-API-02)
		"utility_billing.utility_billing.events.deliver",
	],
	"daily": [
		"utility_billing.utility_billing.account.apply_surcharges",
		# Summary journals up to yesterday and a receivables reconciliation (FS-GL-02, 04)
		"utility_billing.utility_billing.gl.daily",
		# Reminders before the due date and overdue notices (FS-NTF-01)
		"utility_billing.utility_billing.notify.daily",
		# Installments paid or missed (FS-WF-05)
		"utility_billing.utility_billing.plans.daily",
		# Reminders and escalation of waiting approvals; delegations start and end (FS-ACC-07)
		"utility_billing.utility_billing.access.daily",
		# On the 1st: revenue assurance checks for last month (FS-RA)
		"utility_billing.utility_billing.revenue_assurance.monthly",
	],
}

# Uninstallation
# ------------

# before_uninstall = "utility_billing.uninstall.before_uninstall"
# after_uninstall = "utility_billing.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "utility_billing.utils.before_app_install"
# after_app_install = "utility_billing.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "utility_billing.utils.before_app_uninstall"
# after_app_uninstall = "utility_billing.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "utility_billing.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "utility_billing.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "utility_billing.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["utility_billing.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"utility_billing.tasks.all"
# 	],
# 	"daily": [
# 		"utility_billing.tasks.daily"
# 	],
# 	"hourly": [
# 		"utility_billing.tasks.hourly"
# 	],
# 	"weekly": [
# 		"utility_billing.tasks.weekly"
# 	],
# 	"monthly": [
# 		"utility_billing.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "utility_billing.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "utility_billing.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "utility_billing.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "utility_billing.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["utility_billing.utils.before_request"]
# after_request = ["utility_billing.utils.after_request"]

# Job Events
# ----------
# before_job = ["utility_billing.utils.before_job"]
# after_job = ["utility_billing.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"utility_billing.auth.validate"
# ]

# on_social_login = "utility_billing.auth.on_social_login"

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

# Fixtures: roles and approval workflows shipped with the app (synced on bench migrate)
fixtures = [
	{
		"dt": "Role",
		"filters": [["role_name", "in", [
			"Society Admin", "Billing Manager", "General Manager", "Billing Officer", "Meter Reader",
			"Cashier", "Recovery Officer", "Accounts Officer", "Management", "Billing Auditor", "API Integration",
		]]],
	},
	{"dt": "Workflow State", "filters": [["name", "in", ["Draft", "Pending Approval", "Approved", "Rejected", "Cancelled"]]]},
	{"dt": "Workflow Action Master", "filters": [["name", "in", ["Submit for Approval", "Approve", "Reject", "Withdraw", "Cancel"]]]},
	{"dt": "Workflow", "filters": [["name", "in", ["Tariff Approval", "Tariff Adjustment Approval", "Bill Run Approval", "Account Adjustment Approval", "Refund Approval", "Bill Revision Approval", "Bulk Adjustment Approval", "Installment Plan Approval", "Write Off Approval", "Security Deposit Approval", "Opening Balance Approval"]]]},
	{"dt": "Property Setter", "filters": [["name", "in", ["Utility Bill-main-default_print_format", "Payment Receipt-main-default_print_format"]]]},
]

