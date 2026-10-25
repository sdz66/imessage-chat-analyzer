"""Session/message statistics for parsed transcripts."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import datetime
from typing import Optional

from .parser import Message, SYSTEM

_BASE = r"\U0001F000-\U0001FAFF\u2190-\u21FF\u2600-\u27BF\u2B00-\u2BFF"
_TONE = r"\U0001F3FB-\U0001F3FF"
_REG = r"\U0001F1E6-\U0001F1FF"
# one emoji cluster: flag pairs, skin-tone modifiers and ZWJ families kept whole
EMOJI_RE = re.compile(
    f"(?:[{_REG}]{2}"
    f"|[{_BASE}{_REG}]\uFE0F?[{_TONE}]?"
    f"(?:\u200D[{_BASE}{_REG}]\uFE0F?[{_TONE}]?)*)"
)
Q_END_RE = re.compile(r"(?:吗|么|呢|吧|啥)[。！~]?$")
Q_WORD_RE = re.compile(r"为什么|怎么|什么|多少|为啥|哪|几[个只道次年]")

DEFAULT_GAP_HOURS = 4.0
NIGHT_START = 23  # 23:00
NIGHT_END = 2     # 02:00 (inclusive hour)


def _is_night(hour: int) -> bool:
    return hour >= NIGHT_START or hour <= NIGHT_END


def _is_question(msg: Message) -> bool:
    for t in msg.text:
        if "？" in t or "?" in t or Q_END_RE.search(t) or Q_WORD_RE.search(t):
            return True
    return False


def _median(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def compute_stats(messages: list[Message], gap_hours: float = DEFAULT_GAP_HOURS,
                  top_emoji: int = 10, name: str = "") -> dict:
    human = [m for m in messages if not m.is_system]
    if not human:
        return {"name": name, "total_messages": 0}

    senders = sorted({m.sender for m in human})
    days = sorted({m.dt.date() for m in human})

    per_sender: dict[str, dict] = {}
    for s in senders:
        mine = [m for m in human if m.sender == s]
        text = [m for m in mine if m.text]
        emoji_counter: Counter = Counter()
        for m in mine:
            for t in m.text:
                for e in EMOJI_RE.findall(t):
                    e = e.strip("\uFE0F")
                    if e:
                        emoji_counter[e] += 1
        per_sender[s] = {
            "messages": len(mine),
            "text_messages": len(text),
            "chars": sum(len(t) for m in text for t in m.text),
            "images": sum(m.images for m in mine),
            "videos": sum(m.videos for m in mine),
            "stickers": sum(m.stickers for m in mine),
            "digital_touch": sum(m.dtouch for m in mine),
            "locations": sum(m.locations for m in mine),
            "replies": sum(1 for m in mine if m.is_reply),
            "questions": sum(1 for m in mine if _is_question(m)),
            "top_emoji": emoji_counter.most_common(top_emoji),
        }

    # reply latency: consecutive human messages switching sender
    latency: dict[str, list[float]] = {s: [] for s in senders}
    for a, b in zip(human, human[1:]):
        gap_min = (b.dt - a.dt).total_seconds() / 60
        if a.sender != b.sender and 0 <= gap_min < gap_hours * 60:
            latency[b.sender].append(gap_min)

    # read receipts (1:1 conversations): "you" = transcript owner reading the
    # other side, "them" = the other side reading the owner
    reads: dict[str, list[float]] = {"you": [], "them": []}
    for m in human:
        if m.read_delay is not None:
            reads[m.read_by].append(m.read_delay / 60)

    # sessions: split on >= gap_hours between consecutive messages
    sessions: list[list[Message]] = []
    cur = [human[0]]
    for prev, nxt in zip(human, human[1:]):
        if (nxt.dt - prev.dt).total_seconds() >= gap_hours * 3600:
            sessions.append(cur)
            cur = []
        cur.append(nxt)
    sessions.append(cur)

    openers: Counter = Counter()
    closers: Counter = Counter()
    night_sessions = 0
    for sess in sessions:
        openers[sess[0].sender] += 1
        closers[sess[-1].sender] += 1
        if any(_is_night(m.dt.hour) for m in sess):
            night_sessions += 1

    first_of_day: dict = {}
    for m in human:
        d = m.dt.date()
        if d not in first_of_day:
            first_of_day[d] = m.sender
    first_of_day_counts = Counter(first_of_day.values())

    longest = max(sessions, key=len)
    per_day = Counter(m.dt.date() for m in human)
    hours = Counter(m.dt.hour for m in human)

    effects: Counter = Counter()
    tapbacks: Counter = Counter()
    for m in human:
        effects.update(m.effects)
        tapbacks.update(m.tapbacks)

    def _fmt_sess(s: list[Message]) -> dict:
        return {
            "count": len(s),
            "start": s[0].dt.isoformat(timespec="minutes"),
            "end": s[-1].dt.isoformat(timespec="minutes"),
            "open": s[0].sender,
            "close": s[-1].sender,
        }

    return {
        "name": name,
        "date_start": days[0].isoformat(),
        "date_end": days[-1].isoformat(),
        "active_days": len(days),
        "total_messages": len(human),
        "per_sender": per_sender,
        "reply_latency_min": {
            s: {"median": _median(v), "mean": _median(v) and statistics.mean(v),
                "n": len(v)}
            for s, v in latency.items()
        },
        "read_delay_min": {
            "owner_read_other_median": _median(reads["you"]),
            "other_read_owner_median": _median(reads["them"]),
        },
        "sessions": {
            "count": len(sessions),
            "gap_hours": gap_hours,
            "openers": dict(openers),
            "closers": dict(closers),
            "night_sessions": night_sessions,
            "avg_len": round(statistics.mean(len(s) for s in sessions), 1),
            "longest": _fmt_sess(longest),
            "first_message_of_day": dict(first_of_day_counts),
        },
        "per_day_top": [[d.isoformat(), c] for d, c in per_day.most_common(10)],
        "hours": {h: hours.get(h, 0) for h in range(24)},
        "effects": dict(effects),
        "tapbacks": dict(tapbacks),
        "first_message": {"sender": human[0].sender,
                          "at": human[0].dt.isoformat(timespec="minutes")},
        "last_message": {"sender": human[-1].sender,
                         "at": human[-1].dt.isoformat(timespec="minutes")},
    }
