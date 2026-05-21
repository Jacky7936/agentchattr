"""Task-aware agent dispatch based on profile metadata."""

from __future__ import annotations

import re
from typing import Iterable

from agent_profiles import normalize_profile_id


ROLE_KEYWORDS = {
    "orchestrator": ["orchestrate", "dispatch", "triage", "route", "assign", "who", "誰", "不確定", "派誰"],
    "dispatcher": ["dispatch", "triage", "route", "assign", "who", "誰", "不確定", "派誰"],
    "planner": ["plan", "planning", "roadmap", "scope", "requirements", "spec", "規劃", "計畫", "計劃", "需求"],
    "designer": ["ui", "ux", "design", "layout", "wireframe", "mockup", "pencil", "visual", "設計", "畫面", "介面"],
    "architect": [
        "architecture",
        "schema",
        "database",
        "migration",
        "api design",
        "api boundary",
        "endpoint design",
        "boundary",
        "data flow",
        "架構",
        "資料流",
    ],
    "builder": ["implement", "build", "fix", "code", "frontend", "backend", "test", "實作", "修", "修正"],
    "reviewer": ["review", "bug", "regression", "test", "maintainability", "pr", "檢查", "審查", "測試"],
    "researcher": [
        "research",
        "web research",
        "docs",
        "official docs",
        "api docs",
        "regulation",
        "law",
        "latest",
        "compare",
        "scan",
        "summarize",
        "context",
        "legacy",
        "官方文件",
        "法規",
        "法律",
        "最新",
        "文件",
        "整理",
        "研究",
    ],
    "red team": ["red team", "challenge", "risk", "security", "edge case", "abuse", "漏洞", "風險", "權限"],
    "challenger": ["challenge", "risk", "edge case", "contradiction", "反方", "風險", "矛盾"],
    "prototyper": ["prototype", "spike", "demo", "wireframe", "草案", "原型", "多方案"],
}


def select_dispatch_targets(
    text: str,
    profiles: dict[str, dict],
    *,
    active_names: Iterable[str] | None = None,
    max_targets: int = 3,
) -> list[str]:
    """Return agent names whose profile metadata best matches a task."""
    if not text or not profiles or max_targets <= 0:
        return []

    active = {normalize_profile_id(n) for n in active_names} if active_names is not None else None
    scored = []
    for profile_id, profile in profiles.items():
        name = normalize_profile_id(str(profile.get("name") or profile_id))
        if not name:
            continue
        if active is not None and name not in active and normalize_profile_id(profile_id) not in active:
            continue
        score = _score_profile(text, profile)
        if score < 10:
            continue
        role = str(profile.get("role", "")).strip().lower()
        scored.append(
            {
                "name": name,
                "score": score,
                "rank": _profile_rank(profile),
                "is_dispatcher": _is_orchestrator_role(role),
                "role": role,
                "tag_match": _has_trigger_tag(text, profile),
            }
        )

    if not scored:
        fallback = _fallback_dispatcher(profiles, active)
        return [fallback] if fallback else []

    non_dispatchers = [item for item in scored if not item["is_dispatcher"]]
    candidates = _prune_same_role_candidates(non_dispatchers) if non_dispatchers else scored
    candidates.sort(key=lambda item: (-item["score"], item["rank"], item["name"]))
    return [item["name"] for item in candidates[:max_targets]]


def _score_profile(text: str, profile: dict) -> int:
    haystack = _normalize_text(text)
    score = 0

    for tag in _string_list(profile.get("trigger_tags")):
        tag_text = _normalize_text(tag)
        if tag_text and _contains_term(haystack, tag_text):
            score += 12 + min(len(tag_text), 8)

    role = _normalize_text(str(profile.get("role", "")))
    for role_name, keywords in ROLE_KEYWORDS.items():
        if role_name in role:
            for keyword in keywords:
                if _contains_term(haystack, _normalize_text(keyword)):
                    score += 10
    if "reviewer" in role and any(word in haystack for word in ("review", "審查", "檢查")):
        score += 12

    if score > 0:
        profile_terms = " ".join(
            _string_list(profile.get("specialty"))
            + _string_list(profile.get("responsibilities"))
            + _string_list(profile.get("label"))
            + _string_list(profile.get("model"))
        )
        for token in _tokens(profile_terms):
            if len(token) >= 5 and token in haystack:
                score += 1

    return score


def _has_trigger_tag(text: str, profile: dict) -> bool:
    haystack = _normalize_text(text)
    return any(_contains_term(haystack, _normalize_text(tag)) for tag in _string_list(profile.get("trigger_tags")))


def _prune_same_role_candidates(candidates: list[dict]) -> list[dict]:
    by_role: dict[str, list[dict]] = {}
    for item in candidates:
        by_role.setdefault(item["role"], []).append(item)

    pruned = []
    for role_candidates in by_role.values():
        if len(role_candidates) == 1:
            pruned.extend(role_candidates)
            continue
        tagged = [item for item in role_candidates if item["tag_match"]]
        if tagged:
            pruned.extend(tagged)
            continue
        role_candidates.sort(key=lambda item: (-item["score"], item["rank"], item["name"]))
        pruned.append(role_candidates[0])
    return pruned


def _fallback_dispatcher(profiles: dict[str, dict], active: set[str] | None) -> str:
    dispatchers = []
    for profile_id, profile in profiles.items():
        role = str(profile.get("role", "")).strip().lower()
        if not _is_orchestrator_role(role):
            continue
        name = normalize_profile_id(str(profile.get("name") or profile_id))
        if not name:
            continue
        if active is not None and name not in active and normalize_profile_id(profile_id) not in active:
            continue
        dispatchers.append((_profile_rank(profile), name))
    dispatchers.sort()
    return dispatchers[0][1] if dispatchers else ""


def plan_orchestrator_dispatch(
    text: str,
    profiles: dict[str, dict],
    *,
    active_names: Iterable[str] | None = None,
    max_workers: int = 3,
) -> dict[str, object]:
    """Build an auto-dispatch plan with an orchestrator and parallel workers."""
    workers = select_dispatch_targets(
        text,
        profiles,
        active_names=active_names,
        max_targets=max_workers,
    )
    active = {normalize_profile_id(n) for n in active_names} if active_names is not None else None
    orchestrator = _fallback_dispatcher(profiles, active)
    workers = [name for name in workers if name != orchestrator]
    targets = ([orchestrator] if orchestrator else []) + workers
    return {
        "orchestrator": orchestrator,
        "commander": orchestrator,
        "workers": workers,
        "targets": targets,
    }


def plan_commander_dispatch(*args, **kwargs) -> dict[str, object]:
    """Backward-compatible alias for older callers."""
    return plan_orchestrator_dispatch(*args, **kwargs)


def _is_orchestrator_role(role: str) -> bool:
    return role in ("orchestrator", "dispatcher")


def _profile_rank(profile: dict) -> int:
    try:
        return int(profile.get("rank", 50))
    except (TypeError, ValueError):
        return 50


def _string_list(value) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in raw if str(item).strip()]


def _normalize_text(value: str) -> str:
    return str(value).casefold()


def _contains_term(haystack: str, term: str) -> bool:
    if not term:
        return False
    if not _is_ascii_word_term(term):
        return term in haystack
    plural_suffix = "s?" if term[-1:].isalpha() and not term.endswith("s") else ""
    pattern = rf"(?<![a-z0-9_-]){re.escape(term)}{plural_suffix}(?![a-z0-9_-])"
    return re.search(pattern, haystack) is not None


def _is_ascii_word_term(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_ -]*", value))


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9_-]+", _normalize_text(value)))
