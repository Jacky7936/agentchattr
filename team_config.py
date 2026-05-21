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
CODEX_GPT_55_LAUNCH_MODEL = "gpt-5.5"
CLAUDE_OPUS_47_LAUNCH_MODEL = "claude-opus-4-7[1m]"
CLAUDE_SONNET_46_LAUNCH_MODEL = "claude-sonnet-4-6"

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
LEGACY_CODEX_DISPATCHER_SPECIALTY = (
    "Classify incoming tasks and dispatch the smallest useful set of agents by role and specialty."
)
LEGACY_CODEX_DISPATCHER_RESPONSIBILITIES = (
    "Choose agents when the user did not mention anyone explicitly.",
    "Keep the team small and explain the handoff when needed.",
)
LEGACY_CODEX_DISPATCHER_AVOID = ("Do not implement directly unless no better specialist is available.",)
LEGACY_CODEX_DISPATCHER_OUTPUT_CONTRACT = "Name the selected agents and why, then hand off or summarise."
LEGACY_RESEARCHER_SPECIALTY = (
    "Scan large context, compare documents, find contradictions, and summarise evidence quickly."
)
LEGACY_RESEARCHER_TRIGGER_TAGS = (
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
LEGACY_RESEARCHER_RESPONSIBILITIES = (
    "Collect evidence and cite where it came from.",
    "Surface missing context before planning.",
)
LEGACY_CODEX_CHALLENGER_AVOID = ("Do not duplicate Gemini Challenger's broad spec/context challenge.",)

LEGACY_FIELD_MIGRATIONS = {
    "codex-orchestrator": {
        "specialty": (LEGACY_CODEX_DISPATCHER_SPECIALTY,),
        "responsibilities": (LEGACY_CODEX_DISPATCHER_RESPONSIBILITIES,),
        "avoid": (LEGACY_CODEX_DISPATCHER_AVOID,),
        "output_contract": (LEGACY_CODEX_DISPATCHER_OUTPUT_CONTRACT,),
    },
    "codex-architect": {"trigger_tags": (LEGACY_CODEX_ARCHITECT_TRIGGER_TAGS,)},
    "codex-challenger": {"avoid": (LEGACY_CODEX_CHALLENGER_AVOID,)},
    "claude-researcher": {
        "specialty": (LEGACY_RESEARCHER_SPECIALTY,),
        "trigger_tags": (LEGACY_RESEARCHER_TRIGGER_TAGS,),
        "responsibilities": (LEGACY_RESEARCHER_RESPONSIBILITIES,),
    },
}

RETIRED_DEFAULT_PROFILE_IDS = (
    "codex-architecture-reviewer",
    "codex-dispatcher",
    "codex-researcher",
    "codex-spike-prototyper",
    "gemini-researcher",
    "gemini-challenger",
    "gemini-prototyper",
    "grok-prototyper",
)


DEFAULT_TEAM_PROFILES = {
    "codex-orchestrator": {
        "base": "codex",
        "name": "codex-orchestrator",
        "label": "Codex Orchestrator",
        "role": "Orchestrator",
        "model": "Codex GPT-5.5",
        "launch_model": CODEX_GPT_55_LAUNCH_MODEL,
        "thinking_effort": "high",
        "launch_effort": "high",
        "specialty": "Act as the commander for multi-agent work: classify tasks, parallel-dispatch focused workers, track progress, and prevent routing loops.",
        "trigger_tags": ["dispatch", "triage", "assign", "route", "who", "誰", "不確定", "派誰"],
        "rank": 1,
        "responsibilities": [
            "Choose agents when the user did not mention anyone explicitly.",
            "Run the room as commander: split independent work across active workers, ask for progress, and consolidate the result.",
            "Use commander controls to keep active workers from waking each other into loops.",
        ],
        "avoid": ["Do not implement directly unless no better specialist is available.", "Do not mention inactive agents during a commander lane."],
        "output_contract": "Name active workers, assign each slice, track progress, and summarise the final result for the human.",
    },
    "codex-planner": {
        "base": "codex",
        "name": "codex-planner",
        "label": "Codex Planner",
        "role": "Planner",
        "model": "Codex GPT-5.5",
        "launch_model": CODEX_GPT_55_LAUNCH_MODEL,
        "thinking_effort": "xhigh",
        "launch_effort": "xhigh",
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
        "launch_model": CODEX_GPT_55_LAUNCH_MODEL,
        "thinking_effort": "xhigh",
        "launch_effort": "xhigh",
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
    "codex-reviewer": {
        "base": "codex",
        "name": "codex-reviewer",
        "label": "Codex Reviewer",
        "role": "Reviewer",
        "model": "Codex GPT-5.5",
        "launch_model": CODEX_GPT_55_LAUNCH_MODEL,
        "thinking_effort": "xhigh",
        "launch_effort": "xhigh",
        "specialty": "Deep implementation and architecture review for repo-native bugs, regressions, tests, verification gaps, migration safety, and patch fit.",
        "trigger_tags": [
            "architecture review",
            "code review",
            "implementation review",
            "patch review",
            "regression",
            "test",
            "verification",
            "repo pattern",
            "repo-native",
            "migration review",
            "feasibility",
            "架構審查",
            "程式審查",
            "資料庫遷移",
            "實作可行性",
            "測試",
            "驗證",
        ],
        "rank": 2,
        "responsibilities": [
            "Act as the Codex second-opinion reviewer for implementation-heavy changes.",
            "Focus on concrete repo behavior, tests, integration risk, architecture fit, and verification evidence.",
        ],
        "avoid": ["Do not duplicate Claude Reviewer's product-risk pass unless code evidence changes the conclusion."],
        "output_contract": "Find implementation and verification issues first, with file/line evidence and the smallest corrective path.",
    },
    "codex-builder": {
        "base": "codex",
        "name": "codex-builder",
        "label": "Codex Builder",
        "role": "Builder",
        "model": "Codex GPT-5.5",
        "launch_model": CODEX_GPT_55_LAUNCH_MODEL,
        "thinking_effort": "xhigh",
        "launch_effort": "xhigh",
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
        "launch_model": CLAUDE_OPUS_47_LAUNCH_MODEL,
        "thinking_effort": "max",
        "launch_effort": "max",
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
        "launch_model": CLAUDE_OPUS_47_LAUNCH_MODEL,
        "thinking_effort": "max",
        "launch_effort": "max",
        "specialty": "Lead code review for bugs, regressions, tests, maintainability, and product-risk issues.",
        "trigger_tags": ["review", "bug", "regression", "test", "maintainability", "pr", "檢查", "審查", "測試"],
        "rank": 1,
        "responsibilities": ["Find concrete issues with file/line evidence.", "Separate blockers from nice-to-haves."],
        "avoid": ["Do not rewrite the feature unless asked."],
        "output_contract": "Findings first, ordered by severity, with test gaps and residual risk.",
    },
    "claude-researcher": {
        "base": "claude",
        "name": "claude-researcher",
        "label": "Claude Researcher",
        "role": "Researcher",
        "model": "Claude Code Sonnet 4.6",
        "launch_model": CLAUDE_SONNET_46_LAUNCH_MODEL,
        "thinking_effort": "high",
        "launch_effort": "high",
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
    "claude-challenger": {
        "base": "claude",
        "name": "claude-challenger",
        "label": "Claude Challenger",
        "role": "Red Team",
        "model": "Claude Code Opus 4.7",
        "launch_model": CLAUDE_OPUS_47_LAUNCH_MODEL,
        "thinking_effort": "max",
        "launch_effort": "max",
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
        "launch_model": CODEX_GPT_55_LAUNCH_MODEL,
        "thinking_effort": "xhigh",
        "launch_effort": "xhigh",
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
        "avoid": ["Do not duplicate Claude Challenger's broad spec/context challenge."],
        "output_contract": "List the engineering breakpoints, why they matter, and the safer implementation path.",
    },
    "codex-prototyper": {
        "base": "codex",
        "name": "codex-prototyper",
        "label": "Codex Prototyper",
        "role": "Prototyper",
        "model": "Codex GPT-5.5",
        "launch_model": CODEX_GPT_55_LAUNCH_MODEL,
        "thinking_effort": "xhigh",
        "launch_effort": "xhigh",
        "specialty": "Create context-heavy UI drafts, multi-option prototypes, document/spec-to-demo explorations, quick spikes, and alternate implementation drafts.",
        "trigger_tags": [
            "prototype",
            "prototype from docs",
            "ui prototype",
            "context prototype",
            "multi-option",
            "wireframe",
            "spike",
            "demo",
            "quick",
            "experiment",
            "alternative",
            "草案",
            "多方案",
            "文件轉原型",
            "原型",
            "快速",
        ],
        "rank": 2,
        "responsibilities": [
            "Explore quick UI or workflow prototype options from large context.",
            "Move fast on low-risk exploratory spikes.",
            "Keep outputs explicitly exploratory and ready for production handoff.",
        ],
        "avoid": ["Do not own final production merge or final architecture."],
        "output_contract": "Show the prototype direction, compared options, assumptions, and what Codex Builder should productionise.",
    },
    "codex-module-prototype-designer": {
        "base": "codex",
        "name": "codex-module-prototype-designer",
        "label": "Codex Module Prototype Designer",
        "role": "Module Prototype Designer",
        "model": "Codex GPT-5.5",
        "launch_model": CODEX_GPT_55_LAUNCH_MODEL,
        "thinking_effort": "high",
        "launch_effort": "high",
        "specialty": "Turn module plans into polished, repo-aware UI prototypes for validating flow, function, states, and visual direction before production implementation.",
        "trigger_tags": [
            "module prototype",
            "prototype designer",
            "ui prototype",
            "flow prototype",
            "screen prototype",
            "wireflow",
            "mockup",
            "desktop mobile",
            "模組原型",
            "UI原型",
            "畫面原型",
            "流程確認",
            "功能確認",
            "設計方向",
        ],
        "rank": 1,
        "responsibilities": [
            "Read the repo's existing routes, components, design language, and module plan before drafting screens.",
            "Produce inspectable desktop and mobile prototypes that cover the main flow, empty states, error states, and permission or disabled states.",
            "Keep prototype artifacts marked as direction and flow validation, not canonical UI or data-model authority.",
            "Use Browser to inspect the rendered local prototype when a route or static file is available.",
        ],
        "avoid": [
            "Do not promote prototype artifacts into formal UI or schema specifications unless the user explicitly approves.",
            "Do not make production data-model or business-logic changes while exploring prototype direction.",
        ],
        "output_contract": "Deliver the prototype direction, covered flows/states, assumptions, open questions, and what must change before production implementation.",
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
        existing = store.get(profile_id) or {}
        has_custom_model = bool(existing.get("model") and existing.get("model") != profile.get("model"))
        has_custom_launch_model = bool(has_custom_model and existing.get("launch_model"))
        if has_custom_model and not has_custom_launch_model:
            profile = dict(profile)
            profile.pop("launch_model", None)
        force_fields = ["base", "thinking_effort"]
        if profile.get("launch_model") and not has_custom_launch_model:
            force_fields.append("launch_model")
        if profile.get("launch_effort"):
            force_fields.append("launch_effort")
        for field, legacy_values in LEGACY_FIELD_MIGRATIONS.get(profile_id, {}).items():
            existing_value = existing.get(field)
            if any(_legacy_value_matches(existing_value, legacy_value) for legacy_value in legacy_values):
                force_fields.append(field)
        updated = store.upsert_profile(profile_id, profile, preserve_existing=True, force_fields=force_fields)
        if updated:
            seeded[profile_id] = updated

    store.delete_profiles(RETIRED_DEFAULT_PROFILE_IDS)
    _seed_roles_file(data_path / "roles.json", seeded, retired_profile_ids=RETIRED_DEFAULT_PROFILE_IDS)
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


def _seed_roles_file(path: Path, profiles: dict[str, dict], *, retired_profile_ids: tuple[str, ...] = ()) -> None:
    try:
        roles = json.loads(path.read_text("utf-8")) if path.exists() else {}
    except Exception:
        roles = {}
    if not isinstance(roles, dict):
        roles = {}

    changed = False
    for profile_id in retired_profile_ids:
        if roles.pop(profile_id, None) is not None:
            changed = True

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
