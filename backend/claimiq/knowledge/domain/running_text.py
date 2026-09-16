"""Running headers, footers and watermarks in standard-form PDFs.

Published standard forms repeat text on every page: the form's title, a
page-numbered footer, and in some distributed copies a licensing watermark.
Left in, that text is embedded into every chunk, competes with the provision
itself in lexical ranking, and — for a watermark — is quoted back as if it were
contract text.

Detection is by repetition across pages, not by a list of known phrases, so
it works for any form and any distributor's watermark.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import FrozenSet, Iterable, Sequence

#: A line on at least this share of content pages is running text.
DEFAULT_MIN_SHARE = 0.5

#: And on at least this many pages, so a short document's genuinely repeated
#: provision is not stripped.
DEFAULT_MIN_PAGES = 5

#: Longer lines are provision text even when repeated.
MAX_RUNNING_LINE_CHARS = 200

#: Digits are masked only in lines this short. Footers ("General Conditions 95")
#: are short; provision lines that differ only by a clause number are not
#: page furniture and must not collapse into one.
DIGIT_MASK_MAX_CHARS = 60


def normalise_line(line: str) -> str:
    """Case-fold and collapse whitespace; mask digits in short lines.

    Masking lets "General Conditions 95" and "General Conditions 96" count as
    the same footer, without making "Sub-Clause 20.1 ..." and "Sub-Clause
    20.2 ..." the same line.
    """
    collapsed = re.sub(r"\s+", " ", line).strip().lower()
    if len(collapsed) <= DIGIT_MASK_MAX_CHARS:
        return re.sub(r"\d+", "#", collapsed)
    return collapsed


def find_running_lines(
    page_texts: Sequence[str],
    *,
    min_share: float = DEFAULT_MIN_SHARE,
    min_pages: int = DEFAULT_MIN_PAGES,
) -> FrozenSet[str]:
    """Normalised lines that repeat across enough pages to be page furniture.

    Each line counts once per page. Lines that are only digits and punctuation
    (bare page numbers) are always treated as running text once they repeat.
    """
    pages = [text for text in page_texts if text and text.strip()]
    if len(pages) < min_pages:
        return frozenset()

    counts: Counter[str] = Counter()
    for text in pages:
        seen = {
            normalise_line(line)
            for line in text.splitlines()
            if line.strip() and len(line.strip()) <= MAX_RUNNING_LINE_CHARS
        }
        counts.update(seen)

    threshold = max(min_pages, math.ceil(min_share * len(pages)))
    return frozenset(
        line
        for line, count in counts.items()
        if line and count >= threshold
    )


def strip_running_lines(text: str, running: Iterable[str]) -> str:
    """Remove running lines from ``text``, keeping everything else verbatim.

    Only whole lines are removed, and matching is on the normalised form, so
    provision text that merely contains the same words is untouched.
    """
    running_set = running if isinstance(running, (set, frozenset)) else frozenset(running)
    if not running_set or not text:
        return text
    kept = [line for line in text.splitlines() if normalise_line(line) not in running_set]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
