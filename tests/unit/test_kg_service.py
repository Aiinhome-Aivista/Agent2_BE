"""Unit tests for the Knowledge Graph service logic.

Tests the pure-Python helpers and the matching/scoring algorithms without
touching the database — we use monkeypatching to stub the repository.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.services import knowledge_graph_service as kgs


# ============================================================================
# Keyword extraction
# ============================================================================
class TestKeywordExtraction:
    def test_drops_stopwords(self) -> None:
        kw = kgs._extract_keywords("The user cannot login to SAP")
        assert "the" not in kw
        assert "cannot" in kw or "login" in kw or "user" in kw
        assert "sap" in kw

    def test_drops_short_tokens(self) -> None:
        kw = kgs._extract_keywords("a b ci of an as VPN")
        # Min length is 3
        assert all(len(w) >= 3 for w in kw)
        assert "vpn" in kw

    def test_dedups(self) -> None:
        kw = kgs._extract_keywords("sap sap sap database database")
        assert kw.count("sap") == 1
        assert kw.count("database") == 1

    def test_max_n_cap(self) -> None:
        text = " ".join(f"keyword{i}" for i in range(50))
        kw = kgs._extract_keywords(text, max_n=10)
        assert len(kw) <= 10


# ============================================================================
# find_resolution_for_incident — best-match scoring (the bug fix)
# ============================================================================
class TestFindResolutionBestMatch:
    """The pre-fix code returned the FIRST candidate that cleared confidence.
    The fixed code scores every candidate by overlap × confidence × log1p(successes)
    and returns the highest scorer. These tests verify the new ranking."""

    def _build_fixture(
        self, monkeypatch: pytest.MonkeyPatch, resolutions: List[Dict[str, Any]],
    ) -> None:
        """Wire up a fake repo with one symptom → one cause → many resolutions."""
        symptom = {
            "id": "SYM-1", "node_type": "symptom",
            "keywords": ["sap", "down", "application"],
            "_match_overlap": 3, "_match_ratio": 0.6,
        }
        cause = {"id": "CAU-1", "node_type": "cause", "keywords": []}

        def fake_find_symptom_by_keywords(**kwargs: Any) -> List[Dict[str, Any]]:
            return [symptom]

        def fake_edges_from(node_id: str, edge_type: Optional[str] = None) -> List[Dict[str, Any]]:
            if node_id == "SYM-1" and edge_type == "caused_by":
                return [{"dst_node_id": "CAU-1"}]
            if node_id == "CAU-1" and edge_type == "resolved_by":
                return [{"dst_node_id": r["id"]} for r in resolutions]
            return []

        def fake_find_node(node_id: str) -> Optional[Dict[str, Any]]:
            if node_id == "CAU-1":
                return cause
            for r in resolutions:
                if r["id"] == node_id:
                    return r
            return None

        repo = kgs.KnowledgeGraphRepository
        monkeypatch.setattr(repo, "find_symptom_by_keywords", staticmethod(fake_find_symptom_by_keywords))
        monkeypatch.setattr(repo, "edges_from", staticmethod(fake_edges_from))
        monkeypatch.setattr(repo, "find_node", staticmethod(fake_find_node))

    def test_picks_proven_over_untested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A resolution with the same confidence but 50 successes outranks one with 0."""
        self._build_fixture(monkeypatch, [
            {"id": "RES-untested", "confidence": 0.7, "success_count": 0, "failure_count": 0},
            {"id": "RES-proven",   "confidence": 0.7, "success_count": 50, "failure_count": 1},
        ])
        result = kgs.KnowledgeGraphService.find_resolution_for_incident(
            {"subject": "sap is down", "description": "the application is down"},
            min_overlap=3, min_confidence=0.55,
        )
        assert result is not None
        assert result["resolution"]["id"] == "RES-proven"

    def test_picks_high_confidence_over_low(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A high-confidence resolution outranks a low-confidence peer when neither is proven."""
        self._build_fixture(monkeypatch, [
            {"id": "RES-low",  "confidence": 0.6, "success_count": 0, "failure_count": 0},
            {"id": "RES-high", "confidence": 0.95, "success_count": 0, "failure_count": 0},
        ])
        result = kgs.KnowledgeGraphService.find_resolution_for_incident(
            {"subject": "sap is down", "description": "the application is down"},
            min_overlap=3, min_confidence=0.55,
        )
        assert result is not None
        assert result["resolution"]["id"] == "RES-high"

    def test_filters_below_confidence_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Resolutions below min_confidence are excluded even if highest-ranked."""
        self._build_fixture(monkeypatch, [
            {"id": "RES-tooLow", "confidence": 0.30, "success_count": 99, "failure_count": 1},
            {"id": "RES-pass",   "confidence": 0.60, "success_count": 0, "failure_count": 0},
        ])
        result = kgs.KnowledgeGraphService.find_resolution_for_incident(
            {"subject": "sap is down", "description": "the application is down"},
            min_overlap=3, min_confidence=0.55,
        )
        assert result is not None
        assert result["resolution"]["id"] == "RES-pass"

    def test_returns_none_when_no_keywords(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = kgs.KnowledgeGraphService.find_resolution_for_incident(
            {"subject": "x", "description": ""},  # too short → 0 keywords
            min_overlap=3,
        )
        assert result is None


# ============================================================================
# _derive_cause prefers Mistral analysis when present
# ============================================================================
class TestDeriveCause:
    def test_uses_mistral_root_cause_when_available(self) -> None:
        incident = {
            "_mistral_analysis": {"root_cause": "OOM killer terminated the listener"},
            "category": "Database",
        }
        label, _ = kgs._derive_cause(incident, "we restarted the listener")
        assert "OOM" in label

    def test_falls_back_to_first_sentence_of_notes(self) -> None:
        incident = {"category": "Database"}
        label, _ = kgs._derive_cause(
            incident,
            "The pool was exhausted. We then drained it. Then we restarted.",
        )
        assert "pool" in label.lower() and "exhausted" in label.lower()

    def test_generic_label_when_nothing_provided(self) -> None:
        incident = {"category": "Network"}
        label, _ = kgs._derive_cause(incident, None)
        assert "Network" in label
