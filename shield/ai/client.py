"""Single chokepoint for Anthropic Claude calls.

Modes (config.AI_MODE):
  - "real":    real Anthropic API. Requires ANTHROPIC_API_KEY.
  - "fixture": canned responses from shield/ai/fixtures/. Offline-safe.

Every call returns (text, lineage_dict) so the caller can attach lineage
to the AI artifact it writes via RepositoryWriter.write_ai_artifact.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app


class AIError(RuntimeError):
    pass


class AIClient:
    def __init__(self, *, api_key: str | None = None, model: str | None = None, mode: str | None = None) -> None:
        self.api_key = api_key or current_app.config.get("ANTHROPIC_API_KEY", "")
        self.model = model or current_app.config.get("ANTHROPIC_MODEL_APP", "claude-opus-4-7")
        self.mode = mode or current_app.config.get("AI_MODE", "real")
        self.max_output = int(current_app.config.get("ANTHROPIC_MAX_OUTPUT_TOKENS", 4096))

    # --------------------------------------------------------------
    def complete(
        self,
        *,
        system: str,
        user: str,
        prompt_version: str,
        json_response: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        if self.mode == "fixture":
            return self._fixture(prompt_version)

        if not self.api_key:
            raise AIError("ANTHROPIC_API_KEY not set. Put it in .env or set AI_MODE=fixture.")

        try:
            import anthropic
        except ImportError as e:
            raise AIError("anthropic SDK not installed") from e

        client = anthropic.Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_output,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")

        if json_response:
            text = _coerce_json_block(text)

        lineage = {
            "prompt_version": prompt_version,
            "model": self.model,
            "mode": self.mode,
            "produced_at": datetime.utcnow().isoformat() + "Z",
            "input_tokens": getattr(message.usage, "input_tokens", None),
            "output_tokens": getattr(message.usage, "output_tokens", None),
        }
        return text, lineage

    # --------------------------------------------------------------
    def _fixture(self, prompt_version: str) -> tuple[str, dict[str, Any]]:
        path = Path(__file__).parent / "fixtures" / f"{prompt_version}.json"
        if not path.exists():
            return (
                json.dumps({"note": f"No fixture for {prompt_version}. Returning empty result."}),
                {"prompt_version": prompt_version, "model": "fixture", "mode": "fixture",
                 "produced_at": datetime.utcnow().isoformat() + "Z"},
            )
        text = path.read_text(encoding="utf-8")
        return text, {
            "prompt_version": prompt_version, "model": "fixture", "mode": "fixture",
            "produced_at": datetime.utcnow().isoformat() + "Z",
            "fixture_path": str(path.relative_to(Path(__file__).parent)),
        }


def _coerce_json_block(text: str) -> str:
    """Strip code fences if Claude returned ```json ... ```; otherwise leave intact."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        # might still start with "json\n"
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    return s
