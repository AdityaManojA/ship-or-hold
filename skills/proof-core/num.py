#!/usr/bin/env python3
"""Strict number parsing shared by every Proof tool. Standard library only.

Two rules, applied everywhere a Proof tool reads a number:

1. A missing input is an error, never a silent zero. Use ``require`` for fields a
   calculation cannot do without.
2. Parsing is locale-safe. "12,50" is twelve and a half, "1.234,56" is one thousand
   two hundred thirty-four and change, "$1,234.56" is the same amount in dollars.
   When a string is genuinely ambiguous ("1,250" could be 1250 or 1.25), the parser
   refuses and asks the caller to say which convention applies, rather than guess.
"""

import re

_CURRENCY = re.compile(r"[\s$€£¥₹]|USD|EUR|GBP|INR|CAD|AUD", re.I)


class MissingInput(ValueError):
    """A required field is absent or empty."""


class BadNumber(ValueError):
    """A value could not be read as a number, or is ambiguous."""


def parse_number(value, decimal=None, field="value"):
    """Read ``value`` as a float.

    ``decimal`` may be "." or "," to force a convention. When it is None the
    convention is inferred, and an ambiguous string raises ``BadNumber``.
    """
    if value is None:
        raise MissingInput("%s is missing" % field)
    if isinstance(value, bool):
        raise BadNumber("%s is a boolean, not a number" % field)
    if isinstance(value, (int, float)):
        return float(value)
    text = _CURRENCY.sub("", str(value)).strip()
    if not text:
        raise MissingInput("%s is empty" % field)
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("()").lstrip("+-")
    if not re.fullmatch(r"[0-9.,]+", text):
        raise BadNumber("%s: %r is not a number" % (field, value))

    if decimal not in (None, ".", ","):
        raise ValueError("decimal must be '.', ',' or None")
    if decimal is None:
        decimal = _infer_decimal(text, field, value)
    thousands = "," if decimal == "." else "."
    cleaned = text.replace(thousands, "")
    if decimal == ",":
        cleaned = cleaned.replace(",", ".")
    if cleaned.count(".") > 1:
        raise BadNumber("%s: %r has more than one decimal mark" % (field, value))
    try:
        number = float(cleaned)
    except ValueError:
        raise BadNumber("%s: %r is not a number" % (field, value))
    return -number if negative else number


def _infer_decimal(text, field, original):
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        # Whichever mark comes last is the decimal mark.
        return "." if text.rfind(".") > text.rfind(",") else ","
    if not has_dot and not has_comma:
        return "."
    mark = "." if has_dot else ","
    groups = text.split(mark)
    if len(groups) > 2:
        # "1.234.567" or "1,234,567": repeated marks are thousands separators.
        if all(len(g) == 3 for g in groups[1:]):
            return "," if mark == "." else "."
        raise BadNumber("%s: %r is not a number" % (field, original))
    tail = groups[1]
    if mark == "." and len(tail) != 3:
        return "."
    if mark == "," and len(tail) != 3:
        return ","
    if len(groups[0]) > 3:
        # "1234,567" cannot be a thousands grouping, so the mark is decimal.
        return mark
    raise BadNumber(
        "%s: %r is ambiguous (thousands or decimal?). Pass decimal='.' or decimal=','."
        % (field, original))


def require(spec, key, decimal=None):
    """Return ``spec[key]`` as a float, or raise ``MissingInput``."""
    if not isinstance(spec, dict) or key not in spec:
        raise MissingInput("'%s' is missing" % key)
    return parse_number(spec[key], decimal=decimal, field=key)


def optional(spec, key, default=None, decimal=None):
    """Return ``spec[key]`` as a float when present, else ``default``."""
    if not isinstance(spec, dict) or key not in spec or spec[key] in (None, ""):
        return default
    return parse_number(spec[key], decimal=decimal, field=key)


def require_list(spec, key):
    """Return ``spec[key]`` when it is a non-empty list, or raise ``MissingInput``."""
    if not isinstance(spec, dict) or not isinstance(spec.get(key), list) or not spec[key]:
        raise MissingInput("'%s' is missing or empty" % key)
    return spec[key]
