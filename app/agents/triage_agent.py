"""Triage Agent.

Classifies an incident into a category, computes priority/severity, and
calculates an SLA deadline. Uses scikit-learn TF-IDF + cosine similarity
against a small in-memory training set — replaceable with a Hugging Face
pipeline later.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.agents.base import BaseAgent

# ============================================================================
# Training corpus — small, hand-curated, easy to extend.
# In production this would come from labelled historical incidents.
# ============================================================================
_TRAINING_DATA: List[Tuple[str, str, str]] = [
    # (text, category, severity)
    ("database connection refused timeout rds mysql postgres", "Database", "high"),
    ("cannot connect to database access denied credentials", "Database", "high"),
    ("query slow performance index missing", "Database", "medium"),
    ("network unreachable vpn dns route firewall", "Network", "high"),
    ("ssl certificate expired tls handshake failure", "Network", "high"),
    ("application 500 error null pointer exception stack trace", "Application", "medium"),
    ("api endpoint 404 not found broken integration", "Application", "medium"),
    ("dashboard slow loading timeout report analytics", "Performance", "medium"),
    ("memory cpu spike high utilization out of memory oom", "Performance", "high"),
    ("disk full storage capacity exceeded", "Infrastructure", "high"),
    ("login failed authentication password reset locked account", "Identity", "low"),
    ("permission denied authorization role access", "Identity", "medium"),
    ("data pipeline failed etl job airflow spark", "Data Pipeline", "high"),
    ("kafka topic offset lag consumer broker", "Data Pipeline", "medium"),
    ("redshift cluster wlm queue concurrency scaling", "Cloud Infrastructure", "high"),
    ("s3 bucket access denied permission policy iam", "Cloud Infrastructure", "medium"),
    ("kubernetes pod crashloopbackoff container terminated", "Infrastructure", "high"),
    ("disk space backup retention purge cleanup", "Infrastructure", "low"),
    ("password expired account locked vpn reset", "Identity", "low"),
    ("printer scanner office equipment hardware", "End User Support", "low"),
]

# Critical-impact keywords — bumps priority regardless of classifier output.
_CRITICAL_KEYWORDS = [
    "production",
    "prod",
    "outage",
    "down",
    "critical",
    "urgent",
    "p1",
    "all users",
    "company-wide",
    "blocker",
    "data loss",
]

# SLA targets (minutes) by priority.
_SLA_MINUTES = {"P1": 60, "P2": 240, "P3": 1440, "P4": 4320}


class TriageAgent(BaseAgent):
    name = "Triage Agent"

    def __init__(self) -> None:
        super().__init__()
        self.vectorizer = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2), max_features=2000
        )
        self._categories = [c for _, c, _ in _TRAINING_DATA]
        self._severities = [s for _, _, s in _TRAINING_DATA]
        self._matrix = self.vectorizer.fit_transform([t for t, _, _ in _TRAINING_DATA])

    # --------------------------------------------------------------------------
    def _classify(self, text: str) -> Tuple[str, str, float]:
        """Return (category, severity, confidence)."""
        if not text.strip():
            return "Uncategorised", "low", 0.5
        vec = self.vectorizer.transform([text])
        sims = cosine_similarity(vec, self._matrix)[0]
        best_idx = int(np.argmax(sims))
        confidence = float(sims[best_idx])
        return self._categories[best_idx], self._severities[best_idx], confidence

    @staticmethod
    def _priority_from(severity: str, text: str) -> str:
        text_l = text.lower()
        critical_hits = sum(1 for kw in _CRITICAL_KEYWORDS if kw in text_l)

        if critical_hits >= 2 or "p1" in text_l:
            return "P1"
        if severity == "high" and critical_hits >= 1:
            return "P1"
        if severity == "high":
            return "P2"
        if severity == "medium":
            return "P3"
        return "P4"

    # --------------------------------------------------------------------------
    def run(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        text = f"{incident.get('subject', '')} {incident.get('description', '')}"
        category, severity, confidence = self._classify(text)
        priority = self._priority_from(severity, text)
        sla_minutes = _SLA_MINUTES[priority]
        sla_deadline = datetime.now() + timedelta(minutes=sla_minutes)

        self.record_step(
            incident_id=incident["id"],
            action="Classified incident",
            output=(
                f"Category: {category} · Severity: {severity} · Priority: {priority} · "
                f"SLA: {sla_minutes}m · Confidence: {confidence:.0%}"
            ),
            step_type="reason",
            metadata={
                "category": category,
                "severity": severity,
                "priority": priority,
                "confidence": confidence,
                "sla_minutes": sla_minutes,
            },
        )

        return {
            "category": category,
            "severity": severity,
            "priority": priority,
            "confidence": confidence,
            "sla_deadline": sla_deadline,
            "status": "analyzing",
        }
