from __future__ import annotations

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from ..schemas import ClaimDraft, HypothesisDraft, SignalDraft
from ..utils import normalise_text

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。\uFF01\uFF1F])\s+|\n+")
_CAPITALISED_SEQUENCE = re.compile(r"\b(?:[A-Z][A-Za-z0-9&.-]{1,}(?:\s+[A-Z][A-Za-z0-9&.-]{1,}){0,3})\b")
_WORD = re.compile(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣_\-]{1,}")
_NEGATION_CUES = {
    "not",
    "no",
    "never",
    "failed",
    "failure",
    "decline",
    "decrease",
    "contrary",
    "disputed",
    "denied",
    "unsupported",
    "반대",
    "아니다",
    "부인",
    "확인되지",
}
_SIGNAL_RULES: dict[str, tuple[str, ...]] = {
    "hiring": ("hiring", "hire", "recruit", "채용", "인력"),
    "product_release": ("launch", "release", "ship", "product", "출시", "릴리즈", "제품"),
    "funding": ("funding", "investment", "raise", "투자", "펀딩"),
    "regulation": ("regulation", "policy", "law", "compliance", "규제", "정책", "법령", "고시"),
    "incident": ("incident", "outage", "failure", "breach", "장애", "사고", "실패"),
    "risk": ("risk", "bottleneck", "constraint", "shortage", "위험", "병목", "제약", "부족"),
    "governance": ("governance", "permission", "audit", "approval", "거버넌스", "권한", "감사", "승인"),
    "strategy": ("strategy", "strategic", "priority", "shift", "전략", "우선순위", "전환"),
    "demand": ("demand", "customer", "adoption", "usage", "수요", "고객", "도입", "사용"),
    "research": ("research", "paper", "benchmark", "study", "연구", "논문", "벤치마크"),
}
_STOPWORDS = {
    "that",
    "this",
    "with",
    "from",
    "have",
    "will",
    "into",
    "rather",
    "than",
    "because",
    "their",
    "there",
    "about",
    "그리고",
    "대한",
    "위해",
    "있는",
    "하는",
    "한다",
    "에서",
}


class TextAnalysis(BaseModel):
    entities: list[str] = Field(default_factory=list)
    claims: list[ClaimDraft] = Field(default_factory=list)
    signals: list[SignalDraft] = Field(default_factory=list)
    hypotheses: list[HypothesisDraft] = Field(default_factory=list)
    provider: str = "local-heuristic"
    model: str = "rules-v2"
    usage: dict[str, int | float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def split_sentences(text: str) -> list[str]:
    return [
        normalise_text(sentence) for sentence in _SENTENCE_BOUNDARY.split(text) if normalise_text(sentence)
    ]


def chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller")
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        proposed_end = min(start + chunk_size, len(text))
        end = proposed_end
        if proposed_end < len(text):
            window = text[start:proposed_end]
            boundaries = [window.rfind(marker) for marker in (". ", "? ", "! ", "\n", "。")]
            boundary = max(boundaries)
            if boundary >= int(chunk_size * 0.55):
                end = start + boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _extract_entities(text: str) -> list[str]:
    matches = [normalise_text(match) for match in _CAPITALISED_SEQUENCE.findall(text)]
    counts = Counter(match for match in matches if match.casefold() not in {"the", "this"})
    return [entity for entity, _ in counts.most_common(12)]


def _signal_type(sentence: str) -> str | None:
    lowered = sentence.casefold()
    for signal_type, keywords in _SIGNAL_RULES.items():
        if any(keyword in lowered for keyword in keywords):
            return signal_type
    return None


def _stance(sentence: str) -> str:
    tokens = {token.casefold() for token in _WORD.findall(sentence)}
    return "oppose" if tokens & _NEGATION_CUES else "support"


def _score_novelty(sentence: str) -> float:
    lowered = sentence.casefold()
    score = 0.48
    if any(
        cue in lowered
        for cue in ("new", "first", "recent", "increased", "shift", "새로운", "최근", "증가", "전환")
    ):
        score += 0.22
    if any(char.isdigit() for char in sentence):
        score += 0.08
    return min(score, 0.95)


def _score_impact(sentence: str, signal_type: str) -> float:
    score = 0.46
    if signal_type in {"incident", "risk", "regulation", "strategy", "governance"}:
        score += 0.18
    lowered = sentence.casefold()
    if any(
        cue in lowered
        for cue in ("critical", "material", "strategic", "bottleneck", "major", "핵심", "중대한", "병목")
    ):
        score += 0.18
    return min(score, 0.95)


def _theme(sentence: str, signal_type: str) -> str:
    tokens = [
        token.casefold()
        for token in _WORD.findall(sentence)
        if len(token) > 3 and token.casefold() not in _STOPWORDS
    ]
    frequent = [word for word, _ in Counter(tokens).most_common(4)]
    return ", ".join(frequent) if frequent else signal_type.replace("_", " ")


def _hypothesis_for_signal(
    signal: SignalDraft,
    *,
    sentence: str,
    entity: str | None,
) -> HypothesisDraft:
    subject = entity or "the observed organisation or system"
    theme = _theme(sentence, signal.signal_type)
    if signal.signal_type in {"risk", "incident", "regulation", "governance"}:
        claim = (
            f"{subject} may face a material {signal.signal_type.replace('_', ' ')} constraint "
            f"around {theme}, which could delay execution or adoption."
        )
    elif signal.signal_type == "hiring":
        claim = (
            f"{subject} may be building operational capability around {theme}, "
            "suggesting a shift in near-term priorities."
        )
    else:
        claim = (
            f"{subject} may be moving from exploration toward operationalisation of {theme}, "
            "but this requires corroboration from independent evidence."
        )
    return HypothesisDraft(
        claim=claim,
        scope=entity or signal.signal_type,
        confidence=0.38 if signal.stance == "support" else 0.30,
        evidence_signal_summary=signal.summary,
        stance=signal.stance,
        metadata_json={"source_sentence": sentence, "signal_type": signal.signal_type},
    )


def analyse_text(text: str, policy: dict | None = None) -> TextAnalysis:
    policy = policy or {}
    extraction = policy.get("extraction", {})
    min_sentence_length = int(extraction.get("min_sentence_length", 28))
    max_claims = int(extraction.get("max_claims_per_chunk", 12))
    max_signals = int(extraction.get("max_signals_per_chunk", 8))
    max_hypotheses = int(extraction.get("max_hypotheses_per_chunk", 5))

    sentences = [sentence for sentence in split_sentences(text) if len(sentence) >= min_sentence_length]
    entities = _extract_entities(text)
    default_entity = entities[0] if entities else None

    claims: list[ClaimDraft] = []
    signals: list[SignalDraft] = []
    hypotheses: list[HypothesisDraft] = []
    seen_hypotheses: set[str] = set()

    for sentence in sentences[:max_claims]:
        claims.append(
            ClaimDraft(
                text=sentence,
                subject=default_entity,
                confidence=0.58,
                metadata_json={"extractor": "local-heuristic-v2"},
            )
        )

    for sentence in sentences:
        signal_type = _signal_type(sentence)
        if not signal_type:
            continue
        stance = _stance(sentence)
        signal = SignalDraft(
            entity=default_entity,
            signal_type=signal_type,
            summary=sentence,
            novelty_score=_score_novelty(sentence),
            impact_score=_score_impact(sentence, signal_type),
            confidence=0.62,
            stance=stance,
            metadata_json={"extractor": "local-heuristic-v2"},
        )
        signals.append(signal)
        candidate = _hypothesis_for_signal(signal, sentence=sentence, entity=default_entity)
        key = normalise_text(candidate.claim).casefold()
        if key not in seen_hypotheses:
            hypotheses.append(candidate)
            seen_hypotheses.add(key)
        if len(signals) >= max_signals or len(hypotheses) >= max_hypotheses:
            break

    warnings: list[str] = []
    if not signals:
        warnings.append("No rule-backed strategic signal was detected in this chunk.")
    return TextAnalysis(
        entities=entities,
        claims=claims,
        signals=signals,
        hypotheses=hypotheses,
        provider="local-heuristic",
        model="rules-v2",
        usage={"input_characters": len(text), "sentences_considered": len(sentences)},
        warnings=warnings,
        metadata={"deterministic": True},
    )
