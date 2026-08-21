"""LLM-assisted goal translation (V1.0).

:class:`LLMGoalPlanner` turns a natural-language request into a structured
:class:`~geonexus.agent.planner.Goal` using any **OpenAI-compatible**
``/chat/completions`` endpoint (OpenAI, DeepSeek, local vLLM/Ollama-compat
servers). The LLM only translates — execution always runs on the
deterministic planner and executor, so a wrong or hallucinated plan is
rejected by schema validation, not silently executed.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .planner import ExecutionError, Goal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are the goal translator of GeoNexus, a federated geospatial "
    "intelligence system. Translate the user's geospatial analysis request "
    "into ONE strict JSON object (no markdown, no commentary) matching this "
    "schema:\n"
    "{\n"
    '  "capability": string,      // required: the core capability, e.g. "ndvi", '
    '"change-detection", "classification"\n'
    '  "skill": string | null,    // optional: exact skill name to force\n'
    '  "spatial": {"bbox": [w, s, e, n], "crs": "EPSG:4326"} | null,\n'
    '  "temporal_steps": [{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}] | [],  '
    "// one entry per time period to analyze\n"
    '  "required_bands": ["B04", "B08"] | [],\n'
    '  "params": {key: value},    // skill arguments; string values\n'
    '  "steps": [...] | []        // optional pipeline (skill chaining) when '
    "the request needs composed operations\n"
    "}\n"
    "Only use capabilities and skill names from the provided list. If the "
    "request is unclear, make the most reasonable choice and keep params empty."
)


@dataclass
class LLMConfig:
    """OpenAI-compatible endpoint configuration."""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            base_url=os.environ.get("GEONEXUS_LLM_BASE_URL", cls.base_url),
            api_key=os.environ.get("GEONEXUS_LLM_API_KEY", ""),
            model=os.environ.get("GEONEXUS_LLM_MODEL", cls.model),
        )

    def is_configured(self) -> bool:
        return bool(self.api_key)


class LLMGoalPlanner:
    """Translates natural language into a structured :class:`Goal`."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config or LLMConfig.from_env()
        if not self.config.is_configured():
            raise ExecutionError(
                "LLM planning is not configured: set GEONEXUS_LLM_API_KEY "
                "(and optionally GEONEXUS_LLM_BASE_URL / GEONEXUS_LLM_MODEL), "
                "or pass an LLMConfig."
            )
        self._client = client or httpx.Client(
            base_url=self.config.base_url.rstrip("/"), timeout=60.0
        )

    # ------------------------------------------------------------------ #
    def plan(
        self,
        goal_text: str,
        available_capabilities: list[str] | None = None,
        available_skills: list[str] | None = None,
    ) -> Goal:
        """Translate ``goal_text`` into a validated :class:`Goal`.

        Raises :class:`ExecutionError` when the LLM output is not valid Goal
        JSON (after one repair retry).
        """
        capabilities = ", ".join(available_capabilities or []) or "ndvi, change-detection"
        skills = ", ".join(available_skills or []) or "ndvi-analysis, ndvi-change"
        user_prompt = (
            f"Available capabilities: {capabilities}\n"
            f"Available skills: {skills}\n\n"
            f"User request: {goal_text}\n\n"
            "Output ONLY the JSON goal object."
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        content = self._complete(messages)
        try:
            data = json.loads(content)
            return Goal(**data)
        except Exception as exc:  # noqa: BLE001 - JSON parse / validation, retry
            # One repair attempt: ask for strictly valid JSON.
            logger.warning("LLM output was not valid Goal JSON; retrying (%s)", exc)
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous output was not valid. Output ONE strict "
                        "JSON object matching the schema, nothing else."
                    ),
                }
            )
            content = self._complete(messages)
            try:
                data = json.loads(content)
                return Goal(**data)
            except Exception as exc2:  # noqa: BLE001 - final failure
                raise ExecutionError(
                    f"LLM returned invalid Goal JSON after retry: {exc2}\n"
                    f"Raw output: {content[:400]}"
                ) from exc2

    def _complete(self, messages: list[dict[str, Any]]) -> str:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.0,
        }
        # OpenAI-compatible JSON mode; unsupported servers ignore the field.
        payload["response_format"] = {"type": "json_object"}
        try:
            response = self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ExecutionError(f"LLM endpoint error: {exc}") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExecutionError(f"Unexpected LLM response shape: {exc}") from exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LLMGoalPlanner:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def plan_from_text(
    goal_text: str,
    available_capabilities: list[str] | None = None,
    available_skills: list[str] | None = None,
    config: LLMConfig | None = None,
    client: httpx.Client | None = None,
) -> Goal:
    """Translate natural language into a Goal (no registry access)."""
    with LLMGoalPlanner(config=config, client=client) as planner:
        return planner.plan(
            goal_text,
            available_capabilities=available_capabilities,
            available_skills=available_skills,
        )
