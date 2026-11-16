"""Display values of Excel cells: what Excel shows on screen, written from the cached value and the
cell's number format (docs/rules/xlsx.md).

Numbers are first rounded to 15 significant digits (what Excel keeps), then written by the format
section that applies: positive / negative / zero / text, split on `;`. Supported tokens: digit
placeholders `0 # ?`, thousands `,`, `.`, `%`, `E+ E-`, quoted and `\\`-escaped literals, `_x` (one
space), `*x` (fill, ignored), `[$symbol-locale]`, colours; date and time codes. Conditions, scaling
commas, fractions and elapsed time fall back to General and say so. The built-in formats whose
look follows the operating system (14 short date, 22 date and time) are written `yyyy-mm-dd` and
`yyyy-mm-dd hh:mm`. Leading and trailing blanks of a number's display are dropped.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from openpyxl.styles.numbers import BUILTIN_FORMATS

SYSTEM_DATE_FORMATS = {14: "yyyy-mm-dd", 22: "yyyy-mm-dd hh:mm"}
EPOCH_1900 = dt.datetime(1899, 12, 30)

COLOURS = {"black", "blue", "cyan", "green", "magenta", "red", "white", "yellow"}

MONTHS = {
    "english": ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"),
    "spanish": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                "septiembre", "octubre", "noviembre", "diciembre"),
    "chinese": ("一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月",
                "十一月", "十二月"),
}
MONTHS_SHORT = {
    "english": tuple(name[:3] for name in MONTHS["english"]),
    "spanish": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
    "chinese": tuple(f"{n}月" for n in range(1, 13)),
}
# Monday first, as datetime.weekday() counts.
DAYS = {
    "english": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "spanish": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "chinese": ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"),
}
DAYS_SHORT = {
    "english": tuple(name[:3] for name in DAYS["english"]),
    "spanish": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
    "chinese": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
}
LANGUAGE_IDS = {0x09: "english", 0x0A: "spanish", 0x04: "chinese"}

Value = str | int | float | bool | dt.datetime | dt.date | dt.time | dt.timedelta | None


@dataclass(frozen=True)
class Display:
    text: str
    fallback: str | None = None     # why the value was written as General instead of by its format


def format_string(number_format: str, format_id: int | None) -> tuple[str, str | None]:
    """The format string to apply, and a fallback reason when the id is a built-in we cannot name.
    openpyxl reports built-in 14 as `mm-dd-yy`, so the id, not the string, tells built-ins apart."""
    if format_id is not None and format_id in SYSTEM_DATE_FORMATS:
        return SYSTEM_DATE_FORMATS[format_id], None
    if format_id is not None and format_id < 164 and format_id not in BUILTIN_FORMATS:
        return "General", "unknown_builtin"
    return number_format or "General", None


def display_value(value: Value, number_format: str = "General", format_id: int | None = None,
                  error: bool = False) -> Display:
    if value is None:
        return Display("")
    if error or isinstance(value, str):
        return Display(str(value))
    if isinstance(value, bool):
        return Display("TRUE" if value else "FALSE")
    fmt, reason = format_string(number_format, format_id)
    if reason:
        return _general(value, reason)
    sections = _split_sections(fmt)
    number = _serial(value)
    section, signed = _pick(sections, number)
    tokens, problem = _tokenize(section)
    if problem:
        return _general(value, problem)
    if any(t[0] == "date" for t in tokens):
        if number < 0:
            return _general(value, "negative_date")
        return Display(_date_text(tokens, _datetime(value, number)).strip())
    if not any(t[0] in ("digit", "general") for t in tokens):
        if any(t[0] == "text" for t in tokens):          # "@": numbers are shown as General
            return _general(value, None)
        return Display(_literals(tokens).strip())
    text, problem = _number_text(tokens, abs(number))
    if problem:
        return _general(value, problem)
    if not signed and number < 0:
        text = "-" + text.lstrip()
    return Display(text.strip())


# --- sections and tokens ----------------------------------------------------------------------

def _split_sections(fmt: str) -> list[str]:
    sections, current, i = [], [], 0
    while i < len(fmt):
        ch = fmt[i]
        if ch == '"':
            end = fmt.find('"', i + 1)
            end = len(fmt) if end < 0 else end
            current.append(fmt[i:end + 1])
            i = end + 1
            continue
        if ch in "\\_*" and i + 1 < len(fmt):
            current.append(fmt[i:i + 2])
            i += 2
            continue
        if ch == "[":
            end = fmt.find("]", i)
            end = len(fmt) - 1 if end < 0 else end
            current.append(fmt[i:end + 1])
            i = end + 1
            continue
        if ch == ";":
            sections.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    sections.append("".join(current))
    return sections


def _pick(sections: list[str], number: float) -> tuple[str, bool]:
    """The section for this number, and whether the section itself carries the sign (so the value
    is written without one)."""
    numeric = sections[:3]
    if number < 0 and len(numeric) >= 2:
        return numeric[1], True
    if number == 0 and len(numeric) >= 3:
        return numeric[2], True
    return numeric[0], False


DATE_LETTERS = set("ymdhsYMDHS")


def _tokenize(section: str) -> tuple[list[tuple[str, str]], str | None]:
    """(kind, text) tokens: lit, digit (0 # ?), point, comma, percent, exp (E+ / E-), date, ampm,
    text (@), general. Returns a fallback reason for what is not supported."""
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(section)
    while i < n:
        ch = section[i]
        low = section[i:].lower()
        if ch == '"':
            end = section.find('"', i + 1)
            end = n if end < 0 else end
            tokens.append(("lit", section[i + 1:end]))
            i = end + 1
        elif ch == "\\":
            tokens.append(("lit", section[i + 1:i + 2]))
            i += 2
        elif ch == "_":
            tokens.append(("lit", " "))
            i += 2
        elif ch == "*":
            i += 2
        elif ch == "[":
            end = section.find("]", i)
            end = n if end < 0 else end
            inner = section[i + 1:end]
            i = end + 1
            if inner.startswith("$"):
                symbol, _, locale = inner[1:].partition("-")
                if symbol:
                    tokens.append(("lit", symbol))
                if locale:
                    tokens.append(("locale", locale))
            elif inner[:1] in "<>=":
                return tokens, "condition"
            elif inner.lower() in COLOURS or re.fullmatch(r"(?i)color\d+", inner):
                pass
            elif re.fullmatch(r"(?i)h+|m+|s+", inner):
                return tokens, "elapsed_time"
            else:
                return tokens, "unsupported_token"
        elif low.startswith("general"):
            tokens.append(("general", ""))
            i += 7
        elif low.startswith("am/pm"):
            tokens.append(("ampm", section[i:i + 5]))
            i += 5
        elif low.startswith("a/p"):
            tokens.append(("ampm", section[i:i + 3]))
            i += 3
        elif ch in "eE" and i + 1 < n and section[i + 1] in "+-":
            tokens.append(("exp", section[i + 1]))
            i += 2
        elif ch in DATE_LETTERS:
            j = i
            while j < n and section[j].lower() == ch.lower():
                j += 1
            tokens.append(("date", section[i:j].lower()))
            i = j
        elif ch in "eEgGbB":
            return tokens, "unsupported_token"
        elif ch in "0#?":
            tokens.append(("digit", ch))
            i += 1
        elif ch == ".":
            tokens.append(("point", "."))
            i += 1
        elif ch == ",":
            tokens.append(("comma", ","))
            i += 1
        elif ch == "%":
            tokens.append(("percent", "%"))
            i += 1
        elif ch == "/" and not any(t[0] == "date" for t in tokens) and any(t[0] == "digit" for t in tokens):
            return tokens, "fraction"
        elif ch == "@":
            tokens.append(("text", "@"))
            i += 1
        else:
            tokens.append(("lit", ch))
            i += 1
    if any(t[0] == "date" for t in tokens):
        # In a date section a point or digit means fractional seconds: not supported.
        if any(t[0] in ("digit", "exp", "percent") for t in tokens):
            return tokens, "unsupported_token"
        tokens = [("lit", ".") if t[0] == "point" else ("lit", ",") if t[0] == "comma" else t
                  for t in tokens]
    return tokens, None


def _literals(tokens: list[tuple[str, str]]) -> str:
    return "".join(text for kind, text in tokens if kind == "lit")


# --- numbers ----------------------------------------------------------------------------------

def _decimal(number: float) -> Decimal:
    """The number as Excel keeps it: 15 significant digits."""
    return Decimal(f"{number:.15g}")


def _number_text(tokens: list[tuple[str, str]], number: float) -> tuple[str, str | None]:
    exp_at = next((k for k, t in enumerate(tokens) if t[0] == "exp"), None)
    point_at = next((k for k, t in enumerate(tokens) if t[0] == "point" and (exp_at is None or k < exp_at)), None)
    int_end = point_at if point_at is not None else (exp_at if exp_at is not None else len(tokens))
    frac_end = exp_at if exp_at is not None else len(tokens)
    int_digits = [k for k in range(int_end) if tokens[k][0] == "digit"]
    frac_digits = [k for k in range(int_end, frac_end) if tokens[k][0] == "digit"]
    exp_digits = [k for k in range(frac_end, len(tokens)) if tokens[k][0] == "digit"]
    thousands = False
    for k, (kind, _) in enumerate(tokens):
        if kind != "comma":
            continue
        if k < int_end and any(t[0] == "digit" for t in tokens[k + 1:int_end]):
            thousands = thousands or bool(int_digits and int_digits[0] < k)
        elif not any(t[0] == "digit" for t in tokens[k + 1:frac_end]) and (int_digits or frac_digits):
            return "", "scaling"       # a comma after the last digit placeholder divides by 1000
    value = _decimal(number)
    for kind, _ in tokens:
        if kind == "percent":
            value *= 100
    exponent = 0
    if exp_at is not None:
        width = max(1, len(int_digits))
        if value != 0:
            exponent = math.floor(math.log10(float(value))) - (width - 1)
            value = value.scaleb(-exponent)
    quantum = Decimal(1).scaleb(-len(frac_digits))
    value = value.quantize(quantum, rounding=ROUND_HALF_UP)
    if exp_at is not None and value >= Decimal(10) ** max(1, len(int_digits)):
        exponent += 1
        value = (value / 10).quantize(quantum, rounding=ROUND_HALF_UP)
    whole, _, fraction = f"{value:f}".partition(".")
    whole = "" if whole == "0" else whole
    fraction = fraction.ljust(len(frac_digits), "0")
    out: dict[int, str] = {}
    _fill_integer(tokens, int_digits, whole, thousands, out)
    _fill_fraction(tokens, frac_digits, fraction, out)
    if exp_at is not None:
        sign = "-" if exponent < 0 else ("+" if tokens[exp_at][1] == "+" else "")
        digits = str(abs(exponent)).rjust(sum(1 for k in exp_digits if tokens[k][1] == "0"), "0")
        out[exp_at] = "E" + sign
        for k in exp_digits:
            out[k] = ""
        if exp_digits:
            out[exp_digits[0]] = digits
    parts = []
    for k, (kind, text) in enumerate(tokens):
        if k in out:
            parts.append(out[k])
        elif kind == "lit":
            parts.append(text)
        elif kind == "point":
            parts.append(".")
        elif kind == "percent":
            parts.append("%")
        elif kind == "general":
            parts.append(_general_text(number))
    return "".join(parts), None


def _fill_integer(tokens, positions: list[int], whole: str, thousands: bool, out: dict[int, str]) -> None:
    if not positions:
        return
    if thousands:
        first_zero = next((n for n, k in enumerate(positions) if tokens[k][1] == "0"), None)
        minimum = 0 if first_zero is None else len(positions) - first_zero
        digits = whole.rjust(minimum, "0")
        for k in positions:
            out[k] = ""
        out[positions[0]] = ",".join(_groups(digits)) if digits else ""
        return
    digits = list(whole)
    for n, k in enumerate(reversed(positions)):
        placeholder = tokens[k][1]
        if digits:
            out[k] = digits.pop()
        else:
            out[k] = "0" if placeholder == "0" else " " if placeholder == "?" else ""
    if digits:
        out[positions[0]] = "".join(digits) + out[positions[0]]


def _groups(digits: str) -> list[str]:
    head = len(digits) % 3 or 3
    return [digits[:head]] + [digits[i:i + 3] for i in range(head, len(digits), 3)]


def _fill_fraction(tokens, positions: list[int], fraction: str, out: dict[int, str]) -> None:
    chars = list(fraction[:len(positions)])
    for n in range(len(positions) - 1, -1, -1):
        placeholder = tokens[positions[n]][1]
        if placeholder == "0" or chars[n] != "0":
            break
        chars[n] = "" if placeholder == "#" else " "
    for k, ch in zip(positions, chars):
        out[k] = ch


def _general_text(number: float) -> str:
    value = _decimal(number)
    if value == value.to_integral_value() and abs(value) < Decimal("1e15"):
        return str(int(value))
    magnitude = abs(float(value))
    if magnitude >= 1e11 or magnitude < 1e-9:
        mantissa, exponent = f"{float(value):.5E}".split("E")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return f"{mantissa}E{exponent[0]}{exponent[1:].lstrip('0').rjust(2, '0')}"
    digits = 11 - (math.floor(math.log10(magnitude)) + 1)
    rounded = value.quantize(Decimal(1).scaleb(-max(digits, 0)), rounding=ROUND_HALF_UP)
    text = f"{rounded:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _general(value: Value, reason: str | None) -> Display:
    """General: numbers as `_general_text`; date and time values (whose format we cannot apply)
    as `yyyy-mm-dd`, with ` hh:mm:ss` when there is a time of day."""
    if isinstance(value, dt.datetime):
        text = value.strftime("%Y-%m-%d") if value.time() == dt.time() else value.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(value, dt.date):
        text = value.strftime("%Y-%m-%d")
    elif isinstance(value, dt.time):
        text = value.strftime("%H:%M:%S")
    else:
        text = _general_text(_serial(value))
    return Display(text, reason)


# --- dates ------------------------------------------------------------------------------------

def _serial(value: Value) -> float:
    if isinstance(value, dt.datetime):
        delta = value - EPOCH_1900
        return delta.days + delta.seconds / 86400 + delta.microseconds / 86400e6
    if isinstance(value, dt.date):
        return float((value - EPOCH_1900.date()).days)
    if isinstance(value, dt.time):
        return (value.hour * 3600 + value.minute * 60 + value.second) / 86400
    if isinstance(value, dt.timedelta):
        return value.total_seconds() / 86400
    return float(value)  # type: ignore[arg-type]


def _datetime(value: Value, number: float) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    if isinstance(value, dt.time):
        return dt.datetime.combine(EPOCH_1900.date(), value)
    seconds = round(number * 86400)
    return EPOCH_1900 + dt.timedelta(seconds=seconds)


def _language(tokens: list[tuple[str, str]]) -> str:
    for kind, text in tokens:
        if kind == "locale":
            try:
                code = int(text, 16)
            except ValueError:
                continue
            return LANGUAGE_IDS.get(code & 0x3FF, "english")
    return "english"


def _date_text(tokens: list[tuple[str, str]], when: dt.datetime) -> str:
    language = _language(tokens)
    twelve = any(kind == "ampm" for kind, _ in tokens)
    dates = [k for k, t in enumerate(tokens) if t[0] == "date"]
    parts = []
    for k, (kind, text) in enumerate(tokens):
        if kind == "lit":
            parts.append(text)
        elif kind == "ampm":
            morning = when.hour < 12
            if text.lower() == "am/pm":
                word = "AM" if morning else "PM"
                parts.append(word if text[0].isupper() else word.lower())
            else:
                letter = "A" if morning else "P"
                parts.append(letter if text[0].isupper() else letter.lower())
        elif kind == "date":
            parts.append(_date_code(text, when, language, twelve, _is_minute(tokens, dates, k)))
    return "".join(parts)


def _is_minute(tokens, dates: list[int], k: int) -> bool:
    if tokens[k][1][0] != "m" or len(tokens[k][1]) > 2:
        return False
    at = dates.index(k)
    before = tokens[dates[at - 1]][1] if at > 0 else ""
    after = tokens[dates[at + 1]][1] if at + 1 < len(dates) else ""
    return before.startswith("h") or after.startswith("s")


def _date_code(code: str, when: dt.datetime, language: str, twelve: bool, minute: bool) -> str:
    letter, size = code[0], len(code)
    if letter == "y":
        return f"{when.year % 100:02d}" if size <= 2 else f"{when.year:04d}"
    if letter == "m" and minute:
        return f"{when.minute:02d}" if size == 2 else str(when.minute)
    if letter == "m":
        if size == 1:
            return str(when.month)
        if size == 2:
            return f"{when.month:02d}"
        if size == 3:
            return MONTHS_SHORT[language][when.month - 1]
        if size == 4:
            return MONTHS[language][when.month - 1]
        return MONTHS[language][when.month - 1][0]
    if letter == "d":
        if size == 1:
            return str(when.day)
        if size == 2:
            return f"{when.day:02d}"
        if size == 3:
            return DAYS_SHORT[language][when.weekday()]
        return DAYS[language][when.weekday()]
    if letter == "h":
        hour = (when.hour % 12 or 12) if twelve else when.hour
        return f"{hour:02d}" if size >= 2 else str(hour)
    if letter == "s":
        return f"{when.second:02d}" if size >= 2 else str(when.second)
    return code
