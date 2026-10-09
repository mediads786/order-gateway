STATUS_CLASSES = {
    **dict.fromkeys(("CONFIRMED", "APPLIED", "EXECUTED", "DONE"), "status-green"),
    **dict.fromkeys(("RETRYING", "PENDING", "PENDING_APPROVAL", "PROPOSED"), "status-amber"),
    **dict.fromkeys(("FAILED_DEAD", "FAILED", "REJECTED", "EXECUTION_FAILED"), "status-red"),
    **dict.fromkeys(("RECEIVED", "QUEUED", "PROCESSING", "APPROVED"), "status-blue"),
}


def status_class(status: str) -> str:
    return STATUS_CLASSES.get(status, "status-neutral")


CSS = """
:root {
    --page-bg: #f5f7fa;
    --surface: #ffffff;
    --border: #e1e5eb;
    --text: #1f2933;
    --muted: #5f6b7a;
    --header-bg: #14213d;
    --header-text: #ffffff;
    --accent: #a3e635;
    --focus-ring: 2px solid #2563eb;
    --mono: ui-monospace, SFMono-Regular, Consolas, monospace;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    background: var(--page-bg);
    color: var(--text);
    font: 15px/1.6 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.site-header { background: var(--header-bg); color: var(--header-text); }
.header-inner {
    max-width: 1100px;
    margin: auto;
    padding: 1rem 1.25rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
}
.brand { font-size: 1.15rem; font-weight: 750; letter-spacing: -.02em; }
nav { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem; }
nav a, button {
    display: inline-block;
    padding: .55rem .8rem;
    border: 1px solid currentColor;
    border-radius: .5rem;
    font: inherit;
    font-weight: 650;
    line-height: 1.4;
    text-decoration: none;
}
nav a, .button-secondary {
    background: transparent;
    color: var(--header-text);
}
nav a:hover, .button-secondary:hover { background: #263754; }
nav a[aria-current="page"] {
    background: var(--accent);
    color: var(--header-bg);
    border-color: var(--accent);
    text-decoration: underline;
    text-underline-offset: .25em;
}
button { cursor: pointer; background: var(--accent); color: var(--header-bg); border-color: var(--header-bg); }
button:hover { background: #bef264; }
a:focus-visible, button:focus-visible, input:focus-visible, .table-scroll:focus-visible {
    outline: var(--focus-ring);
    outline-offset: 3px;
}
.site-header a:focus-visible, .site-header button:focus-visible {
    box-shadow: 0 0 0 3px var(--surface);
    outline-offset: 4px;
}
nav form { margin: 0; }
main { max-width: 1440px; width: 100%; min-width: 0; margin: 0 auto; padding: 0 24px; }
h1 { font-size: clamp(1.65rem, 4vw, 2.2rem); line-height: 1.2; letter-spacing: -.035em; margin: 0 0 1.25rem; }
h2 { font-size: 1.15rem; line-height: 1.3; margin: 0 0 1rem; }
p { overflow-wrap: anywhere; }
.subtitle { color: var(--muted); margin: .75rem 0 1.25rem; }
main a { color: #174ea6; text-underline-offset: .18em; }
main a:hover { color: var(--header-bg); }
.filters { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem; }
.filters a { padding: .25rem .5rem; border: 2px solid var(--border); border-radius: .5rem; background: var(--surface); text-decoration: none; white-space: nowrap; }
.order-filters a { border-radius: 999px; font-size: .75rem; }
.order-filters .badge { padding: 0; background: transparent; font-weight: inherit; }
.order-filters a.filter-active { border-color: var(--header-bg); font-weight: 750; }
section { margin: 1.5rem 0; padding: 1.25rem; background: var(--surface); border: 1px solid var(--border); border-radius: .75rem; min-width: 0; }
.table-scroll { max-width: 100%; overflow-x: auto; margin: 1rem 0; border: 1px solid var(--border); border-radius: .75rem; background: var(--surface); }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 10px 12px; vertical-align: top; border-bottom: 1px solid var(--border); white-space: nowrap; }
.wrap { white-space: normal; overflow-wrap: anywhere; min-width: 180px; max-width: 340px; }
details { color: var(--muted); }
summary { cursor: pointer; }
details[open] summary { margin-bottom: .5rem; }
th { background: #eef1f5; font-size: .8rem; color: var(--muted); font-weight: 700; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover { background: #f5f7fa; }
td.mono, dd.mono { font-family: var(--mono); font-size: .85rem; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; white-space: nowrap; padding: .2rem .6rem; border-radius: 999px; font-size: .75rem; font-weight: 750; line-height: 1.5; }
.status-green { color: #166534; background: #dcfce7; }
.status-amber { color: #854d0e; background: #fef3c7; }
.status-red { color: #991b1b; background: #fee2e2; }
.status-blue { color: #1e40af; background: #dbeafe; }
.status-neutral { color: #374151; background: #e5e7eb; }
pre { margin: 0; padding: .85rem; border-radius: .5rem; background: #f1f3f6; font: .8rem/1.6 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 20rem; overflow: auto; }
dl { display: grid; grid-template-columns: minmax(7rem, 1fr) minmax(0, 3fr); gap: .65rem 1rem; margin: 0; }
dt { color: var(--muted); font-weight: 600; }
dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.empty-state { padding: 2rem 1rem; text-align: center; color: var(--muted); }
.login-main { display: grid; place-items: center; min-height: 70vh; }
.login-card { width: 100%; max-width: 420px; padding: 2rem; background: var(--surface); border: 1px solid var(--border); border-radius: .85rem; box-shadow: 0 8px 24px #14213d0a; }
.login-card label { display: block; font-weight: 600; }
.login-card input { display: block; width: 100%; margin: .5rem 0 1.25rem; padding: .7rem; border: 1px solid var(--muted); border-radius: .5rem; background: var(--surface); color: var(--text); font: inherit; }
.login-card button { width: 100%; }
@media (max-width: 640px) {
    .header-inner { padding-left: 1rem; padding-right: 1rem; }
    nav { width: 100%; }
    nav a, button { padding: .5rem .65rem; }
    section, .login-card { padding: 1rem; }
    dl { grid-template-columns: 1fr; gap: .25rem; }
    dd { margin-bottom: .65rem; }
    table { min-width: 640px; }
}
"""
