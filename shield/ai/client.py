"""Single chokepoint for Anthropic Claude calls.

Modes (config.AI_MODE):
  - "real":    real Anthropic API. Requires ANTHROPIC_API_KEY.
  - "fixture": canned responses from shield/ai/fixtures/. Offline-safe.

Every call returns (text, lineage_dict) so the caller can attach lineage
to the AI artifact it writes via RepositoryWriter.write_ai_artifact.
"""
from __future__ import annotations

import json
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
        # SDK-level retries cover transient 429 (rate limit) and 5xx
        # (server-side) errors with exponential backoff. Default is 2.
        # We bump to 4 because real engagements fire 5-10 AI calls in
        # quick succession (extraction, overlap, chat, posture, roadmap,
        # coverage) and rate-limit windows are easy to brush against.
        # RQ-level retry is deliberately NOT used — re-running the whole
        # job would write duplicate AI artifacts.
        self.max_retries = int(current_app.config.get("ANTHROPIC_MAX_RETRIES", 4))
        # Hard ceiling per attempt. Real Anthropic calls are typically
        # under 30 s; 120 s protects against pathological hangs without
        # cutting off legitimately slow Opus calls on large payloads.
        self.timeout_seconds = float(current_app.config.get("ANTHROPIC_TIMEOUT_SECONDS", 120))

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

        client = anthropic.Anthropic(
            api_key=self.api_key,
            max_retries=self.max_retries,
            timeout=self.timeout_seconds,
        )
        # Prompt caching: the per-platform system prompts (in ai/prompts/*.md)
        # are large and stable, so we mark them ephemeral-cacheable. The
        # first call within a 5-minute window pays full input cost; every
        # subsequent call hits cache at 10% of the input cost. The user
        # message varies per call so it is NOT cached.
        #
        # Stream the response. The non-streaming `messages.create` hangs
        # past ~5 minutes on long generations (P3's 16K output) — httpx /
        # SSL connections appear to time out somewhere in the chain even
        # when the SDK timeout is generous. Streaming sends bytes every
        # ~500 ms which keeps the connection actively used and lets the
        # SDK reconstruct the same final Message object via
        # `get_final_message()`. Verified live: P3 stream finishes in
        # ~2:45 vs >10 min hang for the non-streaming equivalent.
        with client.messages.stream(
            model=self.model,
            max_tokens=self.max_output,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        ) as stream:
            # Drain the stream — the SDK accumulates chunks internally
            # so `get_final_message()` returns a fully-populated Message.
            for _ in stream.text_stream:
                pass
            message = stream.get_final_message()
        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")

        if json_response:
            text = _coerce_json_block(text)

        usage = message.usage
        lineage = {
            "prompt_version": prompt_version,
            "model": self.model,
            "mode": self.mode,
            "produced_at": datetime.utcnow().isoformat() + "Z",
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            # Cache stats: how much of the input was cached vs. fresh.
            # cache_creation_input_tokens is non-zero on the first call that
            # creates the cache; cache_read_input_tokens is non-zero on
            # subsequent calls that hit it.
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
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
