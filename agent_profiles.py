"""Persistent agent profiles for fixed multi-agent lineups."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path


_NAME_RE = re.compile(r"[^a-z0-9-]")
_ALIASES = {
    "codex-reviwer": "codex-reviewer",
}
_CORE_PROFILE_FIELDS = {"base", "name", "label", "role"}
_LIST_METADATA_FIELDS = {"trigger_tags", "responsibilities", "avoid"}


def normalize_profile_id(value: str) -> str:
    value = value.strip().lower().replace("_", "-").replace(" ", "-")
    value = _NAME_RE.sub("", value)
    return re.sub(r"-+", "-", value).strip("-")


class AgentProfileStore:
    """Small JSON-backed store: profile_id -> {base, name, label, role}."""

    def __init__(self, path: str | Path, agents_config: dict[str, dict] | None = None):
        self._path = Path(path)
        self._agents_config = agents_config or {}
        self._profiles: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            return
        changed = False
        with self._lock:
            for profile_id, profile in raw.items():
                if not isinstance(profile, dict):
                    continue
                pid = normalize_profile_id(profile_id)
                if not pid:
                    continue
                clean = self._normalize_profile(pid, profile)
                if clean:
                    self._profiles[pid] = clean
                    changed = changed or pid != profile_id
            changed = self._apply_aliases_locked() or changed
        if changed:
            self._save()

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            with self._lock:
                snapshot = dict(sorted(self._profiles.items()))
            tmp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", "utf-8")
            tmp.replace(self._path)
        except Exception:
            pass

    def bootstrap_from_roles(self, roles_path: str | Path):
        roles_file = Path(roles_path)
        if not roles_file.exists():
            return
        try:
            roles = json.loads(roles_file.read_text("utf-8"))
        except Exception:
            return
        if not isinstance(roles, dict):
            return

        changed = False
        with self._lock:
            for raw_name, raw_role in roles.items():
                name = _ALIASES.get(normalize_profile_id(str(raw_name)), normalize_profile_id(str(raw_name)))
                role = str(raw_role).strip()
                if not name:
                    continue
                base = self.infer_base(name)
                if not base:
                    continue
                profile = self._profiles.get(name, {})
                if not profile:
                    profile = {
                        "base": base,
                        "name": name,
                        "label": self._derive_label(base, role, fallback_name=name),
                        "role": role,
                    }
                    self._profiles[name] = profile
                    changed = True
                else:
                    if not profile.get("role") and role:
                        profile["role"] = role
                        changed = True
                    if self._fill_missing_locked(name, profile, base):
                        changed = True
            changed = self._apply_aliases_locked() or changed
        if changed:
            self._save()

    def ensure_profile(self, profile_id: str, base: str, label: str | None = None) -> tuple[dict | None, str | None]:
        pid = normalize_profile_id(profile_id)
        base = normalize_profile_id(base)
        if not pid:
            return None, "profile is required"
        if not base or base not in self._agents_config:
            return None, f"unknown base: {base}"

        changed = False
        with self._lock:
            profile = self._profiles.get(pid)
            if profile:
                profile_base = profile.get("base", "")
                if profile_base and profile_base != base:
                    return None, f"profile '{pid}' belongs to {profile_base}, not {base}"
                if self._fill_missing_locked(pid, profile, base):
                    changed = True
            else:
                role = self._derive_role(pid, base)
                profile = {
                    "base": base,
                    "name": pid,
                    "label": label or self._derive_label(base, role, fallback_name=pid),
                    "role": role,
                }
                self._profiles[pid] = profile
                changed = True
            result = dict(profile)
        if changed:
            self._save()
        return result, None

    def get(self, profile_id: str) -> dict | None:
        pid = normalize_profile_id(profile_id)
        with self._lock:
            profile = self._profiles.get(pid)
            return dict(profile) if profile else None

    def get_all(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._profiles.items()}

    def delete_profiles(self, profile_ids: list[str] | tuple[str, ...] | set[str]) -> bool:
        changed = False
        with self._lock:
            for profile_id in profile_ids:
                pid = normalize_profile_id(str(profile_id))
                if pid and pid in self._profiles:
                    del self._profiles[pid]
                    changed = True
        if changed:
            self._save()
        return changed

    def get_by_name(self, name: str) -> dict | None:
        clean_name = normalize_profile_id(name)
        if not clean_name:
            return None
        with self._lock:
            profile = self._profiles.get(clean_name)
            if profile:
                return dict(profile)
            pid = self._find_profile_id_by_name_locked(clean_name)
            profile = self._profiles.get(pid) if pid else None
            return dict(profile) if profile else None

    def upsert_profile(
        self,
        profile_id: str,
        profile: dict,
        *,
        preserve_existing: bool = True,
        force_fields: list[str] | None = None,
    ) -> dict | None:
        pid = normalize_profile_id(profile_id)
        if not pid:
            return None
        clean = self._normalize_profile(pid, profile)
        if not clean:
            return None
        with self._lock:
            if preserve_existing and pid in self._profiles:
                existing = self._profiles[pid]
                merged = dict(existing)
                for key in force_fields or []:
                    if key in clean:
                        merged[key] = clean[key]
                for key, value in clean.items():
                    if self._is_missing_metadata_value(merged.get(key)):
                        merged[key] = value
                self._profiles[pid] = merged
                result = dict(merged)
            else:
                self._profiles[pid] = clean
                result = dict(clean)
        self._save()
        return result

    def infer_base(self, name: str) -> str:
        clean = normalize_profile_id(name)
        if clean in self._agents_config:
            return clean
        for base in sorted(self._agents_config, key=len, reverse=True):
            if clean.startswith(f"{base}-"):
                return base
        return ""

    def update_identity(
        self,
        *,
        old_name: str,
        new_name: str,
        label: str,
        base: str,
        profile_id: str = "",
        role: str = "",
    ) -> dict | None:
        base = normalize_profile_id(base) or self.infer_base(new_name)
        if not base:
            return None
        pid = normalize_profile_id(profile_id) or normalize_profile_id(new_name)
        old_pid = normalize_profile_id(old_name)
        name = normalize_profile_id(new_name)
        if not pid or not name:
            return None

        with self._lock:
            profile = self._profiles.get(pid)
            if not profile and old_pid and old_pid != pid:
                profile = self._profiles.pop(old_pid, None)
            if not profile:
                profile = {}
            profile["base"] = base
            profile["name"] = name
            profile["label"] = label.strip() or self._derive_label(base, role or profile.get("role", ""), fallback_name=name)
            if role or "role" not in profile:
                profile["role"] = role
            self._profiles[pid] = profile
            result = dict(profile)
        self._save()
        return result

    def update_role_for_name(
        self,
        name: str,
        role: str,
        *,
        base: str = "",
        profile_id: str = "",
        label: str = "",
    ) -> dict | None:
        clean_name = normalize_profile_id(name)
        if not clean_name:
            return None
        base = normalize_profile_id(base) or self.infer_base(clean_name)
        if not base:
            return None

        with self._lock:
            pid = normalize_profile_id(profile_id) or self._find_profile_id_by_name_locked(clean_name) or clean_name
            profile = self._profiles.get(pid, {})
            profile["base"] = profile.get("base") or base
            profile["name"] = profile.get("name") or clean_name
            profile["label"] = label.strip() or profile.get("label") or self._derive_label(base, role, fallback_name=clean_name)
            profile["role"] = role.strip()
            self._profiles[pid] = profile
            result = dict(profile)
        self._save()
        return result

    def _find_profile_id_by_name_locked(self, name: str) -> str:
        for pid, profile in self._profiles.items():
            if profile.get("name") == name:
                return pid
        return ""

    def _normalize_profile(self, profile_id: str, profile: dict) -> dict | None:
        base = normalize_profile_id(str(profile.get("base", ""))) or self.infer_base(profile_id)
        if not base:
            return None
        name = normalize_profile_id(str(profile.get("name", ""))) or profile_id
        name = _ALIASES.get(name, name)
        role = str(profile.get("role", "")).strip()
        label = str(profile.get("label", "")).strip() or self._derive_label(base, role, fallback_name=name)
        clean = {"base": base, "name": name, "label": label, "role": role}
        clean.update(self._normalize_metadata(profile))
        return clean

    def _normalize_metadata(self, profile: dict) -> dict:
        metadata: dict = {}
        for key, value in profile.items():
            if key in _CORE_PROFILE_FIELDS:
                continue
            if key in _LIST_METADATA_FIELDS:
                normalized = self._normalize_string_list(value)
                if normalized:
                    metadata[key] = normalized
                continue
            if key == "rank":
                try:
                    metadata[key] = int(value)
                except (TypeError, ValueError):
                    continue
                continue
            if isinstance(value, str):
                value = value.strip()
                if value:
                    metadata[key] = value
                continue
            if isinstance(value, (int, float, bool)) or value is None:
                metadata[key] = value
                continue
            if isinstance(value, list):
                metadata[key] = [v for v in value if v not in ("", None)]
                continue
            if isinstance(value, dict):
                metadata[key] = value
        return metadata

    def _normalize_string_list(self, value) -> list[str]:
        raw = value if isinstance(value, list) else [value]
        out = []
        for item in raw:
            text = str(item).strip()
            if text:
                out.append(text)
        return out

    def _is_missing_metadata_value(self, value) -> bool:
        return value in (None, "", [], {})

    def _fill_missing_locked(self, profile_id: str, profile: dict, base: str) -> bool:
        changed = False
        if not profile.get("base"):
            profile["base"] = base
            changed = True
        if not profile.get("name"):
            profile["name"] = profile_id
            changed = True
        if not profile.get("label"):
            profile["label"] = self._derive_label(base, profile.get("role", ""), fallback_name=profile["name"])
            changed = True
        if "role" not in profile:
            profile["role"] = self._derive_role(profile_id, base)
            changed = True
        return changed

    def _apply_aliases_locked(self) -> bool:
        changed = False
        for old, new in _ALIASES.items():
            old_profile = self._profiles.pop(old, None)
            if not old_profile:
                continue
            if old_profile.get("name") == old:
                old_profile["name"] = new
            existing = self._profiles.get(new)
            if existing:
                if not existing.get("role") and old_profile.get("role"):
                    existing["role"] = old_profile["role"]
                if not existing.get("label") and old_profile.get("label"):
                    existing["label"] = old_profile["label"]
            else:
                self._profiles[new] = old_profile
            changed = True
        return changed

    def _derive_role(self, profile_id: str, base: str) -> str:
        if profile_id == base:
            return ""
        prefix = f"{base}-"
        raw = profile_id[len(prefix):] if profile_id.startswith(prefix) else profile_id
        return " ".join(part.capitalize() for part in raw.replace("_", "-").split("-") if part)

    def _derive_label(self, base: str, role: str, *, fallback_name: str) -> str:
        base_label = self._agents_config.get(base, {}).get("label", base.capitalize())
        if role:
            return f"{base_label} {role}"
        if fallback_name != base:
            return fallback_name
        return base_label
