"""Stateless intent classifier using LiteLLM."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from litellm import acompletion
from loguru import logger

KNOWN_PRIMARY = frozenset({"code_task", "research", "write", "chat", "system_cmd"})
KNOWN_SIDE = frozenset({"remind", "memo"})

_SYSTEM_PROMPT = """\
You are an intent classifier. Given a user message, return a JSON object with:
- "primary": list of up to 3 routing intents, sorted by confidence descending
- "side": list of background action intents (only "remind" or "memo" if present)

Each intent has: "label" (string), "description" (short phrase), "confidence" (0.0-1.0).

Primary labels: code_task, research, write, chat, system_cmd
Side labels: remind, memo

Respond ONLY with valid JSON. No prose.

Example:
{
  "primary": [
    {"label": "code_task", "description": "fix the auth bug", "confidence": 0.88},
    {"label": "research",  "description": "look into JWT libs", "confidence": 0.55}
  ],
  "side": [
    {"label": "memo", "description": "save progress note", "confidence": 0.91}
  ]
}
"""


@dataclass
class Intent:
    label: str
    description: str
    confidence: float


@dataclass
class IntentResult:
    primary: list[Intent] = field(default_factory=list)
    side: list[Intent] = field(default_factory=list)


_FALLBACK = IntentResult(
    primary=[Intent(label="chat", description="unclear intent", confidence=0.3)]
)


class IntentClassifier:
    """Stateless intent classifier. Caller must resolve model string before calling."""

    async def classify(self, text: str, model: str) -> IntentResult:
        """
        Classify *text* using *model*.

        Raises the underlying LiteLLM exception on API failure.
        Falls back to a low-confidence 'chat' intent on JSON parse failure.
        """
        response = await acompletion(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        raw = response.choices[0].message.content or ""
        return self._parse(raw)

    def _parse(self, raw: str) -> IntentResult:
        """Parse LLM JSON output. Returns fallback on any parse error."""
        try:
            data = json.loads(raw)
            primary = [
                Intent(
                    label=str(i.get("label", "chat")),
                    description=str(i.get("description", "")),
                    confidence=float(i.get("confidence", 0.0)),
                )
                for i in (data.get("primary") or [])
            ]
            side = [
                Intent(
                    label=str(i.get("label", "")),
                    description=str(i.get("description", "")),
                    confidence=float(i.get("confidence", 0.0)),
                )
                for i in (data.get("side") or [])
                if i.get("label") in KNOWN_SIDE
            ]
            if not primary:
                return _FALLBACK
            return IntentResult(primary=primary[:3], side=side)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning("IntentClassifier: parse failed ({}), using fallback", e)
            return _FALLBACK
