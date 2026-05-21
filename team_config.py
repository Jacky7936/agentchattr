"""Default model-specialized agent team for agentchattr."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_profiles import AgentProfileStore
from config_loader import ROOT, load_config


NONBLOCKING_RUNTIME_POLICY = (
    "Start and operate in non-blocking auto-approval mode; do not wait for human "
    "permission prompts during delegated work."
)

LEGACY_CODEX_ARCHITECT_TRIGGER_TAGS = (
    "architecture",
    "architect",
    "schema",
    "database",
    "migration",
    "api",
    "data flow",
    "架構",
    "資料流",
)
LEGACY_GEMINI_RESEARCHER_SPECIALTY = (
    "Scan large context, compare documents, find contradictions, and summarise evidence quickly."
)
LEGACY_GEMINI_RESEARCHER_TRIGGER_TAGS = (
    "research",
    "docs",
    "compare",
    "scan",
    "summarize",
    "context",
    "legacy",
    "文件",
    "整理",
    "研究",
    "大量",
)
LEGACY_GEMINI_RESEARCHER_RESPONSIBILITIES = (
    "Collect evidence and cite where it came from.",
    "Surface missing context before planning.",
)

LEGACY_FIELD_MIGRATIONS = {
    "codex-architect": {"trigger_tags": (LEGACY_CODEX_ARCHITECT_TRIGGER_TAGS,)},
    "gemini-researcher": {
        "model": ("Gemini 3.5 Flash",),
        "specialty": (LEGACY_GEMINI_RESEARCHER_SPECIALTY,),
        "trigger_tags": (LEGACY_GEMINI_RESEARCHER_TRIGGER_TAGS,),
        "responsibilities": (LEGACY_GEMINI_RESEARCHER_RESPONSIBILITIES,),
    },
    "gemini-challenger": {"model": ("Gemini 3.5 Flash",)},
    "grok-prototyper": {"role": ("Builder",), "rank": (3,)},
}


DEFAULT_TEAM_PROFILES = {
    "codex-dispatcher": {
        "base": "codex",
        "name": "codex-dispatcher",
        "label": "Codex Dispatcher",
        "role": "Dispatcher",
        "model": "Codex GPT-5.5",
        "specialty": "Classify incoming tasks and dispatch the smallest useful set of agents by role and specialty.",
        "trigger_tags": ["dispatch", "triage", "assign", "route", "who", "誰", "不確定", "派誰"],
        "rank": 1,
        "responsibilities": [
            "Choose agents when the user did not mention anyone explicitly.",
            "Keep the team small and explain the handoff when needed.",
        ],
        "avoid": ["Do not implement directly unless no better specialist is available."],
        "output_contract": "Name the selected agents and why, then hand off or summarise.",
    },
    "codex-planner": {
        "base": "codex",
        "name": "codex-planner",
        "label": "Codex Planner",
        "role": "Planner",
        "model": "Codex GPT-5.5",
        "specialty": "Turn ambiguous goals into ordered execution plans with constraints, risks, and success criteria.",
        "trigger_tags": ["plan", "planning", "roadmap", "spec", "scope", "requirements", "規劃", "計畫", "計劃", "需求"],
        "rank": 1,
        "responsibilities": ["Frame goals and constraints.", "Produce short, actionable plans."],
        "avoid": ["Do not overrun into implementation before the plan is accepted."],
        "output_contract": "State the plan, assumptions, risks, and next action.",
    },
    "codex-architect": {
        "base": "codex",
        "name": "codex-architect",
        "label": "Codex Architect",
        "role": "Architect",
        "model": "Codex GPT-5.5",
        "specialty": "Own architecture, data flow, repo fit, API boundaries, migrations, and implementation feasibility.",
        "trigger_tags": [
            "architecture",
            "architect",
            "schema",
            "database",
            "migration",
            "api design",
            "api boundary",
            "endpoint design",
            "data flow",
            "架構",
            "資料流",
        ],
        "rank": 1,
        "responsibilities": ["Check system boundaries.", "Keep changes aligned with repo patterns."],
        "avoid": ["Do not become the only reviewer for code you designed."],
        "output_contract": "Explain recommended architecture and the tradeoffs.",
    },
    "codex-architecture-reviewer": {
        "base": "codex",
        "name": "codex-architecture-reviewer",
        "label": "Codex Architecture Reviewer",
        "role": "Architecture Reviewer",
        "model": "Codex GPT-5.5",
        "specialty": "Review architecture, migrations, repo patterns, implementation feasibility, and integration fit.",
        "trigger_tags": [
            "architecture review",
            "implementation review",
            "repo pattern",
            "migration review",
            "feasibility",
            "架構審查",
            "資料庫遷移",
            "實作可行性",
        ],
        "rank": 2,
        "responsibilities": [
            "Check whether a plan fits the existing repo and implementation path.",
            "Call out migration, data-flow, and abstraction risks with concrete alternatives.",
        ],
        "avoid": ["Do not replace Claude Reviewer for product-risk and bug-review breadth."],
        "output_contract": "Find architecture and feasibility risks first, then recommend the smallest reliable implementation path.",
    },
    "codex-builder": {
        "base": "codex",
        "name": "codex-builder",
        "label": "Codex Builder",
        "role": "Builder",
        "model": "Codex GPT-5.5",
        "specialty": "Implement production code changes, tests, refactors, and repo-native fixes.",
        "trigger_tags": ["implement", "build", "fix", "code", "frontend", "backend", "test", "實作", "修正", "修"],
        "rank": 1,
        "responsibilities": ["Make scoped code changes.", "Run verification before reporting completion."],
        "avoid": ["Do not skip tests for risky changes."],
        "output_contract": "Report files changed, verification run, and remaining risks.",
    },
    "claude-designer": {
        "base": "claude",
        "name": "claude-designer",
        "label": "Claude Designer",
        "role": "Designer",
        "model": "Claude Code Opus 4.7",
        "specialty": "Lead UI/UX direction, information architecture, flows, critique, copy, and visual hierarchy.",
        "trigger_tags": ["ui", "ux", "design", "layout", "wireframe", "mockup", "pencil", "visual", "設計", "畫面", "介面"],
        "rank": 1,
        "responsibilities": ["Decide whether the experience is usable and coherent.", "Give concrete UI/UX improvements."],
        "avoid": ["Do not treat implementation convenience as the design answer."],
        "output_contract": "Prioritise UX issues, then give concrete screen-level recommendations.",
    },
    "claude-reviewer": {
        "base": "claude",
        "name": "claude-reviewer",
        "label": "Claude Reviewer",
        "role": "Reviewer",
        "model": "Claude Code Opus 4.7",
        "specialty": "Lead code review for bugs, regressions, tests, maintainability, and product-risk issues.",
        "trigger_tags": ["review", "bug", "regression", "test", "maintainability", "pr", "檢查", "審查", "測試"],
        "rank": 1,
        "responsibilities": ["Find concrete issues with file/line evidence.", "Separate blockers from nice-to-haves."],
        "avoid": ["Do not rewrite the feature unless asked."],
        "output_contract": "Findings first, ordered by severity, with test gaps and residual risk.",
    },
    "gemini-researcher": {
        "base": "antigravity",
        "name": "gemini-researcher",
        "label": "Gemini Researcher",
        "role": "Researcher",
        "model": "Gemini 3.5 Flash (High)",
        "specialty": "Scan large context and web/current sources for laws, official API docs, policy changes, contradictions, and evidence.",
        "trigger_tags": [
            "research",
            "web research",
            "official docs",
            "api docs",
            "regulation",
            "law",
            "latest",
            "rate limit",
            "docs",
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
            "大量",
        ],
        "rank": 1,
        "responsibilities": [
            "Collect evidence and cite where it came from.",
            "Prefer primary sources such as official API docs, laws, standards, and release notes.",
            "Surface missing context before planning.",
        ],
        "avoid": ["Do not be final authority for production code changes."],
        "output_contract": "Summarise evidence, gaps, and recommended next checks.",
    },
    "gemini-challenger": {
        "base": "antigravity",
        "name": "gemini-challenger",
        "label": "Gemini Challenger",
        "role": "Red Team",
        "model": "Gemini 3.5 Flash (High)",
        "specialty": "Challenge assumptions, scan for edge cases, security risk, permission mistakes, and spec gaps.",
        "trigger_tags": ["red team", "challenge", "risk", "edge case", "security", "漏洞", "風險", "權限", "矛盾"],
        "rank": 2,
        "responsibilities": ["Attack the plan from failure scenarios.", "Find hidden assumptions and risky omissions."],
        "avoid": ["Do not repeat the main reviewer; bring an independent angle."],
        "output_contract": "List the highest-risk breakpoints and concrete mitigations.",
    },
    "codex-challenger": {
        "base": "codex",
        "name": "codex-challenger",
        "label": "Codex Challenger",
        "role": "Engineering Challenger",
        "model": "Codex GPT-5.5",
        "specialty": "Challenge engineering plans for feasibility, migration safety, repo-pattern drift, overengineering, and test strategy.",
        "trigger_tags": [
            "engineering risk",
            "migration risk",
            "overengineering",
            "repo pattern",
            "smaller path",
            "feasibility",
            "可行性",
            "過度設計",
            "遷移風險",
        ],
        "rank": 2,
        "responsibilities": [
            "Pressure-test the implementation path before work starts.",
            "Suggest smaller, safer alternatives when a plan is too risky.",
        ],
        "avoid": ["Do not duplicate Gemini Challenger's broad spec/context challenge."],
        "output_contract": "List the engineering breakpoints, why they matter, and the safer implementation path.",
    },
    "gemini-prototyper": {
        "base": "antigravity",
        "name": "gemini-prototyper",
        "label": "Gemini Prototyper",
        "role": "Prototyper",
        "model": "Gemini 3.5 Flash (High)",
        "specialty": "Create context-heavy UI drafts, multi-option prototypes, and document/spec-to-demo explorations.",
        "trigger_tags": [
            "prototype from docs",
            "ui prototype",
            "context prototype",
            "multi-option",
            "wireframe",
            "草案",
            "多方案",
            "文件轉原型",
        ],
        "rank": 2,
        "responsibilities": [
            "Explore quick UI or workflow prototype options from large context.",
            "Keep outputs explicitly exploratory and ready for production handoff.",
        ],
        "avoid": ["Do not own final production merge or final architecture."],
        "output_contract": "Show the prototype direction, compared options, assumptions, and what Codex Builder should productionise.",
    },
    "grok-prototyper": {
        "base": "grok",
        "name": "grok-prototyper",
        "label": "Grok Prototyper",
        "role": "Prototyper",
        "model": "Grok Build",
        "specialty": "Build quick prototypes, spikes, demos, and alternate implementation drafts for low-risk exploration.",
        "trigger_tags": ["prototype", "spike", "demo", "quick", "experiment", "alternative", "grok", "原型", "快速"],
        "rank": 1,
        "responsibilities": ["Move fast on exploratory work.", "Keep prototypes isolated and easy to discard."],
        "avoid": ["Do not own final architecture or final review."],
        "output_contract": "Show what was tried, what worked, and what should be productionised by the main builder.",
    },
}


def apply_default_team_profiles(data_dir: str | Path, agents_config: dict[str, dict]) -> dict[str, dict]:
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    store = AgentProfileStore(data_path / "agent_profiles.json", agents_config)

    seeded: dict[str, dict] = {}
    for profile_id, profile in DEFAULT_TEAM_PROFILES.items():
        if profile["base"] not in agents_config:
            continue
        profile = {"runtime_policy": NONBLOCKING_RUNTIME_POLICY, **profile}
        force_fields = ["base"]
        existing = store.get(profile_id) or {}
        for field, legacy_values in LEGACY_FIELD_MIGRATIONS.get(profile_id, {}).items():
            existing_value = existing.get(field)
            if any(_legacy_value_matches(existing_value, legacy_value) for legacy_value in legacy_values):
                force_fields.append(field)
        updated = store.upsert_profile(profile_id, profile, preserve_existing=True, force_fields=force_fields)
        if updated:
            seeded[profile_id] = updated

    _seed_roles_file(data_path / "roles.json", seeded)
    return seeded


def _legacy_value_matches(existing_value, legacy_value) -> bool:
    if isinstance(existing_value, str):
        existing_value = existing_value.strip()
    if isinstance(legacy_value, str):
        legacy_value = legacy_value.strip()
    if isinstance(existing_value, list):
        existing_value = tuple(existing_value)
    if isinstance(legacy_value, list):
        legacy_value = tuple(legacy_value)
    return existing_value == legacy_value


def _seed_roles_file(path: Path, profiles: dict[str, dict]) -> None:
    try:
        roles = json.loads(path.read_text("utf-8")) if path.exists() else {}
    except Exception:
        roles = {}
    if not isinstance(roles, dict):
        roles = {}

    changed = False
    for profile in profiles.values():
        name = str(profile.get("name", "")).strip()
        role = str(profile.get("role", "")).strip()
        if name and role and roles.get(name) != role:
            roles[name] = role
            changed = True

    if changed:
        path.write_text(json.dumps(dict(sorted(roles.items())), ensure_ascii=False) + "\n", "utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the default model-specialized agent team.")
    parser.add_argument("--data-dir", default="", help="Override server.data_dir for profile seeding.")
    args = parser.parse_args(argv)

    cfg = load_config(ROOT)
    data_dir = args.data_dir or cfg.get("server", {}).get("data_dir", "./data")
    profiles = apply_default_team_profiles(data_dir, cfg.get("agents", {}))
    print(f"Seeded {len(profiles)} team profiles in {Path(data_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
