from __future__ import annotations

import re

from .models import TaskSignals

_RISK = (
    "security",
    "authentication",
    "authorization",
    "credential",
    "permission",
    "race condition",
    "concurrency",
    "concurrent",
    "data loss",
    "data integrity",
    "database migration",
    "migration",
    "billing",
    "delete",
    "production",
)
_DEBUG = ("debug", "bug", "broken", "doesn't work", "not working", "root cause", "investigate", "intermittent")
_ARCHITECTURE = ("architecture", "architect", "redesign", "cross-cutting", "system design", "deep refactor")
_BROAD = (
    "whole repo",
    "entire repository",
    "entire codebase",
    "all modules",
    "across modules",
    "many files",
    "large",
    "end to end",
)
_MECHANICAL = ("rename", "reformat", "boilerplate", "typo", "mechanical", "update imports", "bump version")
_READ_ONLY = ("inspect", "list", "summarize", "explain", "compare", "read-only", "read only", "review")
_DOCS = ("document", "documentation", "readme", "changelog", "write docs")
_INDEPENDENT = ("independent", "separate", "parallel", "each module", "each directory", "different modules")
_DEPENDENT = ("sequential", "dependent", "tightly coupled", "same file", "one after another", "single chain")
_URGENT = ("urgent", "time-sensitive", "time sensitive", "quickly", "interactive", "latency", "fast response")
_VERIFY = ("test", "tests", "testable", "read-only", "read only", "inspect", "lint", "typecheck", "compile")


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) for term in terms)


def _task_count(text: str) -> int:
    counts = {"two": 2, "three": 3, "four": 4, "five": 5, "several": 3, "multiple": 2}
    for word, count in counts.items():
        if re.search(rf"\b{word}\b", text):
            return count
    match = re.search(r"\b([2-9])\s+(?:independent\s+)?(?:modules?|directories|packages|services|files|tasks)\b", text)
    return int(match.group(1)) if match else 1


def classify_task(task: str, config: dict | None = None) -> TaskSignals:
    text = " ".join(task.lower().split())
    if not text:
        raise ValueError("task must not be empty")

    read_only = _contains(text, _READ_ONLY) or text.startswith(("what ", "why ", "how "))
    docs = _contains(text, _DOCS)
    mechanical = _contains(text, _MECHANICAL)
    debug = _contains(text, _DEBUG)
    architecture = _contains(text, _ARCHITECTURE)
    risk_terms = [term for term in _RISK if term in text]
    broad = _contains(text, _BROAD) or bool(
        re.search(r"\b(?:multiple|several|many)\s+(?:files|modules|directories|packages)\b", text)
    )
    independent = _contains(text, _INDEPENDENT)
    dependent = _contains(text, _DEPENDENT)
    urgent = _contains(text, _URGENT)
    task_count = _task_count(text)
    cues = (
        read_only or docs or mechanical or debug or architecture or bool(risk_terms) or broad or independent or urgent
    )

    difficulty = 0.32
    ambiguity = 0.28
    scope = 0.25
    risk = 0.12
    verification = 0.55
    parallelism = 0.12
    coordination = 0.28
    interactions = 0.35
    confidence = 0.72 if cues else 0.32
    evidence: list[str] = []

    if read_only:
        difficulty, verification = 0.22, 0.90
        evidence.append("read-only or inspection task")
    if docs:
        difficulty, ambiguity, verification = min(difficulty, 0.24), 0.18, 0.85
        evidence.append("documentation task")
    if mechanical:
        difficulty, ambiguity, verification = max(difficulty, 0.30), 0.20, 0.80
        evidence.append("mechanical change")
    if debug:
        difficulty, ambiguity, interactions = 0.66, 0.68, 0.68
        evidence.append("debugging or root-cause language")
    if architecture:
        difficulty, ambiguity, scope, interactions = 0.78, 0.62, 0.70, 0.75
        evidence.append("architecture or deep-refactor language")
    if risk_terms:
        risk = min(0.96, 0.70 + 0.04 * len(risk_terms))
        difficulty, ambiguity, verification = max(difficulty, 0.72), max(ambiguity, 0.52), min(verification, 0.38)
        evidence.append("risk indicators: " + ", ".join(risk_terms[:3]))
    if broad:
        scope, difficulty, interactions = 0.84, max(difficulty, 0.60), max(interactions, 0.60)
        evidence.append("broad repository scope")
    if independent and task_count >= 2:
        scope = max(scope, min(0.90, 0.45 + 0.10 * task_count))
        parallelism, coordination = 0.82, 0.22
        evidence.append(f"{task_count} independent work areas")
    if dependent:
        parallelism, coordination = 0.10, 0.88
        evidence.append("ordered or shared-state dependencies")
    if urgent:
        latency = 0.92
        evidence.append("latency-sensitive wording")
    else:
        latency = 0.22
    if _contains(text, _VERIFY):
        verification = max(verification, 0.82)
        evidence.append("verification appears straightforward")

    if re.search(r"\b(?:implement|add|build|fix|change|update|refactor)\b", text):
        difficulty = max(difficulty, 0.42)
        interactions = max(interactions, 0.45)
    if len(text.split()) < 4 and not cues:
        confidence = 0.25
        ambiguity = max(ambiguity, 0.55)
        evidence.append("short task description leaves intent unclear")
    elif not cues:
        confidence = 0.42
        evidence.append("limited task-specific signals")
    if task_count < 2 or not independent:
        parallelism = max(parallelism, 0.10)

    # Keep estimates distinct: ambiguity and scope do not automatically imply parallel work.
    return TaskSignals(
        difficulty=round(difficulty, 2),
        ambiguity=round(ambiguity, 2),
        scope=round(scope, 2),
        risk=round(risk, 2),
        verification_ease=round(verification, 2),
        parallelism=round(parallelism, 2),
        coordination_overhead=round(coordination, 2),
        latency_sensitivity=round(latency, 2),
        expected_interactions=round(interactions, 2),
        confidence=round(confidence, 2),
        read_only=read_only,
        independent_tasks=task_count if independent else 1,
        evidence=tuple(evidence or ("no strong task-specific signals",)),
    )
