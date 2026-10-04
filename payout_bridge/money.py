"""Amounts as integers in minor units, parsed strictly.

A reconciliation that rounds through floats invents the differences it is supposed to find, so every
amount is read once, as text, into an integer number of minor units (cents, yen, fils). Text that could
mean two different numbers is an error, not a guess.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO 4217 minor units for the currencies that are not two-decimal. Everything else is two.
_EXPONENTS = {
    **dict.fromkeys(
        ["BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF"], 0
    ),
    **dict.fromkeys(["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"], 3),
    **dict.fromkeys(["CLF", "UYW"], 4),
}


class AmountError(ValueError):
    pass


def exponent(currency: str, overrides: dict[str, int] | None = None) -> int:
    cur = currency.upper()
    if overrides and cur in overrides:
        return overrides[cur]
    return _EXPONENTS.get(cur, 2)


_THOUSANDS_COMMA = re.compile(r"^[1-9]\d{0,2}(,\d{3})+(\.\d+)?$")
_THOUSANDS_DOT = re.compile(r"^[1-9]\d{0,2}(\.\d{3})+(,\d+)?$")
_DECIMAL_COMMA = re.compile(r"^\d+,\d{1,2}$")


def parse_decimal(text: str, *, decimal_comma: bool = False, three_decimals: bool = False) -> Decimal | None:
    """'1,234.56', '-12.30', '(12.30)', '12,5' → Decimal. Empty → None. Ambiguous → AmountError.

    three_decimals: the currency has three decimals (KWD, BHD…), so a lone thousands separator ('1,234';
    '1.234' with decimal_comma) could also be the decimal one, and is refused."""
    if text is None:
        return None
    s = str(text).strip().replace(" ", "").replace(" ", "").replace("'", "")
    if s == "":
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.startswith(("-", "+")):
        negative, s = (negative != (s[0] == "-")), s[1:]
    elif s.endswith("-"):
        negative, s = (not negative), s[:-1]
    if not s or not re.fullmatch(r"[\d.,]+", s):
        raise AmountError(f"not an amount: {text!r}")
    if "," in s and "." in s:
        if s.rfind(".") > s.rfind(","):
            if not _THOUSANDS_COMMA.match(s):
                raise AmountError(f"ambiguous amount: {text!r}")
            s = s.replace(",", "")
        else:
            if not _THOUSANDS_DOT.match(s):
                raise AmountError(f"ambiguous amount: {text!r}")
            s = s.replace(".", "").replace(",", ".")
    elif three_decimals and re.fullmatch(r"\d{1,3}(\.\d{3})+" if decimal_comma else r"\d{1,3}(,\d{3})+", s):
        # '1,234' is a thousand in one locale and one-point-two-three-four in the other
        raise AmountError(f"ambiguous amount for a three-decimal currency: {text!r}")
    elif "," in s:
        if decimal_comma or _DECIMAL_COMMA.match(s):
            if s.count(",") > 1:
                raise AmountError(f"ambiguous amount: {text!r}")
            s = s.replace(",", ".")
        elif _THOUSANDS_COMMA.match(s):
            s = s.replace(",", "")
        else:
            raise AmountError(f"ambiguous amount: {text!r}")
    elif s.count(".") > 1:
        if decimal_comma and _THOUSANDS_DOT.match(s):
            s = s.replace(".", "")
        else:
            raise AmountError(f"ambiguous amount: {text!r}")
    elif decimal_comma and "." in s:
        if not _THOUSANDS_DOT.match(s):
            raise AmountError(f"ambiguous amount: {text!r}")
        s = s.replace(".", "")
    try:
        value = Decimal(s)
    except InvalidOperation as e:  # pragma: no cover - the regexes above should prevent it
        raise AmountError(f"not an amount: {text!r}") from e
    return -value if negative else value


def to_minor(value: Decimal, currency: str, overrides: dict[str, int] | None = None) -> int:
    """Major units → integer minor units. A value with more decimals than the currency has is an error."""
    exp = exponent(currency, overrides)
    scaled = value.scaleb(exp)
    if scaled != scaled.to_integral_value():
        raise AmountError(f"{value} has more than {exp} decimals for {currency.upper()}")
    return int(scaled)


def parse_minor(text: str, currency: str, *, decimal_comma: bool = False, overrides: dict[str, int] | None = None) -> int | None:
    value = parse_decimal(text, decimal_comma=decimal_comma, three_decimals=exponent(currency, overrides) >= 3)
    return None if value is None else to_minor(value, currency, overrides)


def round_minor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def fmt(minor: int, currency: str, overrides: dict[str, int] | None = None, *, sign: bool = False) -> str:
    """12345, 'USD' → '123.45'; with sign=True → '+123.45'. Thousands separated by commas."""
    exp = exponent(currency, overrides)
    neg = minor < 0
    whole, frac = divmod(abs(minor), 10**exp) if exp else (abs(minor), 0)
    text = f"{whole:,}" + (f".{frac:0{exp}d}" if exp else "")
    if neg:
        return "−" + text
    return ("+" + text) if sign and minor > 0 else text


class FeeSchedule:
    """'2.9%+0.30' → a percentage of the gross plus a fixed amount, rounded half up to the minor unit."""

    def __init__(self, text: str):
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*(?:\+\s*(\d+(?:\.\d+)?))?\s*", text or "")
        if not m:
            raise ValueError(f"fee schedule must look like '2.9%+0.30', got {text!r}")
        self.text = text.strip()
        self.percent = Decimal(m.group(1))
        self.fixed = Decimal(m.group(2) or "0")

    def expected(self, gross_minor: int, currency: str, overrides: dict[str, int] | None = None) -> int:
        fixed_minor = round_minor(self.fixed.scaleb(exponent(currency, overrides)))
        return round_minor(Decimal(abs(gross_minor)) * self.percent / 100) + fixed_minor
