# Order Gateway file reference

Scope: every file in the repository's tracked-file list, plus `docs/project-tour.md` and `docs/file-reference.md`. Ignored environment files, local backups, caches and installed dependencies are outside this reference. Each file path names the source for its row's description. Functions listed are selected real entry points or helpers, not an exhaustive symbol index. Empty package markers are listed in their folders.

An API is an application's callable routes. An ERP is the business system receiving orders or stock changes. A schema defines expected data fields. A migration changes database structure. A fixture is saved input or output used by assertions; a fake simulates an external service. CI means automated checks configured for repository changes. Test rows describe assertions in source, not execution results. The connected tour defines other terms in `docs/project-tour.md`.

## Root

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `.env.example` | Names database, adapter, webhook, admin, approval and proposal settings with local defaults or empty secret placeholders. | None; configuration values |
| `.gitignore` | Excludes .venv, .env, Python/pytest cache files and the old project-guide filename. | None; ignore patterns |
| `Dockerfile` | Builds the Python image and copies application, scripts, mock ERP and migrations. Its default command upgrades the schema before starting the API; Compose overrides it for worker/mock services. | None; image instructions and CMD |
| `docker-compose.yml` | Wires db, api, mock_erp and worker, with health dependencies, environments and volumes. The optional odoo profile adds Odoo and its database. | None; services db, api, mock_erp, worker, odoo-db, odoo |
| `alembic.ini` | Sets migration directory, Python path and console logging for Alembic. Runtime DATABASE_URL overrides the listed database URL in `migrations/env.py`. | None; Alembic/logging sections |
| `requirements.txt` | Pins FastAPI, Uvicorn, SQLAlchemy, psycopg, Alembic, Pydantic, pytest and httpx dependencies. | None; dependency specifications |
| `pytest.ini` | Registers the live_odoo marker for optional live Odoo assertions. | None; pytest marker |
| `README.md` | Explains setup, configuration, architecture, demos, feature layers and known limitations. Historical outcome statements here are not execution claims made by this reference. | None; document sections |
| `deactivate-demo-keys.ps1` | Lists container-side API keys and deactivates names matching the cancel demo naming pattern. | No declared functions; top-level PowerShell |
| `demo-cancel.ps1` | Demonstrates governed local/ERP cancellation, role/self-decision denial, duplicate requests and uncertain-state refusal; temporarily stops/restarts the worker for local mode. It creates keys and changes demo state if invoked. | Check, New-DemoKey, Call, New-OrderViaGateway, Wait-OrderStatus, Cancel-Body, Decision-Body |
| `demo-proposals.ps1` | Demonstrates proposal creation/confirmation, ownership, threshold approval, stock adjustment and rejection, then opens admin pages. It creates demo keys and domain records if invoked. | Step, Expect, New-Key, Call, Propose, Confirm, Decide, OrderStatus, WaitFor |
| `review-m9.ps1` | Collects selected cancellation implementation files and diffs into the clipboard and a review-m9.txt output if invoked. | No declared functions; top-level PowerShell |

## .github/workflows

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `.github/workflows/ci.yml` | Configures checks on pushes to master and pull requests: starts Compose, installs Python dependencies, invokes pytest, shows logs on failure and removes containers/volumes afterward. | None; job test |

## app

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/__init__.py` | Empty package marker. | None |
| `app/main.py` | Assembles the API and routers, validates adapter selection, adds admin response protections, and defines order, Shopify, shipment, retry and health routes. | validate_erp_adapter_setting, lifespan, admin_response_headers, create_order, shopify_orders_create, _shipment_json_constant, create_shipment, get_order, _get_order, retry_order, health |

## app/adapters

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/adapters/__init__.py` | Re-exports the adapter interface, data types, permanent-error type and factory. | Imported AdapterLine, AdapterOrder, ErpAdapter, ErpOrderResult, NonRetryableAdapterError, ShipmentLine, get_adapter |
| `app/adapters/base.py` | Defines the common order/line/result shapes and ERP method contract. NonRetryableAdapterError carries a reason for permanent failure classification. | AdapterLine, AdapterOrder, ShipmentLine, ErpOrderResult, NonRetryableAdapterError, ErpAdapter |
| `app/adapters/factory.py` | Selects mock or Odoo using ERP_ADAPTER and checks required Odoo URL/key. | AdapterConfigurationError, get_adapter |
| `app/adapters/mock.py` | Calls mock sales-order, stock-adjustment and cancellation endpoints and validates their results. Its shipment method raises NotImplementedError. | MockErpAdapter; create_sales_order, adjust_stock_for_shipment, adjust_stock, cancel_order |
| `app/adapters/odoo.py` | Translates orders, shipments, stock adjustments, cancellation and stock seeding into Odoo JSON-2 calls. It reuses external markers, resolves products/partners, classifies named errors and verifies inventory/cancel readback. | OdooAdapter; _call, create_sales_order, _confirm_order, _resolve_products, _resolve_partner, _created_id, cancel_order, adjust_stock_for_shipment, adjust_stock, set_opening_stock, _apply_counted_quantity |

## app/admin

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/admin/__init__.py` | Empty package marker. | None |
| `app/admin/auth.py` | Reads the shared admin token, checks minimum length and derives/verifies the admin session cookie using HMAC. | configured_token, admin_enabled, session_cookie_value, authenticated |
| `app/admin/routes.py` | Handles login/logout, order list/detail/retry, workflow events, approvals and proposal pages. Protected GET requests redirect to login while protected POST requests reject unauthorized callers. | _authorized, admin_root, login_get, login_post, logout, list_orders, order_detail, retry_order_admin, list_workflow_events, list_approvals, list_proposals |
| `app/admin/views.py` | Builds escaped HTML for admin pages, including filters, pagination, refresh controls, audit detail and dead-order retry forms. | safe, utc_text, page, login_page, orders_page, order_detail_page, workflow_events_page, approvals_page, proposals_page |

## app/api

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/api/__init__.py` | Empty package marker; route handlers are located elsewhere, including `app/main.py`. | None |

## app/core

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/core/__init__.py` | Empty package marker. | None |
| `app/core/reasons.py` | Summarizes unexpected fields and caps detailed rejection reasons; adds a hint for recognizable Shopify payloads sent to ordinary intake. | summarize_reasons |
| `app/core/schemas.py` | Defines strict canonical order/customer/line and shipment models, with contact, quantity, currency and decimal-price rules. | CustomerInput, LineInput, OrderInput, ShipmentLineInput, ShipmentInput; trim_contact_text, contact_required, trim_sku, reject_float_price |

## app/db

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/db/__init__.py` | Empty package marker. | None |
| `app/db/models.py` | Defines the database records, relationships, model constraints and indexes for orders, delivery, shipments and governance. | Order, OrderLine, IdempotencyKey, AuditEvent, Job, Shipment, ApiKey, WorkflowEvent, Approval, Proposal |
| `app/db/session.py` | Requires DATABASE_URL and builds the SQLAlchemy engine and reusable session factory with connection pre-ping. | Base; engine and SessionLocal variables |

## app/governance

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/governance/__init__.py` | Empty package marker. | None |
| `app/governance/approvals.py` | Creates, lists and decides approvals; handles held-order queueing, approved stock changes and local/ERP cancellation. Locks/conditional transitions prevent repeated decisions and store execution outcomes. | DecisionInput, LocalCancellationUpdateError; decide, list_approvals, get_approval, check_cancellation_eligibility, _cancellation_eligibility, create_cancellation_approval, create_adjustment_approval, _conditional_status, _approve_order, _reject_approval, _approve_adjustment, _execute_stock_adjustment, _approve_cancellation, _execute_cancellation, _fail_cancellation |
| `app/governance/audit.py` | Inserts an attributed workflow event in its own database transaction and truncates workflow labels. | write_event |
| `app/governance/keys.py` | Hashes a plain API key and finds the matching active database credential. | hash_api_key, authenticate_api_key |
| `app/governance/legacy.py` | Applies optional role-based API-key checks to original order routes; unknown modes enforce key authentication and authenticated role denials are audited. | legacy_guard |
| `app/governance/registry.py` | Defines create_order, adjust_stock and cancel_order metadata, request/decision roles and input schemas. | AdjustStockInput, CancelOrderInput, Workflow; can_request, can_decide; WORKFLOWS |
| `app/governance/routes.py` | Exposes the workflow registry and governed request route; dispatches order intake or approval creation and writes request/outcome events. | list_workflows, request_workflow, run_governed_request, _record_for_actor, _record, _validation_detail, _order_id_from_result, _reject_request_body |

## app/proposals

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/proposals/__init__.py` | Empty package marker. | None |
| `app/proposals/proposers.py` | Provides text-to-workflow result types, a rule parser and optional Anthropic HTTP proposer; factory settings select one. Neither implementation is given ERP execution tools. | AllowedWorkflow, ProposerResult, Proposer, ProposerConfigurationError, RuleProposer, AnthropicProposer; propose, get_proposer |
| `app/proposals/routes.py` | Authenticates and validates proposal creation, lists/details, confirmation and discard. It checks allowed workflows, enforces ownership, and sends confirmation through governed request handling. | ProposalRequest; create_proposal, list_proposals, get_proposal, confirm_proposal, discard_proposal, _config_values, _allowed_workflows, _proposal_visible, _confirm_result |
| `app/proposals/service.py` | Hashes text, counts proposals in a UTC day, stores drafts/events with quota locking, formats safe responses and conditionally changes draft status. | text_digest, utc_day_window, count_today, store_proposal, proposal_body, proposal_event, transition_proposal, _add_event |

## app/services

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/services/__init__.py` | Empty package marker. | None |
| `app/services/orders.py` | Parses/validates intake, serializes responses, locks idempotency keys, stores rejected/accepted orders and creates jobs or held approvals atomically with the key response. | NonFiniteJSON, HoldPolicy; submit_order, submit_unmappable_order, serialize_order, create_queued_job, _request_hash, _lock_idempotency_key, _rejected_order, _replay_status, _contains_nul |
| `app/services/retry.py` | Locks an order/job and requeues only a FAILED_DEAD order with a FAILED job, resetting attempts and adding order.requeued. | requeue_failed_order |
| `app/services/shipments.py` | Hashes shipment requests, checks order/SKU eligibility, locks shipment records and calls the adapter synchronously; stores applied or pending/failure outcomes and audit events. | canonical_request_hash, apply_shipment |
| `app/services/shopify_webhooks.py` | Verifies raw-body HMAC signatures and maps supported Shopify fields into canonical intake, raising a specific error for unmappable input. | UnmappableShopifyOrder; verify_signature, map_shopify_order, _usable_text, _first_text, _address |

## app/workers

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `app/workers/__init__.py` | Empty package marker. | None |
| `app/workers/worker.py` | Claims due database jobs, delivers through the selected adapter, saves success/failure, schedules backoff and recovers stale processing jobs. | compute_backoff, classify_failure, claim_next_job, _claim_one, _mark_done, _mark_failure, recover_stale_jobs, process_one, run_until_idle, run_forever |

## mock_erp

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `mock_erp/__init__.py` | Empty package marker. | None |
| `mock_erp/main.py` | Provides an unauthenticated in-memory ERP simulation with order reads/create/cancel, stock adjustment, fault injection and reset. Duplicate identifiers reuse records; restart/reset loses memory. | SalesOrderInput, FaultInput, StockAdjustmentInput, CancellationInput; create_sales_order, list_sales_orders, get_sales_order, cancel_sales_order, adjust_stock, set_faults, reset |

## scripts

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `scripts/api_keys.py` | Creates named role keys with hash-only storage, safely lists metadata and deactivates a named credential. Creation prints the plain key once. | _create, _list, _deactivate, main |
| `scripts/demo_audit.py` | Reads one order's audit rows ordered by timestamp/event ID and formats timestamp, type and JSON detail for console output. | fetch_audit_lines, main |
| `scripts/demo_e2e.py` | Defines a host-side demo for acceptance/replay, injected mock failure, recovery and audit output, with fault reset on exit. Supports gateway/mock URLs and optional DEMO_API_KEY. | say, expect, api_headers, set_faults, post_order, get_order, wait_for_status, print_second_order_audit, run_demo, main |
| `scripts/odoo_probe.py` | Issues exploratory Odoo JSON-2 reads/error cases/product and stock writes at module execution and prints bounded response reports with key replacement. It changes probe data if invoked. | call |
| `scripts/odoo_probe2.py` | Looks up prior probe data, explores inventory reference fields and reapplies counted stock, then requests a duplicate-login validation case. It changes probe data if invoked. | call |
| `scripts/odoo_seed.py` | Reads local .env settings when available, requires the Odoo adapter and seeds configured sample SKUs through set_opening_stock. Existing environment values take precedence. | main; calls `app/adapters/odoo.py:set_opening_stock` |

## migrations

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `migrations/env.py` | Requires DATABASE_URL, exposes model metadata and configures Alembic's online/offline migration modes. | run_migrations_offline, run_migrations_online |

## migrations/versions

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `migrations/versions/0001_intake_storage.py` | Creates orders, order_lines, idempotency_keys and audit_events with primary/foreign keys. | upgrade, downgrade |
| `migrations/versions/0002_status_and_replay_code.py` | Widens orders.status and adds the saved HTTP status_code to idempotency_keys, backfilling 201 before removing the default. | upgrade, downgrade |
| `migrations/versions/0003_jobs_and_erp_order_id.py` | Adds orders.erp_order_id and creates jobs with one unique order_id per job. | upgrade, downgrade |
| `migrations/versions/0004_job_retries.py` | Adds jobs.next_attempt_at and an index on status/due time. | upgrade, downgrade |
| `migrations/versions/0005_shipments.py` | Creates shipment state, request hash, lines, reference/error and timestamp storage with PENDING/APPLIED check. | upgrade, downgrade |
| `migrations/versions/0006_workflow_governance.py` | Creates api_keys and workflow_events and adds a trigger refusing workflow-event row UPDATE/DELETE. | upgrade, downgrade; SQL function reject_workflow_event_mutation and trigger workflow_events_append_only |
| `migrations/versions/0007_approvals.py` | Creates approvals with allowed-state and no-self-decision checks, key/order links and status/time index. | upgrade, downgrade |
| `migrations/versions/0008_proposals.py` | Creates proposals with status check, stored text/hash/input/outcome and requester/time index. | upgrade, downgrade |
| `migrations/versions/0009_open_cancel_approval.py` | Adds a partial unique index on the cancellation target in approval input while status is PENDING or APPROVED. | upgrade, downgrade; index uq_approvals_open_cancel |

## tests

Each row describes the assertions defined in that file. Key names are examples selected from that file. `tests/conftest.py` supplies the database and mock ERP setup; no row reports a result.

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `tests/conftest.py` | Prepares a dedicated _test database, applies migrations, requires mock ERP, clears data and provides a FastAPI TestClient. | migrate_test_database, mock_erp_service, clear_database, client |
| `tests/test_admin.py` | Asserts admin enable/auth/cookie/header rules, escaped read views, filtering/pagination, detail errors and dead-job retry behavior including concurrent attempts. | test_admin_disabled_for_all_routes, test_admin_login_cookie_validation_and_logout, test_admin_security_headers_and_no_scripts, test_two_simultaneous_admin_retries_requeue_once |
| `tests/test_approvals.py` | Asserts threshold holds, no-job pending orders, replay, distinct deciders, serialized transitions and stock adjustment success/failure and read views. | RecordingAdapter, pending_order, test_threshold_below_equal_and_above_hold_policy, test_double_decision_and_concurrent_decisions_are_serialized, test_adjust_stock_execution_failure_is_final_and_audited |
| `tests/test_cancellations.py` | Asserts request/decision eligibility, local and ERP cancellation, duplicate open target protection, Odoo delivery/readback guards and proposal-to-cancel behavior. | seed_order, test_cancel_registry_and_refusal_matrix_create_no_approval, test_simultaneous_cancel_requests_create_one_open_approval, test_odoo_cancel_flow_guards_delivery_and_checks_readback |
| `tests/test_demo_audit.py` | Asserts the audit formatter orders timestamps and includes an intake event. | test_fetch_audit_lines_are_time_ordered_and_include_received |
| `tests/test_governance.py` | Asserts authentication and role matrix, registry schemas, governed intake/replay/conflict, attributed events, concurrent requests, CLI key storage and trigger protection. | create_key, workflow_request, events_for_request, test_permission_matrix, test_workflow_events_reject_update_delete_but_allow_insert |
| `tests/test_hardening.py` | Asserts cancellation approved-event order linkage, duplicate open cancellation conflict and rejection of zero mock stock delta. | test_cancel_approved_event_has_order_id, test_duplicate_open_cancel_still_409, test_mock_erp_rejects_zero_delta |
| `tests/test_legacy_guard.py` | Asserts default-open versus keyed original routes, role/inactive-key refusal, actor attribution and fail-closed unknown mode. | test_legacy_auth_off_keeps_original_order_routes_open, test_legacy_retry_requires_key_role_and_attributes_requeue, test_unrecognized_legacy_auth_value_fails_closed |
| `tests/test_live_odoo.py` | Defines opt-in live Odoo sale-order reuse and repeated shipment inventory assertions under ODOO_LIVE=1. | test_live_odoo_order_and_repeated_one_unit_shipment |
| `tests/test_odoo_adapter.py` | Asserts auth headers, mapping and decimal boundary, saved/example response shapes, permanent error classification, factory settings, draft reuse and inventory markers. | adapter_order, recorded, test_auth_headers_golden_mapping_and_decimal_boundary, test_factory_defaults_and_rejects_invalid_or_incomplete_config |
| `tests/test_odoo_adapter_exception_names.py` | Asserts selected built-in Odoo argument-error names are permanent even with HTTP 500. | test_builtin_odoo_argument_errors_are_non_retryable |
| `tests/test_odoo_worker.py` | Asserts worker outcomes with FakeOdoo, including lost response after create, transient retry, permanent mapping errors and existing draft/cancelled orders. | build_adapter, order_and_job, test_timeout_after_odoo_create_retries_and_reuses_the_same_order, test_existing_cancelled_order_fails_dead |
| `tests/test_orders.py` | Asserts canonical intake/rejection/replay, transactions, concurrent jobs, retry timing/classification, stale/dead/manual recovery and mock external-ID reuse. | valid_payload, test_skip_locked_claims_distinct_jobs_in_separate_transactions, test_stale_jobs_are_recovered_or_dead_lettered, test_order_and_job_rollback_if_transaction_fails_after_order_insert |
| `tests/test_proposals.py` | Asserts parser patterns, allowlist/schema controls, creation without ERP calls, owner confirmation/races/approval, quotas and simulated Anthropic handling. | FakeProposer, CallRecordingAdapter, test_post_proposal_creates_no_domain_rows_or_adapter_calls, test_simultaneous_real_confirms_create_one_order_and_job, test_confirm_rechecks_request_permission_and_records_denial |
| `tests/test_reasons.py` | Asserts shortened rejection output and saved Shopify hint/reasons. | test_summarize_reasons_collapses_ninety_extra_fields, test_summarize_reasons_puts_shopify_hint_first_and_limits_other_reasons, test_shopify_shaped_order_gets_short_stored_rejection_reasons |
| `tests/test_shipments.py` | Asserts signed gates, validation/eligibility, replay/conflict, pending recovery and concurrent application against fake Odoo stock. | sign, configure, test_odoo_failure_leaves_pending_and_resend_applies, test_simultaneous_identical_shipments_apply_stock_once |
| `tests/test_shopify_rejections.py` | Asserts specific stored mapper failure reasons, retained rejection payload and repeated rejected-webhook reuse. | _body_with, _assert_rejected, test_missing_customer_name_stores_specific_rejection, test_repeated_unmappable_webhook_reuses_rejected_order |
| `tests/test_shopify_webhooks.py` | Asserts signatures over raw bytes, mapping/fallbacks, configuration/topic/ID gates, replay, acknowledged rejection and worker delivery. | sign_body, _headers, test_raw_body_signature_preserves_whitespace_and_unicode, test_valid_signed_webhook_creates_order_job_then_worker_confirms |

## tests/fakes

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `tests/fakes/__init__.py` | Empty package marker. | None |
| `tests/fakes/fake_odoo.py` | Simulates Odoo JSON-2 using httpx transport and in-memory products, partners, orders, stock/moves and deliveries. It records calls and can inject failures/timeouts or ineffective cancellation. | FakeOdoo; add_product, set_stock, transport, handle |

## tests/fixtures

These JSON files label their contents as sample data, not live Shopify data. Their consumers include `tests/test_shopify_webhooks.py` and `tests/test_shopify_rejections.py`.

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `tests/fixtures/order_fallbacks.json` | Supplies missing customer/top-level contact with shipping name/phone and a padded SKU for mapping fallbacks. | None; JSON input |
| `tests/fixtures/order_full.json` | Supplies customer/contact, currency, multiple lines and extra Shopify-like fields for supported-field mapping. | None; JSON input |
| `tests/fixtures/order_no_contact.json` | Supplies usable name/line but empty or null contact fields for rejection. | None; JSON input |
| `tests/fixtures/order_no_sku.json` | Supplies one empty SKU for unmappable-line rejection. | None; JSON input |
| `tests/fixtures/order_no_sku_null.json` | Supplies one null SKU for unmappable-line rejection. | None; JSON input |

## tests/fixtures/odoo

The label in each JSON file distinguishes recorded shapes from hand-written examples. A hand-written example is not evidence of a live Odoo result, as explained in `tests/fixtures/odoo/README.md`.

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `tests/fixtures/odoo/README.md` | Explains sanitized recorded fixtures and labels hand-written shapes that need replacement with recordings. | None; documentation |
| `tests/fixtures/odoo/create_result.json` | Stores a labeled recorded create result containing a list of new identifiers. | None; label and response |
| `tests/fixtures/odoo/order_found.json` | Hand-written nonempty sale.order response shape with id/state; live provenance is not verified. | None; label and response |
| `tests/fixtures/odoo/order_missing.json` | Hand-written empty sale.order response shape; live provenance is not verified. | None; label and response |
| `tests/fixtures/odoo/partner_found.json` | Stores labeled recorded res.partner rows with names and false email values. | None; label and response |
| `tests/fixtures/odoo/partner_missing.json` | Hand-written empty partner response shape; live provenance is not verified. | None; label and response |
| `tests/fixtures/odoo/product_found.json` | Stores a labeled recorded product row with id and default_code. | None; label and response |
| `tests/fixtures/odoo/product_missing.json` | Hand-written empty product response shape; live provenance is not verified. | None; label and response |
| `tests/fixtures/odoo/unauthorized.json` | Stores a labeled recorded 401 invalid-API-key error body without a debug field. | None; label, status and response |
| `tests/fixtures/odoo/unknown_model.json` | Stores a labeled recorded 404 unknown-model error body. | None; label, status and response |
| `tests/fixtures/odoo/validation_error.json` | Stores a labeled recorded 422 duplicate-login validation error body/message. | None; label, status and response |

## docs

Documents describe their contents here. Older narrative statements do not override implementation, and no historic execution result is adopted as a result of this documentation task.

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `docs/architecture.md` | Explains runtime components, order/job states, retry, idempotency, events, governance and proposals. Its state diagram should be read alongside actual intake code in `app/services/orders.py`. | None; document and Mermaid diagram |
| `docs/case-study.md` | Presents the owner's integration-learning problem, design choices, historical observations, limits and next steps. | None; narrative document |
| `docs/demo.gif` | Stores a GIF-format demo media asset. Its visual content is not verified in this reference. | None; binary media |
| `docs/demo.md` | Contains mock/Odoo/governance/approval/cancellation demo instructions and PowerShell examples. Some instructions are historical, so use the current route/auth code for behavior. | None; document and command examples |
| `docs/design-decisions.md` | Records design reasoning, costs and an older roadmap. Some universal claims about jobs, delivery and verification are broader than current code supports. | None; design narrative |
| `docs/odoo-e2e-run.md` | Records historical local Odoo setup and observations for order delivery, cancellation, stock adjustment and signed shipments, plus uncovered cases. | None; historical report |
| `docs/odoo-notes.md` | Explains historical JSON-2 probe shapes, inventory-marker hazards, named errors and cancellation calls, with live-verification boundaries. | None; historical API notes |
| `docs/portfolio-assets.md` | Lists suggested screenshots, GIF scenes, uses and repository hygiene checks for portfolio material. | None; checklist |
| `docs/shopify-live-test.md` | Records a historical development-store webhook setup, observations, payload differences and uncovered scenarios. | None; historical report |
| `docs/project-tour.md` | Explains sourced system diagrams, entry traces, folders, tables, terms, extensions, diagnosis, test assertions, security and interview answers. | None; this documentation deliverable |
| `docs/file-reference.md` | Maps every tracked file and the two new guides to purpose and selected real symbols. | None; this documentation deliverable |

## docs/odoo-probe

| File | What it does | Key functions or classes |
| --- | --- | --- |
| `docs/odoo-probe/cancel-probe.txt` | Saves a historical local JSON-2 cancellation report showing create/confirm, related deliveries, plain/context cancellation and repeat cancellation response shapes. | None; captured text report |
| `docs/odoo-probe/probe_output.txt` | Saves first probe output covering auth/model/method/field errors, product/warehouse/quant shapes and inventory application. Includes truncated debug traceback text in some error bodies. | None; captured text report |
| `docs/odoo-probe/probe2_output.txt` | Saves second probe output covering stock-move references, counted-stock adjustment, repeat-apply hazard and duplicate-login error shape. | None; captured text report |

Assumptions: tracked files define reference scope, and these two new documents are included explicitly; symbol columns select useful names rather than every helper; historic notes and sample fixtures are descriptions rather than fresh verification. Marked not verified: GIF visual content and live provenance of hand-written Odoo response examples.
