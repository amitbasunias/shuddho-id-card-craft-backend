# -*- coding: utf-8 -*-
"""
Unicode Bengali -> Bijoy / SutonnyMJ (ANSI) encoder.

Thin wrapper around avro-py's ``to_bijoy`` (https://pypi.org/project/avro-py/),
with a normalisation step it does not perform on its own:

  1. NFC composition (precomposes ো / ৌ and other canonically composable forms).
  2. Explicit composition of the three Bengali nukta letters (ড়, ঢ়, য়).
     These are Unicode *composition exclusions*, so NFC will not compose them.
     Without this step, input that arrives in decomposed form (e.g. য + ় from
     many soft keyboards) leaves the raw nukta U+09BC in the output, which
     renders as garbage under a Bijoy font.

Public API (unchanged):
    unicode_to_bijoy(text)       -> str
    unicode_to_bijoy_ansi(text)  -> str   # backwards-compatible alias
"""

import unicodedata

import avro

# Composition table for Bengali nukta letters (composition exclusions).
# Written with explicit Unicode escapes so editor/IDE normalisation cannot
# silently collapse the decomposed pattern into the precomposed form on save.
_NUKTA_COMPOSE = (
    ("ড়", "ড়"),  # ড + ় → ড়
    ("ঢ়", "ঢ়"),  # ঢ + ় → ঢ়
    ("য়", "য়"),  # য + ় → য়
)


def _canonicalise(text):
    text = unicodedata.normalize("NFC", text)
    for src, dst in _NUKTA_COMPOSE:
        if src in text:
            text = text.replace(src, dst)
    return text


def _adapt_for_sutonnymj(bijoy):
    # avro emits a few code points that are correct for some Bijoy fonts but
    # not for SutonnyMJ. Each substitution targets a single glyph slot that
    # avro only produces in one well-defined context:
    #   U+201C  ("ru" u-kar special glyph, র+ু / দ্রু) → U+00E6
    #   U+00AB  (্র ro-fola, e.g. প্র)               → U+00D6
    bijoy = bijoy.replace("“", "æ")
    bijoy = bijoy.replace("«", "Ö")
    return bijoy


def unicode_to_bijoy(text):
    """Convert Unicode Bengali text to its Bijoy / SutonnyMJ ANSI byte form.

    Non-Bengali characters pass through unchanged.
    """
    if not text:
        return text
    return _adapt_for_sutonnymj(avro.to_bijoy(_canonicalise(text)))


unicode_to_bijoy_ansi = unicode_to_bijoy
