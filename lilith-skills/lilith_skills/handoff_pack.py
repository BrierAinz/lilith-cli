"""Session Handoff Pack — cross-session agent context resumption.

Inspired by Moryn's Handoff Pack pattern: structured context packs that
allow agents to resume work across sessions. When a task completes, the
system auto-captures a HandoffPack with goals, decisions, risks,
preferences, files, and next actions. The next agent can pick up where
the previous one left off.

Usage::

    from lilith_skills.handoff_pack import HandoffPack, HandoffPackManager

    # Capture a handoff pack after task completion
    manager = HandoffPackManager()
    pack = manager.capture(
        session_id="abc123",
        agent="odin",
        goals=["Implement OAuth2 flow"],
        decisions=["Use PKCE instead of implicit grant"],
        risks=["Token refresh edge case on mobile"],
        files=["src/auth.py", "tests/test_auth.py"],
        next_actions=["Write refresh token tests", "Update docs"],
    )

    # Resume from a handoff pack
    context = manager.resume(pack.pack_id)
    # Returns a dict with all context needed to continue

    # Quality gate: validate pack before trusting it
    is_valid = manager.validate(pack)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("lilith.skills.handoff_pack")


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class HandoffPack:
    """Structured context pack for cross-session agent resumption.

    Attributes:
        pack_id: Unique identifier for this handoff pack.
        session_id: The session this pack was captured from.
        agent: The agent that produced this pack.
        timestamp: When the pack was created.
        goals: What the agent was trying to achieve.
        decisions: Key decisions made during the session.
        risks: Identified risks or blockers.
        preferences: User preferences discovered or set.
        files: Files touched or referenced.
        next_actions: What needs to happen next.
        metadata: Free-form additional context.
        quality_score: Auto-computed quality score (0.0-1.0).
    """

    pack_id: str = ""
    session_id: str = ""
    agent: str = ""
    timestamp: float = field(default_factory=time.time)
    goals: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    quality_score: float = 0.0

    def __post_init__(self) -> None:
        if not self.pack_id:
            self.pack_id = str(uuid.uuid4())[:12]

    def to_dict(self) -> dict[str, Any]:
        """Serialize pack to dict."""
        return {
            "pack_id": self.pack_id,
            "session_id": self.session_id,
            "agent": self.agent,
            "timestamp": self.timestamp,
            "goals": self.goals,
            "decisions": self.decisions,
            "risks": self.risks,
            "preferences": self.preferences,
            "files": self.files,
            "next_actions": self.next_actions,
            "metadata": self.metadata,
            "quality_score": self.quality_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffPack:
        """Deserialize pack from dict."""
        return cls(
            pack_id=data.get("pack_id", ""),
            session_id=data.get("session_id", ""),
            agent=data.get("agent", ""),
            timestamp=data.get("timestamp", time.time()),
            goals=data.get("goals", []),
            decisions=data.get("decisions", []),
            risks=data.get("risks", []),
            preferences=data.get("preferences", []),
            files=data.get("files", []),
            next_actions=data.get("next_actions", []),
            metadata=data.get("metadata", {}),
            quality_score=data.get("quality_score", 0.0),
        )

    def summary(self) -> str:
        """Human-readable summary of the pack."""
        lines = [
            f"Handoff Pack {self.pack_id} (from {self.agent})",
            f"  Goals: {len(self.goals)}",
            f"  Decisions: {len(self.decisions)}",
            f"  Risks: {len(self.risks)}",
            f"  Files: {len(self.files)}",
            f"  Next Actions: {len(self.next_actions)}",
            f"  Quality: {self.quality_score:.2f}",
        ]
        return "\n".join(lines)


# ── Quality Gate ─────────────────────────────────────────────────────────────


class HandoffQualityGate:
    """Read-only quality check before trusting a handoff pack.

    Inspired by Moryn's context quality gate. Validates that a pack
    has sufficient information to be useful for resumption.
    """

    MIN_GOALS = 1
    MIN_DECISIONS = 0
    MIN_NEXT_ACTIONS = 1
    MIN_FILES = 0
    MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days

    @classmethod
    def validate(cls, pack: HandoffPack) -> tuple[bool, list[str]]:
        """Validate a handoff pack.

        Returns:
            (is_valid, list_of_issues)
        """
        issues: list[str] = []

        if len(pack.goals) < cls.MIN_GOALS:
            issues.append(f"Too few goals ({len(pack.goals)} < {cls.MIN_GOALS})")

        if len(pack.next_actions) < cls.MIN_NEXT_ACTIONS:
            issues.append(f"Too few next_actions ({len(pack.next_actions)} < {cls.MIN_NEXT_ACTIONS})")

        age = time.time() - pack.timestamp
        if age > cls.MAX_AGE_SECONDS:
            issues.append(f"Pack too old ({age / 86400:.1f} days > 7 days)")

        if not pack.session_id:
            issues.append("Missing session_id")

        if not pack.agent:
            issues.append("Missing agent")

        # Compute quality score
        score = cls._compute_score(pack, age)
        pack.quality_score = score

        is_valid = len(issues) == 0 and score >= 0.5
        return is_valid, issues

    @classmethod
    def _compute_score(cls, pack: HandoffPack, age: float) -> float:
        """Compute a quality score for the pack (0.0-1.0)."""
        score = 0.0

        # Goals present
        if len(pack.goals) >= cls.MIN_GOALS:
            score += 0.2
        if len(pack.goals) >= 2:
            score += 0.1

        # Decisions present
        if len(pack.decisions) >= 1:
            score += 0.15
        if len(pack.decisions) >= 3:
            score += 0.05

        # Next actions present
        if len(pack.next_actions) >= cls.MIN_NEXT_ACTIONS:
            score += 0.2
        if len(pack.next_actions) >= 3:
            score += 0.1

        # Files present
        if len(pack.files) >= 1:
            score += 0.1

        # Risks present
        if len(pack.risks) >= 1:
            score += 0.1

        # Freshness
        if age < 86400:  # < 1 day
            score += 0.1
        elif age < 3 * 86400:  # < 3 days
            score += 0.05

        return min(1.0, score)


# ── HandoffPackManager ───────────────────────────────────────────────────────


class HandoffPackManager:
    """Manager for creating, storing, and retrieving handoff packs.

    Stores packs in a configurable directory (default: ~/.lilith/handoffs/).
    Each pack is saved as a JSON file.
    """

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        """Initialize the manager.

        Args:
            storage_dir: Directory to store handoff packs. Defaults to
                ~/.lilith/handoffs/
        """
        if storage_dir is None:
            home = Path.home()
            self.storage_dir = home / ".lilith" / "handoffs"
        else:
            self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("HandoffPackManager initialized: %s", self.storage_dir)

    def capture(
        self,
        session_id: str,
        agent: str,
        goals: list[str] | None = None,
        decisions: list[str] | None = None,
        risks: list[str] | None = None,
        preferences: list[str] | None = None,
        files: list[str] | None = None,
        next_actions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HandoffPack:
        """Capture a new handoff pack.

        Args:
            session_id: The session identifier.
            agent: The agent name that produced this pack.
            goals: What the agent was trying to achieve.
            decisions: Key decisions made.
            risks: Identified risks or blockers.
            preferences: User preferences discovered.
            files: Files touched or referenced.
            next_actions: What needs to happen next.
            metadata: Free-form additional context.

        Returns:
            The captured HandoffPack.
        """
        pack = HandoffPack(
            session_id=session_id,
            agent=agent,
            goals=goals or [],
            decisions=decisions or [],
            risks=risks or [],
            preferences=preferences or [],
            files=files or [],
            next_actions=next_actions or [],
            metadata=metadata or {},
        )

        # Auto-compute quality score
        is_valid, issues = HandoffQualityGate.validate(pack)
        if not is_valid:
            logger.warning("Handoff pack %s quality issues: %s", pack.pack_id, issues)
        else:
            logger.info("Handoff pack %s captured (quality=%.2f)", pack.pack_id, pack.quality_score)

        self._save(pack)
        return pack

    def resume(self, pack_id: str) -> dict[str, Any] | None:
        """Resume from a handoff pack.

        Args:
            pack_id: The pack identifier.

        Returns:
            A dict with all context needed to continue, or None if not found.
        """
        pack = self.get(pack_id)
        if pack is None:
            logger.warning("Handoff pack %s not found", pack_id)
            return None

        is_valid, issues = HandoffQualityGate.validate(pack)
        if not is_valid:
            logger.warning("Handoff pack %s failed quality gate: %s", pack_id, issues)
            return None

        logger.info("Resuming from handoff pack %s (quality=%.2f)", pack_id, pack.quality_score)
        return pack.to_dict()

    def get(self, pack_id: str) -> HandoffPack | None:
        """Get a handoff pack by ID."""
        path = self.storage_dir / f"{pack_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return HandoffPack.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error("Failed to load handoff pack %s: %s", pack_id, exc)
            return None

    def list_packs(self, agent: str | None = None) -> list[HandoffPack]:
        """List all handoff packs, optionally filtered by agent."""
        packs: list[HandoffPack] = []
        for path in self.storage_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pack = HandoffPack.from_dict(data)
                if agent is None or pack.agent.lower() == agent.lower():
                    packs.append(pack)
            except Exception as exc:
                logger.debug("Skipping invalid handoff file %s: %s", path.name, exc)
        packs.sort(key=lambda p: p.timestamp, reverse=True)
        return packs

    def delete(self, pack_id: str) -> bool:
        """Delete a handoff pack."""
        path = self.storage_dir / f"{pack_id}.json"
        if path.exists():
            path.unlink()
            logger.info("Deleted handoff pack %s", pack_id)
            return True
        return False

    def stats(self) -> dict[str, Any]:
        """Return statistics about stored handoff packs."""
        packs = self.list_packs()
        total = len(packs)
        avg_quality = sum(p.quality_score for p in packs) / total if total else 0.0
        by_agent: dict[str, int] = {}
        for p in packs:
            by_agent[p.agent] = by_agent.get(p.agent, 0) + 1
        return {
            "total_packs": total,
            "avg_quality": round(avg_quality, 2),
            "by_agent": by_agent,
            "storage_dir": str(self.storage_dir),
        }

    def _save(self, pack: HandoffPack) -> None:
        """Save a pack to disk."""
        path = self.storage_dir / f"{pack.pack_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pack.to_dict(), f, indent=2, ensure_ascii=False)

    def auto_capture_from_session(
        self,
        session_id: str,
        agent: str,
        session_messages: list[dict[str, Any]],
    ) -> HandoffPack | None:
        """Auto-capture a handoff pack from session messages.

        Extracts goals, decisions, files, and next actions from the
        conversation history using simple heuristics.

        Args:
            session_id: The session identifier.
            agent: The agent name.
            session_messages: List of message dicts with 'role' and 'content'.

        Returns:
            The captured HandoffPack, or None if nothing useful found.
        """
        goals: list[str] = []
        decisions: list[str] = []
        files: list[str] = []
        next_actions: list[str] = []
        risks: list[str] = []

        for msg in session_messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            lower = content.lower()

            # Goal detection
            if any(k in lower for k in ("goal:", "objective:", "aim:", "target:")):
                goals.append(content.strip())

            # Decision detection
            if any(k in lower for k in ("decided:", "decision:", "chose:", "opted for")):
                decisions.append(content.strip())

            # File detection
            if any(k in lower for k in ("file:", "created:", "modified:", "updated:")):
                # Simple file path extraction — look for common path patterns
                import re
                paths = re.findall(r"[\w/\\]+\.[\w]+", content)
                files.extend(paths)
            # Also detect files mentioned with path-like patterns
            if "/" in content or "\\" in content:
                import re
                paths = re.findall(r"[\w/\\]+\.[\w]+", content)
                files.extend(paths)

            # Next action detection
            if any(k in lower for k in ("next:", "todo:", "action:", "step:", "up next")):
                next_actions.append(content.strip())

            # Risk detection
            if any(k in lower for k in ("risk:", "warning:", "caution:", "blocker:")):
                risks.append(content.strip())

        # Deduplicate
        goals = list(dict.fromkeys(goals))
        decisions = list(dict.fromkeys(decisions))
        files = list(dict.fromkeys(files))
        next_actions = list(dict.fromkeys(next_actions))
        risks = list(dict.fromkeys(risks))

        if not goals and not next_actions and not decisions:
            logger.debug("No useful content to auto-capture from session %s", session_id)
            return None

        return self.capture(
            session_id=session_id,
            agent=agent,
            goals=goals,
            decisions=decisions,
            risks=risks,
            files=files,
            next_actions=next_actions,
            metadata={"auto_captured": True, "message_count": len(session_messages)},
        )
