"""Language codes and the support tiers promised to users.

Codes are BCP-47 style tags. The region or script subtag is kept, because it matters to translators and
voices (``zh-TW`` is Traditional Chinese, ``pt-BR`` is Brazilian Portuguese). Support tiers are decided by
the base language only (:func:`base`).
"""

from __future__ import annotations

import re

# Target languages whose output (translation, voice, subtitles) is checked by hand before a release.
VERIFIED_TARGETS: frozenset[str] = frozenset({"vi", "en", "zh"})

# Languages offered in the web UI's pickers (any valid code still works). Their display names live with the
# other UI text, in erasedub.webui.strings (``lang.<code>``).
LISTED_LANGUAGES: tuple[str, ...] = ("vi", "en", "zh", "ja", "ko", "th", "id", "es", "fr", "de", "pt", "ru")

# BCP-47 subset: language[-Script][-REGION][-variant...]; extensions and private use are not accepted.
_TAG = re.compile(
    r"^(?P<language>[a-z]{2,3})"
    r"(?:-(?P<script>[a-z]{4}))?"
    r"(?:-(?P<region>[a-z]{2}|[0-9]{3}))?"
    r"(?P<variants>(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3}))*)$"
)
_BAD_CODE = "not a language code (expected something like 'vi', 'en-US' or 'zh-TW')"


def normalize(code: str) -> str:
    """Normalize a language tag, e.g. ``"ZH-tw"`` -> ``"zh-TW"``, ``"en_us"`` -> ``"en-US"``.

    The language subtag is lower-cased, a script subtag title-cased (``zh-Hant``) and a region subtag
    upper-cased. Raises ``ValueError`` for anything else, without echoing the input.
    """
    match = _TAG.match(code.strip().replace("_", "-").lower())
    if match is None:
        raise ValueError(_BAD_CODE)
    out = match["language"]
    if match["script"]:
        out += "-" + match["script"].title()
    if match["region"]:
        out += "-" + match["region"].upper()
    return out + match["variants"]


def base(code: str) -> str:
    """The language subtag only: ``"zh-TW"`` -> ``"zh"``."""
    return normalize(code).split("-", 1)[0]


def is_verified_target(code: str) -> bool:
    """True if ``code`` is a target language checked by hand for each release (any region or script)."""
    return base(code) in VERIFIED_TARGETS
