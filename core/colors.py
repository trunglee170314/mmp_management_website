"""The shared, deliberately small palette. Existing stored colors remain valid."""
from django.core.exceptions import ValidationError

PALETTE = [
    ("#3B82F6", "Blue"), ("#22C55E", "Green"), ("#14B8A6", "Teal"),
    ("#8B5CF6", "Purple"), ("#F59E0B", "Amber"), ("#F97316", "Orange"),
    ("#EC4899", "Pink"), ("#64748B", "Slate"),
]


def valid_palette_color(value, current=None, allow_auto=False):
    value = str(value or "").upper()
    if allow_auto and value in {"", "AUTO"}:
        return ""
    if current and value == str(current).upper():
        return current
    if value not in {color for color, _label in PALETTE}:
        raise ValidationError("Choose one of the eight preset colors.")
    return value
