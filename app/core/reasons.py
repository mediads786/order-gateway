EXTRA_INPUT_REASON = "Extra inputs are not permitted"
SHOPIFY_HINT = {
    "field": "(payload)",
    "reason": "This looks like a Shopify order. Send it to POST /webhooks/shopify/orders-create, not to POST /orders.",
}


def summarize_reasons(raw_reasons: list[dict], body: object) -> list[dict]:
    extra_fields = [entry["field"] for entry in raw_reasons if entry["reason"] == EXTRA_INPUT_REASON]
    summarized: list[dict] = []
    if isinstance(body, dict) and "line_items" in body and "admin_graphql_api_id" in body:
        summarized.append(SHOPIFY_HINT.copy())

    other_count = 0
    omitted_count = 0
    extra_added = False
    for entry in raw_reasons:
        if entry["reason"] == EXTRA_INPUT_REASON:
            if not extra_added:
                first_fields = ", ".join(extra_fields[:5])
                remaining = len(extra_fields) - 5
                reason = f"{len(extra_fields)} fields are not part of the order contract: {first_fields}"
                if remaining > 0:
                    reason += f", and {remaining} more"
                summarized.append({"field": "(unexpected fields)", "reason": reason})
                extra_added = True
            continue
        if other_count < 10:
            summarized.append(entry)
            other_count += 1
        else:
            omitted_count += 1

    if omitted_count:
        summarized.append({"field": "(more)", "reason": f"{omitted_count} further problems not shown"})
    return summarized
