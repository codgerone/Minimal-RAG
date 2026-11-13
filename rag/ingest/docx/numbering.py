"""Automatic numbering of Word paragraphs, computed from numbering.xml (docs/rules/docx.md).

Counters are shared per abstract numbering and level; a `w:num` with a start override counts on its
own from the override. Using a level advances it (its `w:start` the first time) and resets the
deeper levels that restart after it (`w:lvlRestart`). The label is `w:lvlText` with `%1`…`%9`
replaced by each level's current value in that level's format (decimal for every level when the
used level is `w:isLgl`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

FORMATS = ("decimal", "decimalZero", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman",
           "chineseCounting", "chineseCountingThousand", "ideographTraditional",
           "decimalEnclosedCircle", "bullet", "none")
_DIGITS = "零一二三四五六七八九"
_STEMS = "甲乙丙丁戊己庚辛壬癸"


def _attr(element, name: str) -> str | None:
    return None if element is None else element.get(f"{W}{name}")


def _int(element, default: int | None = None) -> int | None:
    value = _attr(element, "val")
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Level:
    start: int
    fmt: str
    text: str | None          # w:lvlText; None: no label
    restart: int | None       # w:lvlRestart; None: after any shallower level
    legal: bool
    style: str | None         # paragraph style linked to the level


@dataclass(frozen=True)
class Label:
    text: str                 # the numbering text as Word shows it (suffix not included)
    bullet: bool


def _level(lvl) -> Level:
    text = lvl.find(f"{W}lvlText")
    return Level(_int(lvl.find(f"{W}start"), 0) or 0, _attr(lvl.find(f"{W}numFmt"), "val") or "decimal",
                 _attr(text, "val") if text is not None else None,
                 _int(lvl.find(f"{W}lvlRestart")), lvl.find(f"{W}isLgl") is not None
                 and _attr(lvl.find(f"{W}isLgl"), "val") not in ("0", "false"),
                 _attr(lvl.find(f"{W}pStyle"), "val"))


def _roman(value: int) -> str:
    out = ""
    for amount, letters in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while value >= amount:
            out += letters
            value -= amount
    return out


def _chinese(value: int) -> str:
    """一 … 九, 十, 十一, 二十, 一百零五, 一千二百: counting numerals below 10 000."""
    if value <= 0 or value >= 10000:
        return str(value)
    parts, zero = [], False
    for unit, name in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit, value = divmod(value, unit)
        if digit:
            if zero and parts:
                parts.append("零")
            parts.append(("" if unit == 10 and not parts and digit == 1 else _DIGITS[digit]) + name)
            zero = False
        elif parts:
            zero = True
    return "".join(parts)


def format_value(value: int, fmt: str) -> str | None:
    """The value in a numbering format; None for a format not supported."""
    if fmt == "decimal":
        return str(value)
    if fmt == "decimalZero":
        return f"{value:02d}" if 0 <= value < 10 else str(value)
    if fmt in ("lowerLetter", "upperLetter"):
        if value <= 0:
            return str(value)
        letters = chr(ord("a") + (value - 1) % 26) * ((value - 1) // 26 + 1)
        return letters if fmt == "lowerLetter" else letters.upper()
    if fmt in ("lowerRoman", "upperRoman"):
        if value <= 0:
            return str(value)
        return _roman(value) if fmt == "lowerRoman" else _roman(value).upper()
    if fmt in ("chineseCounting", "chineseCountingThousand"):
        return _chinese(value)
    if fmt == "ideographTraditional":
        return _STEMS[(value - 1) % 10] if value > 0 else str(value)
    if fmt == "decimalEnclosedCircle":
        return chr(0x2460 + value - 1) if 1 <= value <= 20 else str(value)
    if fmt == "none":
        return ""
    return None


@dataclass
class Numbering:
    """The numbering definitions of one file, and the counters of one pass over its paragraphs."""
    abstracts: dict[str, dict[int, Level]] = field(default_factory=dict)
    nums: dict[str, tuple[str, dict[int, int], dict[int, Level]]] = field(default_factory=dict)
    counters: dict[tuple[str, str], dict[int, int]] = field(default_factory=dict)
    unknown_formats: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, xml: bytes | None, style_numbers: dict[str, str] | None = None) -> "Numbering":
        """`style_numbers`: numbering style ID → its numId, to follow `w:numStyleLink`."""
        numbering = cls()
        if not xml:
            return numbering
        root = etree.fromstring(xml)
        links: dict[str, str] = {}
        for abstract in root.iter(f"{W}abstractNum"):
            ident = _attr(abstract, "abstractNumId") or ""
            numbering.abstracts[ident] = {int(_attr(lvl, "ilvl") or 0): _level(lvl)
                                          for lvl in abstract.findall(f"{W}lvl")}
            link = abstract.find(f"{W}numStyleLink")
            if link is not None and not numbering.abstracts[ident]:
                links[ident] = _attr(link, "val") or ""
        for num in root.iter(f"{W}num"):
            abstract = _attr(num.find(f"{W}abstractNumId"), "val") or ""
            starts: dict[int, int] = {}
            levels: dict[int, Level] = {}
            for override in num.findall(f"{W}lvlOverride"):
                ilvl = int(_attr(override, "ilvl") or 0)
                start = override.find(f"{W}startOverride")
                if start is not None:
                    starts[ilvl] = _int(start, 0) or 0
                lvl = override.find(f"{W}lvl")
                if lvl is not None:
                    levels[ilvl] = _level(lvl)
            numbering.nums[_attr(num, "numId") or ""] = (abstract, starts, levels)
        # An abstract numbering that only links to a numbering style takes that style's levels.
        for ident, style in links.items():
            target = (style_numbers or {}).get(style)
            if target in numbering.nums:
                numbering.abstracts[ident] = numbering.abstracts.get(numbering.nums[target][0], {})
        return numbering

    def fresh(self) -> "Numbering":
        """The same definitions with no number used yet."""
        return Numbering(self.abstracts, self.nums)

    def levels(self, num_id: str) -> dict[int, Level] | None:
        if num_id not in self.nums:
            return None
        abstract, _, overrides = self.nums[num_id]
        return {**self.abstracts.get(abstract, {}), **overrides}

    def level_for_style(self, num_id: str, style_id: str) -> int | None:
        """The level of the numbering linked to a paragraph style (its `w:pStyle`)."""
        for ilvl, level in sorted((self.levels(num_id) or {}).items()):
            if level.style == style_id:
                return ilvl
        return None

    def label(self, num_id: str, ilvl: int) -> Label | None:
        """Advance the counters for a paragraph numbered (num_id, ilvl) and give its label;
        None when the numbering does not exist or writes no label."""
        levels = self.levels(num_id)
        if not levels or ilvl not in levels:
            return None
        abstract, starts, _ = self.nums[num_id]
        key = ("num", num_id) if starts else ("abstract", abstract)
        counts = self.counters.setdefault(key, {})

        def start(level: int) -> int:
            return starts.get(level, levels[level].start if level in levels else 0)

        counts[ilvl] = counts[ilvl] + 1 if ilvl in counts else start(ilvl)
        for deeper in [level for level in counts if level > ilvl]:
            restart = levels[deeper].restart if deeper in levels else None
            if restart is None or (restart != 0 and ilvl < restart):
                del counts[deeper]
        level = levels[ilvl]
        if level.text is None:
            return None
        if level.fmt == "bullet":
            return Label(level.text, True)
        if level.fmt not in FORMATS:
            self.unknown_formats.add(level.fmt)

        def value(match: re.Match) -> str:
            index = int(match.group(1)) - 1
            if index not in levels:
                return ""
            fmt = "decimal" if level.legal else levels[index].fmt
            current = counts.get(index, start(index))
            if fmt == "bullet":
                return ""
            written = format_value(current, fmt)
            return str(current) if written is None else written
        return Label(re.sub(r"%([1-9])", value, level.text), False)
