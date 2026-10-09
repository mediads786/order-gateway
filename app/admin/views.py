import json
import os
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode

from app.db.models import Approval, AuditEvent, Job, Order, Proposal, Shipment
from app.admin.theme import CSS, status_class

ORDER_STATES = (
    "RECEIVED", "REJECTED", "PENDING_APPROVAL", "APPROVED", "CANCELLED",
    "QUEUED", "PROCESSING", "RETRYING", "CONFIRMED", "FAILED_DEAD",
)
PAGE_SIZE = 25


def safe(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def status_badge(status: str, label: str | None = None) -> str:
    return f'<span class="badge {status_class(status)}">{safe(status if label is None else label)}</span>'


def truncate_with_details(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return safe(text)
    return f'<details><summary>{safe(text[:limit])}...</summary><div>{safe(text)}</div></details>'


def page(
    title: str, content: str, *, refresh: bool = True, paused: bool = False,
    show_logout: bool = True, show_refresh: bool = True, login: bool = False,
) -> str:
    refresh_tag = '<meta http-equiv="refresh" content="3">' if refresh else ""
    refresh_label = "Resume refresh" if paused else "Pause refresh"
    refresh_value = "1" if paused else "0"
    logout = '<form method="post" action="/admin/logout"><button class="button-secondary">Logout</button></form>' if show_logout else ""
    items = (
        ("Orders", "/admin/orders"), ("Approvals", "/admin/approvals"),
        ("Proposals", "/admin/proposals"), ("Workflow events", "/admin/workflow-events"),
    )
    current = "Orders" if title in ("Order detail", "404") else title
    links = "".join(
        f'<a href="{target}"' + (' aria-current="page"' if label == current else "")
        + f'>{label}</a>' for label, target in items
    )
    refresh_link = f'<a href="?refresh={refresh_value}">{refresh_label}</a>' if show_refresh else ""
    # All table markup is generated here by the view functions, after escaping values.
    content = content.replace('<table>', '<div class="table-scroll" tabindex="0"><table>')
    content = content.replace('</table>', '</table></div>')
    main_class = ' class="login-main"' if login else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{safe(title)}</title>{refresh_tag}<style>{CSS}</style></head><body>"
        '<header class="site-header"><div class="header-inner"><div class="brand">Order Gateway</div>'
        f'<nav aria-label="Admin navigation">{links}{refresh_link}{logout}</nav></div></header>'
        f"<main{main_class}>{content}</main></body></html>"
    )


def login_page(message: str = "") -> str:
    notice = f"<p>{safe(message)}</p>" if message else ""
    content = (
        '<div class="login-card"><h1>Order Gateway login</h1>'
        f"{notice}<form method=\"post\" action=\"/admin/login\"><label>Admin token "
        "<input type=\"password\" name=\"token\" required></label><button>Log in</button></form>"
        "</div>"
    )
    return page("Admin login", content, refresh=False, show_logout=False, show_refresh=False, login=True)


def not_found_page() -> str:
    return page("404", "<h1>404</h1><p>Order not found.</p>", refresh=False, show_logout=False, show_refresh=False)


def orders_page(
    rows: list[tuple[Order, Job | None]], counts: dict[str, int], status: str,
    page_number: int, total: int, refresh: bool,
) -> str:
    all_class = ' class="filter-active"' if not status else ""
    count_links = [f'<a href="/admin/orders"{all_class}>ALL ({sum(counts.values())})</a>']
    count_links.extend(
        f'<a href="/admin/orders?{urlencode({"status": state})}"'
        + (' class="filter-active"' if status == state else "")
        + f'>{status_badge(state, f"{state} ({counts.get(state, 0)})")}</a>'
        for state in ORDER_STATES
    )
    table_rows = []
    for order, job in rows:
        order_id = str(order.order_id)
        last_error = (job.last_error or "") if job else ""
        table_rows.append(
            "<tr>"
            f'<td class="mono"><a href="/admin/orders/{safe(order_id)}">{safe(order_id[:8])}</a></td>'
            f"<td>{safe(utc_text(order.created_at))}</td><td>{safe(order.source)}</td>"
            f'<td class="wrap">{safe(order.external_ref)}</td><td class="wrap">{safe(order.customer_name)}</td>'
            f"<td class=\"mono\">{safe(order.total)} {safe(order.currency)}</td>"
            f"<td>{status_badge(order.status)}</td>"
            f"<td class=\"mono\">{safe(job.attempts if job else '')}</td><td class=\"wrap\">{truncate_with_details(last_error)}</td></tr>"
        )
    if not table_rows:
        table_rows.append('<tr><td class="empty-state" colspan="9">No orders yet.</td></tr>')
    filters = '<p class="filters order-filters">' + " ".join(count_links) + "</p>"
    selected = f'<p class="subtitle">{status_badge(status, "Filter: " + status) if status else "Filter: ALL"}</p>'
    params = {"status": status} if status else {}
    previous = ""
    if page_number > 1:
        previous_url = "/admin/orders?" + urlencode({**params, "page": page_number - 1})
        previous = f'<a href="{previous_url}">Prev</a> '
    next_link = ""
    if page_number * PAGE_SIZE < total:
        next_url = "/admin/orders?" + urlencode({**params, "page": page_number + 1})
        next_link = f'<a href="{next_url}">Next</a>'
    body = (
        "<h1>Orders</h1>" + filters + selected
        + "<table><thead><tr><th>ID</th><th>Created (UTC)</th><th>Source</th>"
        "<th>External ref</th><th>Customer</th><th>Total</th><th>Status</th>"
        "<th>Attempts</th><th>Last error</th></tr></thead><tbody>"
        + "".join(table_rows) + "</tbody></table>"
        + f"<p>{previous}Page {page_number}{' ' + next_link if next_link else ''}</p>"
    )
    return page("Orders", body, refresh=refresh, paused=not refresh)


def order_detail_page(
    order: Order, job: Job | None, shipments: list[Shipment], events: list[AuditEvent],
    refresh: bool, message: str = "",
) -> str:
    flash = {"requeued": "Order requeued.", "not_dead": "Order is not failed dead.", "not_found": "Order not found."}
    notice = f"<p>{safe(flash[message])}</p>" if message in flash else ""
    if order.status == "PENDING_APPROVAL":
        notice += "<p>Waiting for approval</p>"
    elif order.status == "CANCELLED":
        notice += "<p>Cancelled</p>"
    customer = f"{order.customer_name or ''} {order.customer_email or ''} {order.customer_phone or ''}"
    header = (
        "<section><h2>Order</h2><dl>"
        f"<dt>ID</dt><dd class=\"mono\">{safe(order.order_id)}</dd><dt>Source</dt><dd>{safe(order.source)}</dd>"
        f"<dt>External ref</dt><dd>{safe(order.external_ref)}</dd><dt>Status</dt><dd>{status_badge(order.status)}</dd>"
        f"<dt>Created</dt><dd>{safe(utc_text(order.created_at))}</dd><dt>Customer</dt><dd>{safe(customer)}</dd>"
        f"<dt>Currency</dt><dd>{safe(order.currency)}</dd><dt>Total</dt><dd class=\"mono\">{safe(order.total)}</dd></dl></section>"
    )
    line_rows = "".join(
        f"<tr><td>{safe(line.sku)}</td><td class=\"mono\">{safe(line.qty)}</td><td class=\"mono\">{safe(line.unit_price)}</td>"
        f"<td class=\"mono\">{safe(line.unit_price * line.qty)}</td></tr>" for line in order.lines
    )
    lines_section = (
        "<section><h2>Lines</h2><table><thead><tr><th>SKU</th><th>Qty</th>"
        "<th>Unit price</th><th>Line total</th></tr></thead><tbody>"
        f"{line_rows}</tbody></table></section>"
    )
    if job:
        job_section = (
            "<section><h2>Job</h2><dl>"
            f"<dt>Status</dt><dd>{status_badge(job.status)}</dd><dt>Attempts</dt><dd class=\"mono\">{safe(job.attempts)}</dd>"
            f"<dt>Max attempts</dt><dd class=\"mono\">{safe(os.getenv('MAX_ATTEMPTS', '5'))}</dd>"
            f"<dt>Next attempt</dt><dd>{safe(utc_text(job.next_attempt_at))}</dd>"
            f"<dt>Last error</dt><dd>{safe(job.last_error)}</dd></dl></section>"
        )
    else:
        job_section = "<section><h2>Job</h2><p>No job.</p></section>"
    shipment_rows = "".join(
        f"<tr><td class=\"mono\">{safe(shipment.shipment_id)}</td><td>{status_badge(shipment.status)}</td>"
        f"<td>{safe(utc_text(shipment.applied_at) if shipment.applied_at else '')}</td></tr>"
        for shipment in shipments
    )
    shipment_section = ""
    if shipments:
        shipment_section = (
            "<section><h2>Shipments</h2><table><thead><tr><th>ID</th><th>Status</th>"
            f"<th>Applied</th></tr></thead><tbody>{shipment_rows}</tbody></table></section>"
        )
    audit_rows = "".join(
        "<tr>"
        f"<td>{safe(utc_text(event.created_at))}</td><td>{safe(event.event_type)}</td>"
        f"<td class=\"wrap\"><pre>{safe(json.dumps(event.details, indent=2, sort_keys=True, ensure_ascii=False))}</pre></td></tr>"
        for event in events
    )
    audit = (
        "<section><h2>Audit trail</h2><table><thead><tr><th>Time (UTC)</th>"
        f"<th>Event</th><th>Details</th></tr></thead><tbody>{audit_rows}</tbody></table></section>"
    )
    retry = ""
    if order.status == "FAILED_DEAD":
        retry = f'<form method="post" action="/admin/orders/{safe(order.order_id)}/retry"><button>Retry</button></form>'
    return page(
        "Order detail", f"<h1>Order detail</h1>{notice}{retry}{header}{lines_section}{job_section}{shipment_section}{audit}",
        refresh=refresh, paused=not refresh,
    )


def workflow_events_page(
    rows: list[AuditEvent], event_type: str, page_number: int, total: int, refresh: bool,
) -> str:
    table_rows = []
    for event in rows:
        order_id = ""
        if event.order_id is not None:
            full_order_id = str(event.order_id)
            order_id = f'<a href="/admin/orders/{safe(full_order_id)}">{safe(full_order_id[:8])}</a>'
        details = json.dumps(event.detail, indent=2, sort_keys=True, ensure_ascii=False)
        table_rows.append(
            "<tr>"
            f"<td>{safe(utc_text(event.created_at))}</td>"
            f"<td class=\"mono\">{safe(str(event.request_id)[:8])}</td><td class=\"wrap\">{safe(event.actor_name)}</td>"
            f"<td>{safe(event.role)}</td><td>{safe(event.workflow)}</td>"
            f"<td>{safe(event.event_type)}</td><td class=\"mono\">{safe(event.http_status)}</td>"
            f"<td class=\"mono\">{order_id}</td><td class=\"wrap\"><pre>{safe(details)}</pre></td></tr>"
        )
    if not table_rows:
        table_rows.append('<tr><td class="empty-state" colspan="9">No workflow events yet.</td></tr>')
    event_types = (
        "workflow.requested", "workflow.denied", "workflow.rejected",
        "workflow.executed", "workflow.not_executable", "workflow.failed",
        "workflow.approval_requested", "workflow.approved", "workflow.approval_rejected",
        "workflow.proposed", "workflow.proposal_invalid", "workflow.proposal_confirmed",
        "workflow.proposal_discarded",
    )
    filters = ['<a href="/admin/workflow-events">ALL</a>']
    filters.extend(
        f'<a href="/admin/workflow-events?{urlencode({"type": item})}">{safe(item)}</a>'
        for item in event_types
    )
    previous = ""
    if page_number > 1:
        params = {"page": page_number - 1}
        if event_type:
            params["type"] = event_type
        previous = f'<a href="/admin/workflow-events?{urlencode(params)}">Prev</a> '
    next_link = ""
    if page_number * PAGE_SIZE < total:
        params = {"page": page_number + 1}
        if event_type:
            params["type"] = event_type
        next_link = f'<a href="/admin/workflow-events?{urlencode(params)}">Next</a>'
    body = (
        '<h1>Workflow events</h1><p class="filters">' + " ".join(filters) + "</p>"
        + f'<p class="subtitle">Filter: {safe(event_type or "ALL")}</p>'
        + "<table><thead><tr><th>Time (UTC)</th><th>Request</th><th>Actor</th><th>Role</th>"
        "<th>Workflow</th><th>Event</th><th>HTTP</th><th>Order</th><th>Detail</th>"
        "</tr></thead><tbody>" + "".join(table_rows) + "</tbody></table>"
        + f"<p>{previous}Page {page_number}{' ' + next_link if next_link else ''}</p>"
    )
    return page("Workflow events", body, refresh=refresh, paused=not refresh)


def approvals_page(rows: list[tuple[Approval, dict]], status: str, page_number: int, total: int, refresh: bool) -> str:
    table_rows = []
    for approval, summary in rows:
        if approval.workflow == "cancel_order":
            values = approval.input or {}
            summary = {"order_id": values.get("order_id"), "reason": values.get("reason")}
        order_link = ""
        if approval.order_id is not None:
            order_id = str(approval.order_id)
            order_link = f'<a href="/admin/orders/{safe(order_id)}">{safe(order_id[:8])}</a>'
        table_rows.append(
            "<tr>"
            f"<td>{safe(utc_text(approval.created_at))}</td>"
            f"<td class=\"mono\">{safe(str(approval.approval_id)[:8])}</td><td>{safe(approval.workflow)}</td>"
            f"<td>{status_badge(approval.status)}</td><td class=\"wrap\">{safe(approval.requested_by_name)}</td>"
            f"<td class=\"wrap\">{safe(approval.decided_by_name)}</td><td>{safe(utc_text(approval.decided_at) if approval.decided_at else '')}</td>"
            f"<td class=\"wrap\">{safe(approval.decision_reason)}</td><td class=\"mono\">{order_link}</td>"
            f"<td class=\"wrap\"><pre>{safe(json.dumps(summary, sort_keys=True, ensure_ascii=False))}</pre></td></tr>"
        )
    if not table_rows:
        table_rows.append('<tr><td class="empty-state" colspan="10">No approvals yet.</td></tr>')
    statuses = ("PENDING", "APPROVED", "REJECTED", "EXECUTED", "EXECUTION_FAILED")
    filters = ['<a href="/admin/approvals">ALL</a>']
    filters.extend(f'<a href="/admin/approvals?{urlencode({"status": item})}">{status_badge(item)}</a>' for item in statuses)
    params = {"status": status} if status else {}
    previous = ""
    if page_number > 1:
        previous = f'<a href="/admin/approvals?{urlencode({**params, "page": page_number - 1})}">Prev</a> '
    next_link = ""
    if page_number * PAGE_SIZE < total:
        next_link = f'<a href="/admin/approvals?{urlencode({**params, "page": page_number + 1})}">Next</a>'
    body = (
        '<h1>Approvals</h1><p class="filters">' + " ".join(filters) + "</p>"
        + f'<p class="subtitle">{status_badge(status, "Filter: " + status) if status else "Filter: ALL"}</p>'
        + "<table><thead><tr><th>Created (UTC)</th><th>Approval</th><th>Workflow</th><th>Status</th>"
        "<th>Requested by</th><th>Decided by</th><th>Decided at</th><th>Reason</th><th>Order</th><th>Summary</th>"
        "</tr></thead><tbody>" + "".join(table_rows) + "</tbody></table>"
        + f"<p>{previous}Page {page_number}{' ' + next_link if next_link else ''}</p>"
    )
    return page("Approvals", body, refresh=refresh, paused=not refresh, show_logout=False)


def proposals_page(rows: list[tuple[Proposal, str | None]], status: str, page_number: int, total: int, refresh: bool) -> str:
    table_rows = []
    for proposal, order_id in rows:
        order_link = ""
        if order_id:
            order_link = f'<a href="/admin/orders/{safe(order_id)}">{safe(order_id[:8])}</a>'
        table_rows.append(
            "<tr>"
            f"<td>{safe(utc_text(proposal.created_at))}</td>"
            f"<td class=\"mono\">{safe(str(proposal.proposal_id)[:8])}</td>"
            f"<td class=\"wrap\">{safe(proposal.requested_by_name)}</td><td>{safe(proposal.proposer)}</td>"
            f"<td>{status_badge(proposal.status)}</td><td>{safe(proposal.workflow)}</td>"
            f"<td class=\"wrap\">{safe(proposal.invalid_reason)}</td><td class=\"mono\">{len(proposal.text)}</td><td class=\"mono\">{order_link}</td></tr>"
        )
    if not table_rows:
        table_rows.append('<tr><td class="empty-state" colspan="9">No proposals yet.</td></tr>')
    statuses = ("PROPOSED", "INVALID", "CONFIRMED", "DISCARDED")
    filters = ['<a href="/admin/proposals">ALL</a>']
    filters.extend(f'<a href="/admin/proposals?{urlencode({"status": item})}">{status_badge(item)}</a>' for item in statuses)
    params = {"status": status} if status else {}
    previous = ""
    if page_number > 1:
        previous = f'<a href="/admin/proposals?{urlencode({**params, "page": page_number - 1})}">Prev</a> '
    next_link = ""
    if page_number * PAGE_SIZE < total:
        next_link = f'<a href="/admin/proposals?{urlencode({**params, "page": page_number + 1})}">Next</a>'
    body = (
        '<h1>Proposals</h1><p class="filters">' + " ".join(filters) + "</p>"
        + f'<p class="subtitle">{status_badge(status, "Filter: " + status) if status else "Filter: ALL"}</p>'
        + "<table><thead><tr><th>Created (UTC)</th><th>Proposal</th><th>Requested by</th>"
        "<th>Proposer</th><th>Status</th><th>Workflow</th><th>Invalid reason</th><th>Text length</th><th>Order</th>"
        "</tr></thead><tbody>" + "".join(table_rows) + "</tbody></table>"
        + f"<p>{previous}Page {page_number}{' ' + next_link if next_link else ''}</p>"
    )
    return page("Proposals", body, refresh=refresh, paused=not refresh, show_logout=False)
