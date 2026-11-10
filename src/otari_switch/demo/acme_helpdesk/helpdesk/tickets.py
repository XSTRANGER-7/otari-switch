"""The five AI features of Acme Helpdesk. Each one calls a frontier model directly today."""

import json

from helpdesk.llm import client

SUMMARY_PROMPT = "Summarise this support ticket in one or two sentences for an agent. Be factual."
PRIORITY_PROMPT = "Classify the ticket priority. Answer with exactly one word: low, medium, high or urgent."
FIELDS_PROMPT = "Extract the fields from the support ticket."
REPLY_PROMPT = (
    "You are a friendly Acme support agent. Write a reply to the customer: thank them, address every point, "
    "say what happens next, and sign off as 'The Acme Support Team'."
)
ACTION_PROMPT = "Decide the single best next action for this ticket by calling one tool."

FIELDS_SCHEMA = {
    "type": "object",
    "properties": {
        "customer_name": {"type": "string"},
        "product": {"type": "string"},
        "order_id": {"type": ["string", "null"]},
        "issue_type": {"type": "string", "enum": ["billing", "delivery", "defect", "account", "other"]},
    },
    "required": ["customer_name", "product", "order_id", "issue_type"],
    "additionalProperties": False,
}

TOOLS = [
    {"type": "function", "function": {
        "name": "refund_order", "description": "Refund an order in full.",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"},
                                                        "reason": {"type": "string"}},
                       "required": ["order_id", "reason"]}}},
    {"type": "function", "function": {
        "name": "escalate_to_human", "description": "Send the ticket to a senior agent.",
        "parameters": {"type": "object", "properties": {"team": {"type": "string",
                                                                 "enum": ["billing", "technical", "safety"]},
                                                        "note": {"type": "string"}},
                       "required": ["team", "note"]}}},
    {"type": "function", "function": {
        "name": "send_kb_article", "description": "Send a help-centre article that solves the issue.",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string"}},
                       "required": ["topic"]}}},
]


def summarize_ticket(ticket: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": ticket}],
        temperature=0.2,
    )
    return resp.choices[0].message.content


def classify_priority(ticket: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": PRIORITY_PROMPT}, {"role": "user", "content": ticket}],
        temperature=0,
    )
    return resp.choices[0].message.content.strip().lower()


def extract_fields(ticket: str) -> dict:
    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "system", "content": FIELDS_PROMPT}, {"role": "user", "content": ticket}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "ticket_fields", "schema": FIELDS_SCHEMA, "strict": True}},
    )
    return json.loads(resp.choices[0].message.content)


def draft_reply(ticket: str, summary: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "system", "content": REPLY_PROMPT},
                  {"role": "user", "content": f"Ticket:\n{ticket}\n\nAgent summary: {summary}"}],
        temperature=0.5,
    )
    return resp.choices[0].message.content


def choose_action(ticket: str) -> dict:
    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "system", "content": ACTION_PROMPT}, {"role": "user", "content": ticket}],
        tools=TOOLS,
        tool_choice="required",
    )
    call = resp.choices[0].message.tool_calls[0]
    return {"name": call.function.name, "arguments": json.loads(call.function.arguments)}
