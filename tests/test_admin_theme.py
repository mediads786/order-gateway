from app.admin.theme import status_class
from app.admin.views import truncate_with_details
from test_admin import create_order, login


def test_status_class_maps_statuses_and_unknown_safely():
    groups = {
        "status-green": ("CONFIRMED", "APPLIED", "EXECUTED", "DONE"),
        "status-amber": ("RETRYING", "PENDING", "PENDING_APPROVAL", "PROPOSED"),
        "status-red": ("FAILED_DEAD", "FAILED", "REJECTED", "EXECUTION_FAILED"),
        "status-blue": ("RECEIVED", "QUEUED", "PROCESSING", "APPROVED"),
        "status-neutral": ("CANCELLED", "DISCARDED", "INVALID", '<script>unknown</script>'),
    }
    for expected, statuses in groups.items():
        for status in statuses:
            assert status_class(status) == expected


def test_authenticated_admin_theme_keeps_badge_navigation_and_escaping(client, monkeypatch):
    create_order(client, "admin-theme", name="<script>Theme User</script>")
    login(client, monkeypatch)
    response = client.get("/admin/orders")
    assert response.status_code == 200
    assert '<a href="/admin/orders" aria-current="page">Orders</a>' in response.text
    assert '<span class="badge status-blue">RECEIVED</span>' in response.text
    assert "&lt;script&gt;Theme User&lt;/script&gt;" in response.text
    assert "<script>Theme User</script>" not in response.text


def test_truncate_with_details_preserves_text_and_escapes():
    assert truncate_with_details("Short error") == "Short error"
    assert truncate_with_details("x" * 80) == "x" * 80
    assert truncate_with_details("<script>") == "&lt;script&gt;"
    text = "<script>" + "x" * 90 + "</script>"
    result = truncate_with_details(text)
    assert result.startswith("<details><summary>&lt;script&gt;" + "x" * 72 + "...</summary>")
    assert "<div>&lt;script&gt;" + "x" * 90 + "&lt;/script&gt;</div>" in result
    assert result.endswith("</details>")
    assert "<script>" not in result
