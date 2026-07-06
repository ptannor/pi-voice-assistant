"""Hebrew vocalization (nikud/diacritics) via Dicta's free Nakdan API.

Only worth using for phrases with rare/traditional vocabulary that Edge TTS
mispronounces in plain form (confirmed: common everyday words are actually
read *better* in plain form -- see pronunciation.py). Decide per-phrase
whether to call this at all; don't vocalize everything by default.

**Rule of thumb for future phrases**: vocalize anything that's Biblical,
Mishnaic, Talmudic, or otherwise classical/liturgical register -- not just
the specific words we've hit so far. This will matter directly once the
planned Sefaria-sourced daily halacha content (see roadmap) is built: text
pulled from Mishna/Talmud/Tanach should default to going through `vocalize()`
before synthesis, since that's exactly the kind of vocabulary Edge TTS wasn't
trained heavily on and gets wrong in plain form -- the same failure mode
confirmed here for the Shabbat candle-lighting announcement's עישרתן/עירבתן/
הדליקו. Everyday conversational Hebrew (Modern Hebrew, the vocabulary Edge
TTS's training data is mostly made of) should stay in plain form instead.

Dicta (dicta.org.il) is an Israeli academic non-profit; this hits the same
unauthenticated endpoint their own free public web tool (nakdan.dicta.org.il)
calls -- there's no official developer API/terms published, so use this
sparingly (asset-generation time, not on every runtime TTS call) and handle
its unavailability gracefully, same as the Hebcal client.
"""
from __future__ import annotations

import json
import urllib.request

from .pronunciation import WORD_CORRECTIONS

NAKDAN_URL = "https://nakdan-u1-0.loadbalancer.dicta.org.il/api"


class NakdanError(Exception):
    pass


def vocalize(text: str) -> str:
    """Add Hebrew nikud to plain text, using WORD_CORRECTIONS to override
    Nakdan's own top choice for words known to come back wrong.
    """
    payload = json.dumps({"task": "nakdan", "genre": "modern", "data": text}).encode("utf-8")
    request = urllib.request.Request(
        NAKDAN_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            items = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise NakdanError(f"Nakdan API request failed: {exc}") from exc

    parts: list[str] = []
    for item in items:
        word = item.get("word", "")
        if item.get("sep"):
            parts.append(word)
        elif word in WORD_CORRECTIONS:
            parts.append(WORD_CORRECTIONS[word])
        elif item.get("options"):
            parts.append(item["options"][0])
        else:
            parts.append(word)

    return "".join(parts).replace("|", "")
