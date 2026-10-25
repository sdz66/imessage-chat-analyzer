"""Parser for transcripts produced by ReagentX/imessage-exporter (txt format).

Block layout in the source format::

    Jan 9, 2026  7:30:00 PM (Read by them after 2 minutes, 30 seconds)
    <sender>                      # "Me" or a contact name
    <body line 1>                 # until the next blank line
    <body line 2>

System events (background change, unsent, ...) put the event text on the
timestamp line itself. Quoted reply context and tapback attributions are
indented with 4 spaces. "This message responded to an earlier message." marks
a reply. Effects appear as body lines "Sent with <Effect>".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

TS_RE = re.compile(
    r"^([A-Z][a-z]{2} \d{1,2}, \d{4})\s+(\d{1,2}:\d{2}:\d{2}) (AM|PM)"
    r"(?: \(Read by (you|them) after ([^)]*)\))?\s*$"
)
READ_DUR_RE = re.compile(r"(\d+)\s*(day|hour|minute|second)s?")
EDITED_TS_RE = re.compile(
    r"^[A-Z][a-z]{2} \d{1,2}, \d{4}\s+\d{1,2}:\d{2}:\d{2} (?:AM|PM)\s+"
)
TAPBACK_BY_RE = re.compile(r"^\s*(Loved|Liked|Disliked|Laughed|Emphasized|Questioned)\s+by\s+", re.I)
ATTACH_TOKEN_RE = re.compile(
    r"[^\s/]+\.(heic|png|jpg|jpeg|gif|webp|tiff|mov|mp4|caf|m4a|m4v|vcf)\b", re.I
)
VIDEO_EXTS = {"mov", "mp4", "caf", "m4a", "m4v"}
UNIT_SECONDS = {"day": 86400, "hour": 3600, "minute": 60, "second": 1}

ME = "Me"
SYSTEM = "SYSTEM"
REPLY_MARK = "This message responded to an earlier message."
# match on the stable suffix so any macOS username / iOS backup root works
ATTACH_SUFFIX = "/Library/Messages/Attachments/"
STICKER_SUFFIX = "/Library/Messages/StickerCache/"


@dataclass
class Message:
    dt: datetime
    sender: str
    text: list[str] = field(default_factory=list)
    images: int = 0
    videos: int = 0
    stickers: int = 0
    dtouch: int = 0
    locations: int = 0
    effects: list[str] = field(default_factory=list)
    tapbacks: list[str] = field(default_factory=list)  # Loved/Liked/...
    is_reply: bool = False
    read_by: Optional[str] = None  # "you" | "them"
    read_delay: Optional[float] = None  # seconds

    @property
    def is_system(self) -> bool:
        return self.sender == SYSTEM

    @property
    def has_media(self) -> bool:
        return bool(self.images or self.videos or self.stickers or self.dtouch or self.locations)


def parse_read_dur(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    total = 0.0
    for num, unit in READ_DUR_RE.findall(raw):
        total += int(num) * UNIT_SECONDS[unit.lower()]
    return total or None


def _classify_line(line: str, msg: Message, tapback_pending: list[bool]) -> None:
    s = line.strip()
    if not s:
        return
    if line[:1] in (" ", "\t"):
        # indented lines are quoted reply context (or tapback attributions)
        m = TAPBACK_BY_RE.match(line)
        if m and tapback_pending[0]:
            msg.tapbacks.append(m.group(1).capitalize())
        return
    if TAPBACK_BY_RE.match(line):
        msg.tapbacks.append(TAPBACK_BY_RE.match(line).group(1).capitalize())
        return
    if s == "Tapbacks:":
        tapback_pending[0] = True
        return
    if s == REPLY_MARK:
        msg.is_reply = True
        return
    tapback_pending[0] = False

    if ATTACH_SUFFIX in s:
        ext = s.rsplit(".", 1)[-1].lower()
        if ext in VIDEO_EXTS:
            msg.videos += 1
        else:
            msg.images += 1
        return
    if STICKER_SUFFIX in s or "Sticker from" in s or s.startswith("Normal Sticker from"):
        msg.stickers += 1
        return
    if s.startswith("Digital Touch"):
        msg.dtouch += 1
        return
    if s.startswith("Sent with "):
        msg.effects.append(s[len("Sent with "):].strip())
        return
    if "https://maps.apple.com" in s:
        msg.locations += 1
        return
    if ATTACH_TOKEN_RE.fullmatch(s):
        ext = s.rsplit(".", 1)[-1].lower()
        if ext in VIDEO_EXTS:
            msg.videos += 1
        else:
            msg.images += 1
        return

    # plain text; strip bare attachment tokens embedded in the line, and the
    # leading timestamp that imessage-exporter prepends to edited messages
    cleaned = EDITED_TS_RE.sub("", s)
    tokens = ATTACH_TOKEN_RE.findall(cleaned)
    if tokens:
        stripped = ATTACH_TOKEN_RE.sub("", cleaned).strip()
        if stripped:
            msg.text.append(stripped)
        for tok_ext in tokens:
            if tok_ext.lower() in VIDEO_EXTS:
                msg.videos += 1
            else:
                msg.images += 1
        return
    msg.text.append(cleaned)


def parse_file(path: str) -> list[Message]:
    """Parse one imessage-exporter txt transcript into Message records."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")

    messages: list[Message] = []
    i = 0
    n = len(lines)
    while i < n:
        m = TS_RE.match(lines[i])
        if not m:
            i += 1
            continue
        date_s, time_s, ampm, read_by, read_dur = m.groups()
        dt = datetime.strptime(f"{date_s} {time_s} {ampm}", "%b %d, %Y %I:%M:%S %p")
        trailing = re.sub(r"\s*\(Read by [^)]*\)\s*$", "", lines[i][m.end(3):]).strip()
        i += 1

        if trailing:  # system event on the timestamp line itself
            messages.append(Message(dt=dt, sender=SYSTEM, text=[trailing]))
            continue

        msg = Message(dt=dt, sender=SYSTEM, read_by=read_by,
                      read_delay=parse_read_dur(read_dur))
        if i >= n or lines[i].strip() == "":
            # timestamp with no sender line: nothing to record
            continue
        msg.sender = lines[i].strip()  # "Me" or contact / group name
        i += 1

        tapback_pending = [False]
        while i < n and lines[i].strip() != "":
            _classify_line(lines[i], msg, tapback_pending)
            i += 1
        messages.append(msg)
    return messages


def human_senders(messages: list[Message]) -> list[str]:
    """Sender labels excluding SYSTEM, ordered by volume (desc)."""
    counts: dict[str, int] = {}
    for m in messages:
        if not m.is_system:
            counts[m.sender] = counts.get(m.sender, 0) + 1
    return sorted(counts, key=counts.get, reverse=True)  # type: ignore[arg-type]
