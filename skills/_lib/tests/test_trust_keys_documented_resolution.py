"""The reader of a written resolution rule: which resolver a text prescribes
for a config key.

The skills tell a session how to turn a bare config value into a path;
test_trust_keys_documented_resolution_toolkit.py reads those texts and
resolves the real config values exactly as they prescribe. This file holds the
reader itself.
"""

import re

_RESOLVER = re.compile(r"\b(resolve_standard|resolve_relative)\(")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def documented_resolver(text: str, key: str) -> str:
    """The resolver the text prescribes for `key`.

    A sentence that names the key and exactly one resolver wins. Otherwise the
    general rule applies: the sentence about bare filenames and the resolver it
    names. A sentence naming the key beside two resolvers is ambiguous
    instruction and fails rather than guessing.
    """
    sentences = _SENTENCE_END.split(" ".join(text.split()))
    specific = []
    for sentence in sentences:
        if key not in sentence:
            continue
        named = set(_RESOLVER.findall(sentence))
        assert len(named) <= 1, f"ambiguous rule for {key}: {sentence!r}"
        specific.extend(named)
    if specific:
        assert len(set(specific)) == 1, f"conflicting rules for {key}: {sorted(set(specific))}"
        return specific[0]
    general = [
        found
        for sentence in sentences
        if "bare filename" in sentence
        for found in _RESOLVER.findall(sentence)
    ]
    assert general, f"no resolver rule covers {key}"
    assert len(set(general)) == 1, f"conflicting general rules: {sorted(set(general))}"
    return general[0]


def test_the_rule_reader_takes_the_carve_out_over_the_general_join():
    """The reader itself: a general join plus a carve-out sentence yields the
    carve-out for the trust key and the join for everything else."""
    text = (
        "Every bare filename below is joined onto its subkey via `resolve_relative()`. "
        "`trust_ladder_standard` instead takes `resolve_standard()`."
    )
    assert documented_resolver(text, "trust_ladder_standard") == "resolve_standard"
    assert documented_resolver(text, "model_routing_standard") == "resolve_relative"
    assert documented_resolver(text.split(". ")[0] + ".", "trust_ladder_standard") == "resolve_relative"
