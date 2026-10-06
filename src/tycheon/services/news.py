"""News as untrusted data: documents in, bounded numeric signals out.

AGENTS.md: all external text is untrusted, and Tycheon never follows instructions inside it.
This module is where that is made structural rather than hoped for:

* Document text is read **only by deterministic code** in this module (a word-list scorer and
  an injection detector). It is never placed in a prompt, never returned from a tool, and never
  interpreted as a command. What leaves is a sentiment number per document plus flags.
* Documents published after ``as_of`` are excluded (and counted), so a future-dated item cannot
  leak into a past review.
* A document that looks like it is trying to instruct the system is flagged, **excluded from the
  aggregate**, and reported; it does not get to move the number it was trying to move.

The scorer is deliberately simple and says so: this is a bounded, auditable stand-in for a
real sentiment model, not evidence of one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from tycheon.data.asof import as_utc
from tycheon.data.sample import SAMPLE_DATASETS, sample_end
from tycheon.services.schemas import NewsOut, NewsSignal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

MAX_TEXT_CHARS = 20_000

POSITIVE = frozenset(
    [
        "beat",
        "beats",
        "surge",
        "surges",
        "growth",
        "gain",
        "gains",
        "rally",
        "strong",
        "upgrade",
        "upgraded",
        "record",
        "profit",
        "profitable",
        "outperform",
        "improved",
        "improves",
        "robust",
        "resilient",
        "expands",
        "expansion",
    ]
)
NEGATIVE = frozenset(
    [
        "miss",
        "misses",
        "plunge",
        "plunges",
        "decline",
        "declines",
        "loss",
        "losses",
        "weak",
        "downgrade",
        "downgraded",
        "probe",
        "lawsuit",
        "recall",
        "fraud",
        "bankruptcy",
        "default",
        "slump",
        "warns",
        "warning",
        "cut",
        "cuts",
        "layoffs",
    ]
)

# Phrases that address the reader or the system instead of describing the world. Matching one
# does not prove malice; it is enough to refuse to let the document influence a number.
_INJECTION = re.compile(
    r"""
    ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)
    | disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|instructions)
    | (?:system|developer)\s+(?:prompt|message|instructions?)
    | you\s+are\s+now\b
    | act\s+as\s+(?:an?\s+)?(?:admin|system|assistant|trader)
    | </?\s*(?:system|assistant|tool|function)\b
    | \b(?:tool|function)_?call\b
    | \b(?:propose_paper_trade|approve|execute)\b.{0,40}\b(?:trade|order|buy|sell)\b
    | \b(?:api[_\s-]?key|secret|password|token)\b
    | begin\s+(?:system|instructions)
    | override\s+(?:the\s+)?(?:policy|limits?|approval)
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


@dataclass(frozen=True)
class NewsDocument:
    """One document. ``text`` and ``title`` are untrusted."""

    doc_id: str
    symbol: str
    source: str
    published_at: datetime
    title: str
    text: str

    def __post_init__(self) -> None:
        as_utc(self.published_at, "published_at")


class NewsStore(Protocol):
    def documents(self, symbol: str) -> Sequence[NewsDocument]: ...


class InMemoryNewsStore:
    """Documents held in memory; the default store and the one tests inject attackers into."""

    def __init__(self, documents: Sequence[NewsDocument] = ()) -> None:
        self._documents = list(documents)

    def add(self, document: NewsDocument) -> None:
        self._documents.append(document)

    def documents(self, symbol: str) -> list[NewsDocument]:
        return [d for d in self._documents if d.symbol == symbol]


def sanitize(text: str) -> str:
    """Normalise and bound untrusted text before any analysis (no control characters)."""
    normalised = unicodedata.normalize("NFKC", text[:MAX_TEXT_CHARS])
    return "".join(ch for ch in normalised if ch in "\n\t " or unicodedata.category(ch)[0] != "C")


def injection_suspected(text: str) -> bool:
    return _INJECTION.search(sanitize(text)) is not None


def sentiment(text: str) -> float:
    """Word-list sentiment in ``[-1, 1]``: ``(pos - neg) / (pos + neg + 1)``, 0 with no signal."""
    words = re.findall(r"[a-z]+", sanitize(text).lower())
    pos = sum(w in POSITIVE for w in words)
    neg = sum(w in NEGATIVE for w in words)
    return (pos - neg) / (pos + neg + 1)


def news_signals(symbol: str, store: NewsStore, as_of: datetime) -> NewsOut:
    """Numeric signals for ``symbol`` from documents published by ``as_of``."""
    when = as_utc(as_of)
    used: list[NewsSignal] = []
    excluded = 0
    for doc in sorted(store.documents(symbol), key=lambda d: (d.published_at, d.doc_id)):
        if as_utc(doc.published_at) > when:
            excluded += 1
            continue
        flagged = injection_suspected(doc.title) or injection_suspected(doc.text)
        used.append(
            NewsSignal(
                doc_id=doc.doc_id,
                source=doc.source[:40],
                published_at=as_utc(doc.published_at).isoformat(),
                sentiment=sentiment(doc.title + " " + doc.text),
                injection_suspected=flagged,
            )
        )
    clean = [s.sentiment for s in used if not s.injection_suspected]
    return NewsOut(
        symbol=symbol,
        as_of=when.isoformat(),
        n_documents_used=len(used),
        n_excluded_after_as_of=excluded,
        n_injection_suspected=sum(s.injection_suspected for s in used),
        mean_sentiment=sum(clean) / len(clean) if clean else None,
        signals=used,
    )


# --------------------------------------------------------------------- synthetic sample
_TEMPLATES = (
    ("Quarterly results beat expectations", "Revenue growth was strong and profit improved."),
    ("Analyst downgrade after weak guidance", "The company warns of a decline in orders."),
    ("Record expansion announced", "Management expects robust growth and a rally in demand."),
    ("Regulator opens probe", "A probe and a possible lawsuit weigh on the outlook."),
    ("Steady quarter", "Results were in line with the prior period."),
    (
        "Early-morning note after the close",
        "Strong gain reported; published after the review date.",
    ),
)


def sample_news() -> InMemoryNewsStore:
    """Deterministic synthetic news for the bundled sample symbols (not real news).

    Documents are dated relative to each series' last bar. The last one is dated *after* it, so
    a review as of that bar must exclude it.
    """
    docs: list[NewsDocument] = []
    offsets = (-20, -12, -9, -5, -2, 2)
    for symbol in SAMPLE_DATASETS:
        end = sample_end(symbol)
        for i, offset in enumerate(offsets):
            title, text = _TEMPLATES[(i + len(symbol)) % len(_TEMPLATES)]
            docs.append(
                NewsDocument(
                    doc_id=f"{symbol}-N{i + 1}",
                    symbol=symbol,
                    source="synthetic-wire",
                    published_at=end + timedelta(days=offset),
                    title=title,
                    text=text,
                )
            )
    return InMemoryNewsStore(docs)
