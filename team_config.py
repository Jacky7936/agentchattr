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
        "trigger_tags": ["architecture", "architect", "schema", "database", "migration", "api", "data flow", "架構", "資料流"],
        "rank": 1,
        "responsibilities": ["Check system boundaries.", "Keep changes aligned with repo patterns."],
        "avoid": ["Do not become the only reviewer for code you designed."],
        "output_contract": "Explain recommended architecture and the tradeoffs.",
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
        "base": "gemini",
        "name": "gemini-researcher",
        "label": "Gemini Researcher",
        "role": "Researcher",
        "model": "Gemini 3.5 Flash",
        "specialty": "Scan large context, compare documents, find contradictions, and summarise evidence quickly.",
        "trigger_tags": ["research", "docs", "compare", "scan", "summarize", "context", "legacy", "文件", "整理", "研究", "大量"],
        "rank": 1,
        "responsibilities": ["Collect evidence and cite where it came from.", "Surface missing context before planning."],
        "avoid": ["Do not be final authority for production code changes."],
        "output_contract": "Summarise evidence, gaps, and recommended next checks.",
    },
    "gemini-challenger": {
        "base": "gemini",
        "name": "gemini-challenger",
        "label": "Gemini Challenger",
        "role": "Red Team",
        "model": "Gemini 3.5 Flash",
        "specialty": "Challenge assumptions, scan for edge cases, security risk, permission mistakes, and spec gaps.",
        "trigger_tags": ["red team", "challenge", "risk", "edge case", "security", "漏洞", "風險", "權限", "矛盾"],
        "rank": 2,
        "responsibilities": ["Attack the plan from failure scenarios.", "Find hidden assumptions and risky omissions."],
        "avoid": ["Do not repeat the main reviewer; bring an independent angle."],
        "output_contract": "List the highest-risk breakpoints and concrete mitigations.",
    },
    "grok-prototyper": {
        "base": "grok",
        "name": "grok-prototyper",
        "label": "Grok Prototyper",
        "role": "Builder",
        "model": "Grok Build",
        "specialty": "Build quick prototypes, spikes, demos, and alternate implementation drafts for low-risk exploration.",
        "trigger_tags": ["prototype", "spike", "demo", "quick", "experiment", "alternative", "grok", "原型", "快速"],
        "rank": 3,
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
        updated = store.upsert_profile(profile_id, profile, preserve_existing=True)
        if updated:
            seeded[profile_id] = updated

    _seed_roles_file(data_path / "roles.json", seeded)
    return seeded


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
        if name and role and not roles.get(name):
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
