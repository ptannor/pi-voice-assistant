"""Known-correct vocalizations for specific words Dicta's Nakdan gets wrong.

Not every phrase needs full vocalization -- confirmed by listening: common,
everyday words (e.g. "דקות"/minutes) are read correctly by Edge TTS in their
normal plain form, and adding nikud can actually make them *worse* (Nakdan's
reconstructed vowel-vav pattern for "דקות" came out as "dakevot" instead of
"dakot"). But sentences using rarer/more traditional vocabulary (e.g. the
Shabbat candle-lighting announcement's עישרתן/עירבתן/הדליקו) are mispronounced
in plain form and *do* need vocalization to come out right.

So: decide per-phrase whether to vocalize at all (see the phrase list where
these are used), and use this dict only to correct specific words within an
already-vocalized phrase where Nakdan's own top choice is still wrong.
"""
from __future__ import annotations

# {plain word: corrected fully-vocalized form, to use instead of Nakdan's
# default top choice when vocalizing a sentence containing this word}
WORD_CORRECTIONS: dict[str, str] = {
    # Nakdan's top choice is the shin ("sh") variant, producing "Isharten"
    # instead of "Isarten". The sin variant is grammatically correct, but
    # keeping its dagesh (gemination mark) produced an "Ish-sarten" artifact
    # when synthesized -- Modern Hebrew doesn't phonemically realize
    # gemination anyway, so dropping it gives the correct "Isarten" sound.
    "עישרתן": "עִשַׂרְתֶּן",
}
