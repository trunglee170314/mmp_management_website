"""Presentation-only terminology; persisted model names/history stay compatible."""
import re
from django import template

register = template.Library()
_EVENT = re.compile(r"^(Boards?)(?= (?:created|updated|added|removed|assigned|released|archived|restored|deleted|responsibility|details|components)\b)", re.IGNORECASE)


@register.filter
def device_event(value):
    return _EVENT.sub(lambda match: "Devices" if match.group().lower() == "boards" else "Device", str(value or ""))


@register.filter
def without_help(form):
    for field in form.fields.values():
        field.help_text = ""
    return form
