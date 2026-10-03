"""Ordered routing rules. First matching rule wins."""

def route(data: dict, existing_customer: bool, review: bool) -> tuple[str, str, str]:
    if review:
        return "Operations", "needs_review", "Possible duplicate requires a person to review it"
    if data["request_type"] == "billing":
        return "Finance", "new", "Billing request"
    if data["request_type"] == "service" and data["urgency"] == "urgent":
        return "Escalations", "new", "Urgent service request"
    if existing_customer:
        return "Account Management", "new", "Existing customer"
    if data["request_type"] == "sales" and data["estimated_value_cents"] > 2500000:
        return "Senior Sales", "new", "Sales value exceeds $25,000"
    if data["request_type"] == "sales":
        return "Sales", "new", "Sales request"
    if data["request_type"] == "service":
        return "Service", "new", "Service request"
    return "Operations", "new", "General inquiry"
