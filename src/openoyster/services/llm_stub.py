from __future__ import annotations

from ..schemas import ClaimDraft, EntityDraft, HypothesisDraft, SignalDraft, TextAnalysis
from ..utils import normalise_text
from .chunking import split_sentences


def stub_analysis(text: str, index: int) -> TextAnalysis:
    sentences = split_sentences(text) or [normalise_text(text)]
    first_sentence = sentences[0] if sentences else ""
    first_word = _first_word(text)
    stance = "oppose" if "반대" in text or "no evidence" in text.casefold() else "support"
    claims = [ClaimDraft(text=sentence, confidence=0.6) for sentence in sentences[:3] if sentence]
    signal = SignalDraft(
        entity=first_word,
        signal_type="observation",
        summary=first_sentence,
        novelty_score=0.7,
        impact_score=0.7,
        confidence=0.6,
        stance=stance,
    )
    hypothesis = HypothesisDraft(
        claim=f"Stub hypothesis: {first_sentence[:60]}",
        scope=first_word,
        confidence=0.6,
        evidence_signal_summary=first_sentence,
        stance=stance,
        quoted_evidence=first_sentence,
    )
    return TextAnalysis(
        entities=[EntityDraft(name=first_word, kind="other")],
        claims=claims,
        signals=[signal],
        hypotheses=[hypothesis],
        provider="stub",
        model="test-double",
        usage={"input_characters": len(text)},
        metadata={"test_double": True, "chunk_index": index},
    )


def _first_word(text: str) -> str:
    for line in text.splitlines():
        words = line.strip().split()
        if words:
            return words[0]
    return "chunk"
