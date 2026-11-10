"""These tests call the model for real, so Otari Switch can record them and re-run them on new models."""

import pytest

from helpdesk.tickets import choose_action, classify_priority, draft_reply, extract_fields, summarize_ticket
from tests.sample_tickets import TICKETS


@pytest.mark.parametrize("ticket", TICKETS)
def test_summary_is_short(ticket):
    s = summarize_ticket(ticket)
    assert s and len(s) < 600


@pytest.mark.parametrize("ticket", TICKETS)
def test_priority_is_a_known_label(ticket):
    assert classify_priority(ticket) in {"low", "medium", "high", "urgent"}


@pytest.mark.parametrize("ticket", TICKETS)
def test_fields_have_the_right_keys(ticket):
    f = extract_fields(ticket)
    assert set(f) == {"customer_name", "product", "order_id", "issue_type"}
    assert f["issue_type"] in {"billing", "delivery", "defect", "account", "other"}


@pytest.mark.parametrize("ticket", TICKETS)
def test_reply_signs_off(ticket):
    reply = draft_reply(ticket, summarize_ticket(ticket))
    assert "Acme Support Team" in reply


@pytest.mark.parametrize("ticket", TICKETS)
def test_action_is_a_known_tool(ticket):
    assert choose_action(ticket)["name"] in {"refund_order", "escalate_to_human", "send_kb_article"}
