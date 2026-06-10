"""Parse a Lever apply page into a FormSpec (standard fields + all cards + rqdata)."""

import json
import re
from typing import cast

from selectolax.parser import HTMLParser

from applyme.models import Card, CardField, CardFieldType, FieldRef, FormSpec

_SITEKEY_RE = re.compile(r'data-sitekey="([0-9a-f-]+)"', re.I)
_STANDARD = ("name", "email", "phone", "org", "location", "selectedLocation")
_TYPE_MAP = {
    "multiple-choice": "multiple-choice",
    "multiple-select": "multiple-select",
    "dropdown": "dropdown",
    "text": "text",
    "textarea": "textarea",
}


def parse_form_html(html: str, posting_url: str) -> FormSpec:
    """Parse a Lever /apply page HTML string into a FormSpec."""
    tree = HTMLParser(html)
    standard: dict[str, FieldRef] = {}
    for node in tree.css("input, select, textarea"):
        name = node.attributes.get("name")
        if name in _STANDARD:
            standard[name] = FieldRef(
                input_name=name,
                field_type=node.attributes.get("type", "text") or "text",
                required="required" in node.attributes,
                selector=f'[name="{name}"]',
            )
    sitekey_m = _SITEKEY_RE.search(html)
    account_node = tree.css_first('input[name="accountId"]')
    account_id = account_node.attributes.get("value", "") if account_node else ""
    cards = _parse_cards(tree)
    return FormSpec(
        standard_fields=standard,
        cards=cards,
        sitekey=sitekey_m.group(1) if sitekey_m else "",
        account_id=account_id or "",
        posting_id=posting_url.rstrip("/").split("/")[-2],
        rqdata=None,
    )


def _parse_cards(tree: HTMLParser) -> list[Card]:
    """Decode every cards[…][baseTemplate] hidden input into a Card with typed CardFields."""
    cards: list[Card] = []
    for tpl in tree.css('input[name$="[baseTemplate]"]'):
        raw = tpl.attributes.get("value")
        input_name = tpl.attributes.get("name")
        if not raw or not input_name:
            continue
        blob = json.loads(raw)
        card_id = blob["id"]
        prefix = input_name.split("[")[0]  # 'cards' or 'surveysResponses'
        fields = [
            CardField(
                field_index=i,
                field_type=cast("CardFieldType", _TYPE_MAP.get(f["type"], "text")),
                text=f["text"],
                required=f.get("required", False),
                options=[o["text"] for o in f.get("options", [])],
                input_name=f"{prefix}[{card_id}][field{i}]",
            )
            for i, f in enumerate(blob.get("fields", []))
        ]
        cards.append(Card(card_id=card_id, fields=fields))
    return cards
