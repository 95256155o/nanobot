# Discord Command Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Resume checkpoint (2026-03-19):** Paused before Task 1 dispatch. Baseline commit: `b8356da`. Branch: `intent-gate` in `~/nanobot-fork`. All 7 tasks pending. Resume by dispatching Task 1 subagent.

**Goal:** Add a `#command-center` Discord channel that classifies incoming messages, asks for emoji confirmation, then routes work to a fresh isolated thread with its own NanoBot session.

**Architecture:** `DiscordCommandCenterChannel` (subclass of `DiscordChannel`) runs as a second gateway consumer on the same token, handling only `command_center_channel_id`. The existing `discord` plugin gains an `ignore_channel_ids` field to skip that channel. A stateless `IntentClassifier` calls LiteLLM to classify text into structured intents. A `CommandCenterRouter` state machine manages the confirm-then-route flow in memory.

**Tech Stack:** Python 3.11+, asyncio, litellm (`acompletion`), discord REST API (httpx), pytest + pytest-asyncio, pydantic v2

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `nanobot/channels/discord.py` | Add `ignore_channel_ids` to `DiscordConfig`; filter in `_handle_message_create` |
| Create | `nanobot/intent/__init__.py` | Package marker |
| Create | `nanobot/intent/classifier.py` | `Intent`, `IntentResult`, `IntentClassifier` — stateless LLM classification |
| Create | `nanobot/channels/discord_command_center.py` | `CommandCenterConfig`, `PendingConfirmation`, `CommandCenterRouter`, `DiscordCommandCenterChannel` |
| Create | `tests/test_intent_classifier.py` | Unit tests for classifier parsing and model call |
| Create | `tests/test_discord_command_center.py` | Unit tests for router state machine, routing flow, error paths |
| Modify | `tests/test_channel_plugins.py` | Verify `discord_command_center` is auto-discovered |

**No changes to:** `registry.py` (auto-discovers), `schema.py` (extra="allow"), `session/manager.py`, `bus/`, `agent/`.

---

### Task 1: Add `ignore_channel_ids` to `DiscordChannel`

**Files:**
- Modify: `nanobot/channels/discord.py`
- Test: `tests/test_discord_command_center.py`

This prevents the normal `discord` plugin from processing `#command-center` messages that `DiscordCommandCenterChannel` owns.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discord_command_center.py
"""Tests for DiscordCommandCenterChannel and CommandCenterRouter."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel, DiscordConfig


async def _make_discord(ignore_ids: list[str]) -> DiscordChannel:
    config = DiscordConfig(
        enabled=True,
        token="test-token",
        allow_from=["user123"],
        ignore_channel_ids=ignore_ids,
    )
    bus = MessageBus()
    ch = DiscordChannel(config, bus)
    ch._http = AsyncMock()
    ch._bot_user_id = "bot999"
    return ch


@pytest.mark.asyncio
async def test_discord_ignores_specified_channel():
    """Messages in ignore_channel_ids must not reach the bus."""
    ch = await _make_discord(ignore_ids=["111222333"])
    published = []
    ch.bus.publish_inbound = AsyncMock(side_effect=lambda m: published.append(m))

    payload = {
        "id": "msg1",
        "channel_id": "111222333",
        "guild_id": "guild1",
        "author": {"id": "user123", "bot": False},
        "content": "hello",
        "attachments": [],
    }
    with patch.object(ch, "_start_typing", AsyncMock()):
        with patch.object(ch, "_add_reaction", AsyncMock()):
            await ch._handle_message_create(payload)

    assert len(published) == 0


@pytest.mark.asyncio
async def test_discord_processes_non_ignored_channel():
    """Messages NOT in ignore_channel_ids still reach the bus."""
    ch = await _make_discord(ignore_ids=["111222333"])
    published = []
    ch.bus.publish_inbound = AsyncMock(side_effect=lambda m: published.append(m))

    payload = {
        "id": "msg2",
        "channel_id": "999888777",
        "guild_id": "guild1",
        "author": {"id": "user123", "bot": False},
        "content": "do stuff",
        "attachments": [],
    }
    with patch.object(ch, "_start_typing", AsyncMock()):
        with patch.object(ch, "_add_reaction", AsyncMock()):
            await ch._handle_message_create(payload)

    assert len(published) == 1
    assert published[0].chat_id == "999888777"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd ~/nanobot-fork
pytest tests/test_discord_command_center.py::test_discord_ignores_specified_channel -v
```
Expected: `AttributeError` or `ValidationError` — `ignore_channel_ids` does not exist yet.

- [ ] **Step 3: Add `ignore_channel_ids` to `DiscordConfig`**

In `nanobot/channels/discord.py`, find `DiscordConfig` and add one field:

```python
class DiscordConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377
    group_policy: Literal["mention", "open"] = "mention"
    ignore_channel_ids: list[str] = Field(default_factory=list)  # ADD THIS
```

Then in `_handle_message_create`, add an early return **before** the `is_allowed` check:

```python
async def _handle_message_create(self, payload: dict[str, Any]) -> None:
    """Handle incoming Discord messages."""
    author = payload.get("author") or {}
    if author.get("bot"):
        return

    sender_id = str(author.get("id", ""))
    channel_id = str(payload.get("channel_id", ""))
    content = payload.get("content") or ""
    guild_id = payload.get("guild_id")

    # NEW: skip channels explicitly delegated to another plugin
    if channel_id in self.config.ignore_channel_ids:
        return

    # ... rest unchanged
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_discord_command_center.py::test_discord_ignores_specified_channel \
       tests/test_discord_command_center.py::test_discord_processes_non_ignored_channel -v
```
Expected: both PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest tests/ -v --tb=short
```
Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
cd ~/nanobot-fork
git add nanobot/channels/discord.py tests/test_discord_command_center.py
git commit -m "feat(discord): add ignore_channel_ids to DiscordConfig"
```

---

### Task 2: `IntentClassifier`

**Files:**
- Create: `nanobot/intent/__init__.py`
- Create: `nanobot/intent/classifier.py`
- Test: `tests/test_intent_classifier.py`

Stateless classifier. Takes raw text + model string, returns structured intents. No NanoBot session context, no config access. Caller resolves model before calling.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_intent_classifier.py
"""Tests for IntentClassifier."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from nanobot.intent.classifier import IntentClassifier, Intent, IntentResult

SAMPLE_RESPONSE = json.dumps({
    "primary": [
        {"label": "code_task", "description": "fix the login bug", "confidence": 0.88},
        {"label": "research",  "description": "find JWT libraries",  "confidence": 0.55},
    ],
    "side": [
        {"label": "memo", "description": "save progress note", "confidence": 0.91},
    ],
})


def _mock_completion(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_classify_returns_primary_and_side_intents():
    with patch("nanobot.intent.classifier.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = _mock_completion(SAMPLE_RESPONSE)
        clf = IntentClassifier()
        result = await clf.classify("fix the login bug and remind me later", "openrouter/test-model")

    assert isinstance(result, IntentResult)
    assert len(result.primary) == 2
    assert result.primary[0].label == "code_task"
    assert result.primary[0].confidence == pytest.approx(0.88)
    assert len(result.side) == 1
    assert result.side[0].label == "memo"


@pytest.mark.asyncio
async def test_classify_handles_malformed_json_gracefully():
    """Bad LLM output falls back to a low-confidence chat intent."""
    with patch("nanobot.intent.classifier.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = _mock_completion("This is not JSON at all.")
        clf = IntentClassifier()
        result = await clf.classify("hello", "openrouter/test-model")

    assert isinstance(result, IntentResult)
    assert len(result.primary) >= 1
    assert result.primary[0].label == "chat"
    assert result.primary[0].confidence < 0.5


@pytest.mark.asyncio
async def test_classify_passes_model_to_acompletion():
    with patch("nanobot.intent.classifier.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = _mock_completion(SAMPLE_RESPONSE)
        clf = IntentClassifier()
        await clf.classify("hello", "openrouter/my-model")

    call_kwargs = mock_ac.call_args
    assert call_kwargs.kwargs["model"] == "openrouter/my-model"


@pytest.mark.asyncio
async def test_classify_raises_on_api_error():
    with patch("nanobot.intent.classifier.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.side_effect = RuntimeError("API down")
        clf = IntentClassifier()
        with pytest.raises(RuntimeError, match="API down"):
            await clf.classify("hello", "openrouter/test-model")
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
pytest tests/test_intent_classifier.py -v
```
Expected: `ModuleNotFoundError: No module named 'nanobot.intent'`

- [ ] **Step 3: Create the package and classifier**

```python
# nanobot/intent/__init__.py
"""Intent classification package."""
```

```python
# nanobot/intent/classifier.py
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_intent_classifier.py -v
```
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add nanobot/intent/__init__.py nanobot/intent/classifier.py tests/test_intent_classifier.py
git commit -m "feat(intent): add stateless IntentClassifier with LiteLLM backend"
```

---

### Task 3: `CommandCenterConfig` + `PendingConfirmation`

**Files:**
- Create: `nanobot/channels/discord_command_center.py` (scaffold)
- Test: `tests/test_discord_command_center.py` (extend)

Define the config model and state dataclass. No routing logic yet.

- [ ] **Step 1: Write the failing config test**

Add to `tests/test_discord_command_center.py`:

```python
from nanobot.channels.discord_command_center import CommandCenterConfig


def test_command_center_config_defaults():
    cfg = CommandCenterConfig.model_validate({
        "enabled": True,
        "token": "tok",
        "allowFrom": ["user1"],
        "commandCenterChannelId": "cc123",
    })
    assert cfg.command_center_channel_id == "cc123"
    assert cfg.confirmation_timeout_s == 60
    assert cfg.classifier_model is None
    assert cfg.low_confidence_threshold == 0.5
    assert cfg.session_category_id is None


def test_command_center_config_timeout_clamped():
    """confirmationTimeoutS outside 10-300 is clamped."""
    cfg = CommandCenterConfig.model_validate({
        "enabled": True,
        "token": "tok",
        "allowFrom": ["u"],
        "commandCenterChannelId": "x",
        "confirmationTimeoutS": 5,
    })
    assert cfg.confirmation_timeout_s == 10


def test_command_center_config_timeout_clamped_high():
    cfg = CommandCenterConfig.model_validate({
        "enabled": True,
        "token": "tok",
        "allowFrom": ["u"],
        "commandCenterChannelId": "x",
        "confirmationTimeoutS": 999,
    })
    assert cfg.confirmation_timeout_s == 300
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_discord_command_center.py::test_command_center_config_defaults -v
```
Expected: `ModuleNotFoundError: nanobot.channels.discord_command_center`

- [ ] **Step 3: Create the scaffold**

```python
# nanobot/channels/discord_command_center.py
"""Discord Command Center channel — routing layer for #command-center."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, field_validator
from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel
from nanobot.config.schema import Base
from nanobot.intent.classifier import Intent, IntentClassifier, IntentResult

DISCORD_API_BASE = "https://discord.com/api/v10"
_REACTION_NUMBERS = ["1️⃣", "2️⃣", "3️⃣"]


class CommandCenterConfig(Base):
    """Configuration for the discord_command_center channel."""

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377
    command_center_channel_id: str = ""
    session_category_id: str | None = None
    confirmation_timeout_s: int = 60
    classifier_model: str | None = None
    low_confidence_threshold: float = 0.5

    @field_validator("confirmation_timeout_s", mode="before")
    @classmethod
    def clamp_timeout(cls, v: int) -> int:
        return max(10, min(300, int(v)))


@dataclass
class PendingConfirmation:
    original_message_id: str
    original_channel_id: str
    original_sender_id: str
    original_content: str
    intents: IntentResult
    confirmation_message_id: str
    timeout_handle: asyncio.TimerHandle
    guild_id: str | None = None
    reactions_posted: bool = False
    pending_reactions: list = field(default_factory=list)
```

- [ ] **Step 4: Run config tests**

```bash
pytest tests/test_discord_command_center.py::test_command_center_config_defaults \
       tests/test_discord_command_center.py::test_command_center_config_timeout_clamped \
       tests/test_discord_command_center.py::test_command_center_config_timeout_clamped_high -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add nanobot/channels/discord_command_center.py tests/test_discord_command_center.py
git commit -m "feat(command-center): scaffold config and PendingConfirmation"
```

---

### Task 4: Router — classify → confirm → timeout

**Files:**
- Modify: `nanobot/channels/discord_command_center.py`
- Test: `tests/test_discord_command_center.py`

Implement `CommandCenterRouter`: receive a message, call classifier, post intent menu, handle timeout. No thread creation yet.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_discord_command_center.py`:

```python
import asyncio
from unittest.mock import call as mock_call
from nanobot.channels.discord_command_center import (
    CommandCenterConfig,
    DiscordCommandCenterChannel,
)
from nanobot.intent.classifier import Intent, IntentResult


def _make_channel(cc_channel_id: str = "cc123") -> DiscordCommandCenterChannel:
    config = CommandCenterConfig.model_validate({
        "enabled": True,
        "token": "tok",
        "allowFrom": ["user1"],
        "commandCenterChannelId": cc_channel_id,
        "confirmationTimeoutS": 30,
    })
    bus = MessageBus()
    ch = DiscordCommandCenterChannel(config, bus)
    ch._http = AsyncMock()
    ch._bot_user_id = "bot999"
    return ch


@pytest.mark.asyncio
async def test_non_cc_message_is_discarded():
    """Messages from channels other than command_center_channel_id are silently dropped."""
    ch = _make_channel("cc123")
    published = []
    ch.bus.publish_inbound = AsyncMock(side_effect=lambda m: published.append(m))

    payload = {
        "id": "msg1", "channel_id": "other999", "guild_id": "g1",
        "author": {"id": "user1", "bot": False},
        "content": "hello", "attachments": [],
    }
    await ch._handle_message_create(payload)
    assert published == []


@pytest.mark.asyncio
async def test_low_confidence_posts_clarification():
    """If top intent confidence < threshold, post clarification, no pending state."""
    ch = _make_channel()
    low_result = IntentResult(
        primary=[Intent("chat", "unclear", 0.3)]
    )
    sent_messages = []
    ch._http.post = AsyncMock(return_value=MagicMock(
        status_code=200, raise_for_status=MagicMock()
    ))

    with patch("nanobot.channels.discord_command_center.IntentClassifier") as MockCLF:
        MockCLF.return_value.classify = AsyncMock(return_value=low_result)
        with patch.object(ch, "_add_reaction", AsyncMock()):
            with patch.object(ch, "_remove_reaction", AsyncMock()):
                with patch.object(ch, "_send_cc_message", AsyncMock()) as mock_send:
                    payload = {
                        "id": "msg1", "channel_id": "cc123", "guild_id": "g1",
                        "author": {"id": "user1", "bot": False},
                        "content": "hmm", "attachments": [],
                    }
                    await ch._handle_message_create(payload)

    mock_send.assert_called_once()
    msg_content = mock_send.call_args[0][0]
    assert "❓" in msg_content
    assert len(ch._router._pending) == 0


@pytest.mark.asyncio
async def test_high_confidence_creates_pending_confirmation():
    """Valid classification creates PendingConfirmation and posts intent menu."""
    ch = _make_channel()
    high_result = IntentResult(
        primary=[
            Intent("code_task", "fix the bug", 0.88),
            Intent("research", "look into it", 0.55),
        ]
    )

    with patch("nanobot.channels.discord_command_center.IntentClassifier") as MockCLF:
        MockCLF.return_value.classify = AsyncMock(return_value=high_result)
        with patch.object(ch, "_add_reaction", AsyncMock()):
            with patch.object(ch, "_remove_reaction", AsyncMock()):
                with patch.object(ch, "_send_cc_message", AsyncMock(return_value="menu_msg_id")):
                    with patch.object(ch._router, "_post_reaction_numbers", AsyncMock()):
                        payload = {
                            "id": "msg1", "channel_id": "cc123", "guild_id": "g1",
                            "author": {"id": "user1", "bot": False},
                            "content": "fix the login bug", "attachments": [],
                        }
                        await ch._handle_message_create(payload)

    assert len(ch._router._pending) == 1


@pytest.mark.asyncio
async def test_timeout_removes_pending_and_posts_message():
    """When confirmation times out, pending state is removed and timeout message is posted."""
    ch = _make_channel()
    high_result = IntentResult(
        primary=[Intent("code_task", "fix bug", 0.88)]
    )

    with patch("nanobot.channels.discord_command_center.IntentClassifier") as MockCLF:
        MockCLF.return_value.classify = AsyncMock(return_value=high_result)
        with patch.object(ch, "_add_reaction", AsyncMock()):
            with patch.object(ch, "_remove_reaction", AsyncMock()):
                with patch.object(ch, "_send_cc_message", AsyncMock(return_value="menu_id")):
                    with patch.object(ch._router, "_post_reaction_numbers", AsyncMock()):
                        payload = {
                            "id": "msg1", "channel_id": "cc123", "guild_id": "g1",
                            "author": {"id": "user1", "bot": False},
                            "content": "fix the login bug", "attachments": [],
                        }
                        await ch._handle_message_create(payload)

    # Manually trigger the timeout
    assert len(ch._router._pending) == 1
    pending = list(ch._router._pending.values())[0]
    with patch.object(ch, "_send_cc_message", AsyncMock()) as mock_send:
        await ch._router._on_timeout(pending.confirmation_message_id)

    assert len(ch._router._pending) == 0
    mock_send.assert_called_once()
    assert "⏱️" in mock_send.call_args[0][0]
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_discord_command_center.py::test_non_cc_message_is_discarded -v
```
Expected: `AttributeError` — `DiscordCommandCenterChannel` not yet defined.

- [ ] **Step 3: Implement the router classify→confirm→timeout flow**

Add to `nanobot/channels/discord_command_center.py`:

```python
class CommandCenterRouter:
    """In-memory state machine for pending intent confirmations."""

    def __init__(self, channel: "DiscordCommandCenterChannel"):
        self._channel = channel
        self._pending: dict[str, PendingConfirmation] = {}

    async def handle_message(
        self,
        message_id: str,
        channel_id: str,
        sender_id: str,
        content: str,
        guild_id: str | None,
        model: str,
    ) -> None:
        """Full classify→post-menu→arm-timeout flow."""
        ch = self._channel

        await ch._add_reaction(channel_id, message_id, "👀")

        try:
            result = await ch._classifier.classify(content, model)
        except Exception as e:
            logger.warning("CommandCenter: classification failed: {}", e)
            await ch._remove_reaction(channel_id, message_id, "👀")
            await ch._send_cc_message(f"⚠️ Classification failed. Try again.")
            return

        await ch._remove_reaction(channel_id, message_id, "👀")

        top_confidence = result.primary[0].confidence if result.primary else 0.0
        if top_confidence < ch.config.low_confidence_threshold:
            await ch._send_cc_message(
                "❓ Not sure what you need. Reply with one of: "
                "chat, code, research, write, memo, remind"
            )
            return

        # Build and post the intent menu
        lines = ["**Detected intent — react to confirm:**"]
        for i, intent in enumerate(result.primary[:3]):
            pct = int(intent.confidence * 100)
            lines.append(f"{_REACTION_NUMBERS[i]} **{intent.label}** — {intent.description} (~{pct}%)")
        if result.side:
            side_labels = ", ".join(s.label for s in result.side)
            lines.append(f"\n*Also detected: {side_labels}*")

        menu_msg_id = await ch._send_cc_message("\n".join(lines))
        if not menu_msg_id:
            return

        # Arm timeout
        loop = asyncio.get_event_loop()
        timeout_s = ch.config.confirmation_timeout_s

        pending = PendingConfirmation(
            original_message_id=message_id,
            original_channel_id=channel_id,
            original_sender_id=sender_id,
            original_content=content,
            intents=result,
            confirmation_message_id=menu_msg_id,
            timeout_handle=loop.call_later(
                timeout_s, lambda mid=menu_msg_id: asyncio.ensure_future(self._on_timeout(mid))
            ),
            guild_id=guild_id,
        )
        self._pending[menu_msg_id] = pending

        await self._post_reaction_numbers(channel_id, menu_msg_id, len(result.primary[:3]))

    async def _post_reaction_numbers(self, channel_id: str, message_id: str, count: int) -> None:
        """Post 1️⃣/2️⃣/3️⃣ reactions sequentially, then set reactions_posted=True."""
        pending = self._pending.get(message_id)
        for i in range(count):
            await self._channel._add_reaction(channel_id, message_id, _REACTION_NUMBERS[i])

        if pending:
            pending.reactions_posted = True
            for emoji in list(pending.pending_reactions):
                await self.handle_reaction(channel_id, message_id, pending.original_sender_id, emoji)
            pending.pending_reactions.clear()

    async def _on_timeout(self, confirmation_message_id: str) -> None:
        """Called when confirmation window expires."""
        self._pending.pop(confirmation_message_id, None)
        await self._channel._send_cc_message("⏱️ Timed out. Send again when ready.")

    async def handle_reaction(
        self,
        channel_id: str,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> None:
        """Process an incoming reaction. Ignores wrong user, wrong emoji, not-ready state."""
        pending = self._pending.get(message_id)
        if not pending:
            return
        if user_id != pending.original_sender_id:
            return
        if not pending.reactions_posted:
            pending.pending_reactions.append(emoji)
            return
        if emoji not in _REACTION_NUMBERS:
            return

        idx = _REACTION_NUMBERS.index(emoji)
        if idx >= len(pending.intents.primary):
            return

        # Valid selection — cancel timeout and route
        pending.timeout_handle.cancel()
        self._pending.pop(message_id, None)
        selected = pending.intents.primary[idx]
        await self._channel._route(pending, selected)


class DiscordCommandCenterChannel(DiscordChannel):
    """
    Discord channel plugin for #command-center routing.

    Handles ONLY command_center_channel_id. All other channels are discarded silently.
    Does NOT forward events to super() — the normal discord plugin handles everything else.
    """

    name = "discord_command_center"
    display_name = "Discord Command Center"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = CommandCenterConfig.model_validate(config)
        # Pass a DiscordConfig-compatible view to super().__init__
        from nanobot.channels.discord import DiscordConfig
        discord_cfg = DiscordConfig(
            enabled=config.enabled,
            token=config.token,
            allow_from=config.allow_from,
            gateway_url=config.gateway_url,
            intents=config.intents,
        )
        super().__init__(discord_cfg, bus)
        self.config: CommandCenterConfig = config  # override with richer config
        self._classifier = IntentClassifier()
        self._router = CommandCenterRouter(self)

    def _resolved_model(self) -> str:
        """Return the classifier model, falling back to the agent's default."""
        if self.config.classifier_model:
            return self.config.classifier_model
        # Read from global nanobot config if available; otherwise use a safe default
        try:
            from nanobot.config.loader import get_config
            cfg = get_config()
            return cfg.agents.defaults.model
        except Exception:
            return "anthropic/claude-haiku-4-5-20251001"

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        """Override: only process command_center_channel_id; discard everything else."""
        author = payload.get("author") or {}
        if author.get("bot"):
            return

        channel_id = str(payload.get("channel_id", ""))
        if channel_id != self.config.command_center_channel_id:
            return  # not our channel — discard silently

        sender_id = str((payload.get("author") or {}).get("id", ""))
        if not self.is_allowed(sender_id):
            return

        content = payload.get("content") or ""
        guild_id = payload.get("guild_id")
        message_id = str(payload.get("id", ""))

        await self._router.handle_message(
            message_id=message_id,
            channel_id=channel_id,
            sender_id=sender_id,
            content=content,
            guild_id=guild_id,
            model=self._resolved_model(),
        )

    async def _handle_reaction_add(self, payload: dict[str, Any]) -> None:
        """Handle MESSAGE_REACTION_ADD events."""
        channel_id = str(payload.get("channel_id", ""))
        if channel_id != self.config.command_center_channel_id:
            return

        user_id = str((payload.get("member") or {}).get("user", {}).get("id", "")
                      or payload.get("user_id", ""))
        if user_id == self._bot_user_id:
            return  # ignore own reactions

        message_id = str(payload.get("message_id", ""))
        emoji_data = payload.get("emoji") or {}
        emoji_name = emoji_data.get("name", "")

        await self._router.handle_reaction(channel_id, message_id, user_id, emoji_name)

    async def _send_cc_message(self, content: str) -> str | None:
        """Post a message to #command-center. Returns message_id on success, None on failure."""
        if not self._http:
            return None
        url = f"{DISCORD_API_BASE}/channels/{self.config.command_center_channel_id}/messages"
        headers = {"Authorization": f"Bot {self.config.token}"}
        try:
            resp = await self._http.post(url, headers=headers, json={"content": content})
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("id", ""))
        except Exception as e:
            logger.warning("CommandCenter: failed to post message: {}", e)
            return None

    async def _route(self, pending: PendingConfirmation, selected: Intent) -> None:
        """Create thread/channel and publish the routed InboundMessage. Stub for Task 5."""
        raise NotImplementedError("Routing not yet implemented — see Task 5")
```

Also override `_gateway_loop` to dispatch reaction events. Add this method to `DiscordCommandCenterChannel`:

```python
    async def _gateway_loop(self) -> None:
        """Extended gateway loop that also dispatches MESSAGE_REACTION_ADD."""
        if not self._ws:
            return

        import json as _json
        async for raw in self._ws:
            try:
                data = _json.loads(raw)
            except _json.JSONDecodeError:
                logger.warning("CC: invalid JSON from gateway: {}", raw[:100])
                continue

            op = data.get("op")
            event_type = data.get("t")
            seq = data.get("s")
            payload = data.get("d")

            if seq is not None:
                self._seq = seq

            if op == 10:
                interval_ms = payload.get("heartbeat_interval", 45000)
                await self._start_heartbeat(interval_ms / 1000)
                await self._identify()
            elif op == 0 and event_type == "READY":
                user_data = payload.get("user") or {}
                self._bot_user_id = user_data.get("id")
                logger.info("Discord CC gateway READY as {}", self._bot_user_id)
            elif op == 0 and event_type == "MESSAGE_CREATE":
                await self._handle_message_create(payload)
            elif op == 0 and event_type == "MESSAGE_REACTION_ADD":
                await self._handle_reaction_add(payload)
            elif op == 7:
                logger.info("Discord CC gateway: reconnect requested")
                break
            elif op == 9:
                logger.warning("Discord CC gateway: invalid session")
                break
```

- [ ] **Step 4: Run the router tests**

```bash
pytest tests/test_discord_command_center.py -v -k "non_cc or low_confidence or high_confidence or timeout"
```
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add nanobot/channels/discord_command_center.py tests/test_discord_command_center.py
git commit -m "feat(command-center): add router classify→confirm→timeout flow"
```

---

### Task 5: Routing — thread/channel creation + session handoff

**Files:**
- Modify: `nanobot/channels/discord_command_center.py` (implement `_route`)
- Test: `tests/test_discord_command_center.py`

Replace the `_route` stub with full thread creation (with channel fallback), handoff message, and `InboundMessage` publication.

- [ ] **Step 1: Write the failing routing tests**

Add to `tests/test_discord_command_center.py`:

```python
from nanobot.channels.discord_command_center import PendingConfirmation
from nanobot.intent.classifier import Intent, IntentResult


def _make_pending(ch: DiscordCommandCenterChannel) -> PendingConfirmation:
    loop = asyncio.get_event_loop()
    return PendingConfirmation(
        original_message_id="orig1",
        original_channel_id="cc123",
        original_sender_id="user1",
        original_content="fix the login bug in auth.py",
        intents=IntentResult(primary=[Intent("code_task", "fix auth", 0.88)]),
        confirmation_message_id="menu1",
        timeout_handle=loop.call_later(60, lambda: None),
        guild_id="guild1",
    )


@pytest.mark.asyncio
async def test_route_creates_thread_and_publishes_message():
    """Happy path: thread is created and InboundMessage is published."""
    ch = _make_channel()
    pending = _make_pending(ch)
    pending.timeout_handle.cancel()

    thread_response = MagicMock(
        status_code=200,
        raise_for_status=MagicMock(),
        json=MagicMock(return_value={"id": "thread999"}),
    )
    # POST /threads succeeds
    ch._http.post = AsyncMock(return_value=thread_response)

    published = []
    ch.bus.publish_inbound = AsyncMock(side_effect=lambda m: published.append(m))

    selected = Intent("code_task", "fix auth", 0.88)
    with patch.object(ch, "_send_cc_message", AsyncMock()):
        await ch._route(pending, selected)

    assert len(published) == 1
    msg = published[0]
    assert msg.chat_id == "thread999"
    assert msg.content == "fix the login bug in auth.py"
    assert msg.session_key_override is None


@pytest.mark.asyncio
async def test_route_falls_back_to_channel_on_403():
    """Thread creation 403 → falls back to channel creation without retry."""
    ch = _make_channel()
    pending = _make_pending(ch)
    pending.timeout_handle.cancel()

    # Category configured for fallback
    ch.config = CommandCenterConfig.model_validate({
        "enabled": True, "token": "tok", "allowFrom": ["user1"],
        "commandCenterChannelId": "cc123", "sessionCategoryId": "cat456",
    })

    forbidden = MagicMock(status_code=403)
    forbidden.raise_for_status.side_effect = Exception("403 Forbidden")
    channel_ok = MagicMock(
        status_code=200, raise_for_status=MagicMock(),
        json=MagicMock(return_value={"id": "newchan888"}),
    )
    call_count = {"n": 0}

    async def _mock_post(url, **kwargs):
        call_count["n"] += 1
        if "threads" in url:
            return forbidden
        return channel_ok

    ch._http.post = _mock_post

    published = []
    ch.bus.publish_inbound = AsyncMock(side_effect=lambda m: published.append(m))

    selected = Intent("code_task", "fix auth", 0.88)
    with patch.object(ch, "_send_cc_message", AsyncMock()):
        await ch._route(pending, selected)

    # Only one thread attempt (no retry on 403), then one channel creation
    assert call_count["n"] == 2
    assert len(published) == 1
    assert published[0].chat_id == "newchan888"


@pytest.mark.asyncio
async def test_route_posts_error_when_both_fail():
    """If thread and channel creation both fail, error is posted in #command-center."""
    ch = _make_channel()
    pending = _make_pending(ch)
    pending.timeout_handle.cancel()

    fail_resp = MagicMock(status_code=500)
    fail_resp.raise_for_status.side_effect = Exception("500")
    ch._http.post = AsyncMock(return_value=fail_resp)

    published = []
    ch.bus.publish_inbound = AsyncMock(side_effect=lambda m: published.append(m))

    selected = Intent("code_task", "fix auth", 0.88)
    with patch.object(ch, "_send_cc_message", AsyncMock()) as mock_send:
        await ch._route(pending, selected)

    assert len(published) == 0
    msg = mock_send.call_args[0][0]
    assert "⚠️" in msg
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_discord_command_center.py::test_route_creates_thread_and_publishes_message -v
```
Expected: `NotImplementedError` (stub).

- [ ] **Step 3: Implement `_route`**

Replace the stub in `DiscordCommandCenterChannel`:

```python
    async def _route(self, pending: PendingConfirmation, selected: Intent) -> None:
        """Create thread (or channel fallback), post handoff, publish InboundMessage."""
        thread_id = await self._create_thread(pending, selected)
        if thread_id is None:
            await self._send_cc_message("⚠️ Could not create session channel.")
            return

        # Post handoff message in the new thread
        handoff = f"▶ {selected.label} — {pending.original_content[:80]}"
        if pending.intents.side:
            side_labels = ", ".join(s.label for s in pending.intents.side)
            handoff += f"\n⏳ Side actions will attempt: {side_labels}"
        await self._post_to_channel(thread_id, handoff)

        # Publish the original message — agent responds directly to user's request
        from nanobot.bus.events import InboundMessage
        msg = InboundMessage(
            channel=self.name,
            sender_id=pending.original_sender_id,
            chat_id=thread_id,
            content=pending.original_content,
            session_key_override=None,
        )
        await self.bus.publish_inbound(msg)

        # Confirm routing in #command-center
        await self._send_cc_message(f"✅ Routed to <#{thread_id}>")

        # Execute side intents best-effort (after routing succeeds)
        if pending.intents.side:
            await self._execute_side_intents(pending, thread_id)

    async def _create_thread(
        self, pending: PendingConfirmation, selected: Intent
    ) -> str | None:
        """Try to create a thread; fall back to channel. Returns new ID or None."""
        if not self._http:
            return None

        headers = {"Authorization": f"Bot {self.config.token}"}
        slug = selected.label.replace("_", "-")

        # Attempt 1: public thread on the original message
        thread_url = (
            f"{DISCORD_API_BASE}/channels/"
            f"{self.config.command_center_channel_id}/threads"
        )
        try:
            resp = await self._http.post(
                thread_url,
                headers=headers,
                json={
                    "name": f"{slug}-{pending.original_message_id[:6]}",
                    "auto_archive_duration": 1440,
                    "message_id": pending.original_message_id,
                },
            )
            if resp.status_code == 403:
                logger.info("CC: MANAGE_THREADS not granted, falling back to channel")
            elif resp.status_code >= 500:
                # Retry once for transient errors
                resp = await self._http.post(
                    thread_url, headers=headers,
                    json={
                        "name": f"{slug}-{pending.original_message_id[:6]}",
                        "auto_archive_duration": 1440,
                        "message_id": pending.original_message_id,
                    },
                )
                if resp.status_code < 300:
                    return str(resp.json().get("id", ""))
            else:
                resp.raise_for_status()
                return str(resp.json().get("id", ""))
        except Exception as e:
            logger.warning("CC: thread creation failed: {}", e)

        # Fallback: create a channel in the category
        if not self.config.session_category_id or not pending.guild_id:
            logger.warning("CC: no sessionCategoryId configured, cannot fall back to channel")
            return None

        chan_url = f"{DISCORD_API_BASE}/guilds/{pending.guild_id}/channels"
        try:
            resp = await self._http.post(
                chan_url,
                headers=headers,
                json={
                    "name": f"{slug}-{pending.original_message_id[:6]}",
                    "type": 0,  # GUILD_TEXT
                    "parent_id": self.config.session_category_id,
                },
            )
            resp.raise_for_status()
            return str(resp.json().get("id", ""))
        except Exception as e:
            logger.warning("CC: channel fallback creation failed: {}", e)
            return None

    async def _post_to_channel(self, channel_id: str, content: str) -> None:
        """Post a message to any channel by ID."""
        if not self._http:
            return
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {self.config.token}"}
        try:
            resp = await self._http.post(url, headers=headers, json={"content": content})
            resp.raise_for_status()
        except Exception as e:
            logger.warning("CC: failed to post to channel {}: {}", channel_id, e)
```

- [ ] **Step 4: Run routing tests**

```bash
pytest tests/test_discord_command_center.py -v -k "route"
```
Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add nanobot/channels/discord_command_center.py tests/test_discord_command_center.py
git commit -m "feat(command-center): implement thread/channel routing with session handoff"
```

---

### Task 6: Side intents — best-effort direct dispatch

**Files:**
- Modify: `nanobot/channels/discord_command_center.py`
- Test: `tests/test_discord_command_center.py`

Side intents (`memo`, `remind`) are executed as direct async function calls — **not** through the agent bus — to avoid writing into the fresh session. For the first PR they append to a local text log and post a confirmation in `#command-center`. This satisfies Option B from the spec (direct tool dispatch, no conversation loop).

- [ ] **Step 1: Write the failing side intent test**

Add to `tests/test_discord_command_center.py`:

```python
@pytest.mark.asyncio
async def test_side_intents_do_not_contaminate_thread_session():
    """Side intents must not publish to the routed thread_id."""
    ch = _make_channel()
    pending = _make_pending(ch)
    pending.intents.side = [Intent("memo", "save progress note", 0.92)]
    pending.timeout_handle.cancel()

    thread_response = MagicMock(
        status_code=200, raise_for_status=MagicMock(),
        json=MagicMock(return_value={"id": "thread999"}),
    )
    ch._http.post = AsyncMock(return_value=thread_response)

    all_published = []
    ch.bus.publish_inbound = AsyncMock(side_effect=lambda m: all_published.append(m))

    selected = Intent("code_task", "fix auth", 0.88)
    with patch.object(ch, "_send_cc_message", AsyncMock()):
        await ch._route(pending, selected)

    # Only one InboundMessage: the routed main intent
    assert len(all_published) == 1
    assert all_published[0].chat_id == "thread999"


@pytest.mark.asyncio
async def test_side_intent_failure_reports_without_blocking():
    """A failing side intent posts a warning in #command-center but does not affect routing."""
    ch = _make_channel()
    pending = _make_pending(ch)
    pending.intents.side = [Intent("memo", "note something", 0.90)]
    pending.timeout_handle.cancel()

    thread_ok = MagicMock(
        status_code=200, raise_for_status=MagicMock(),
        json=MagicMock(return_value={"id": "thread999"}),
    )
    ch._http.post = AsyncMock(return_value=thread_ok)
    ch.bus.publish_inbound = AsyncMock()

    warning_messages = []

    async def capture_send(content):
        warning_messages.append(content)

    selected = Intent("code_task", "fix auth", 0.88)
    with patch.object(ch, "_send_cc_message", AsyncMock(side_effect=capture_send)):
        with patch.object(ch, "_execute_side_intents", AsyncMock(side_effect=Exception("memo boom"))):
            await ch._route(pending, selected)

    # Routing still published
    assert ch.bus.publish_inbound.called
    # Error was reported in #command-center (spec Section 8: "Side intent fails")
    assert any("⚠️" in m for m in warning_messages)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_discord_command_center.py -v -k "side_intent"
```
Expected: FAIL — `_execute_side_intents` not yet implemented.

- [ ] **Step 3: Implement `_execute_side_intents`**

Add to `DiscordCommandCenterChannel`:

```python
    async def _execute_side_intents(
        self, pending: PendingConfirmation, thread_id: str
    ) -> None:
        """
        Execute side intents as direct function calls (Option B).

        Does NOT publish to the agent bus — avoids contaminating the fresh session.
        Each intent is logged locally and confirmed in #command-center.
        Failures are reported individually without blocking.
        """
        from pathlib import Path
        from datetime import datetime

        side_log = Path.home() / ".nanobot" / "side_intents.log"
        side_log.parent.mkdir(parents=True, exist_ok=True)

        for intent in pending.intents.side:
            try:
                timestamp = datetime.now().isoformat()
                entry = f"[{timestamp}] [{intent.label}] {pending.original_content}\n"
                with open(side_log, "a", encoding="utf-8") as f:
                    f.write(entry)
                label_display = "📝 Memo" if intent.label == "memo" else "⏰ Reminder"
                await self._send_cc_message(
                    f"{label_display} saved: {pending.original_content[:60]}"
                )
            except Exception as e:
                logger.warning("CC: side intent [{}] failed: {}", intent.label, e)
                await self._send_cc_message(f"⚠️ Side action [{intent.label}] failed.")
```

Also wrap the `_execute_side_intents` call in `_route` with a try/except to ensure failures never propagate:

```python
        # Execute side intents best-effort (after routing succeeds)
        if pending.intents.side:
            try:
                await self._execute_side_intents(pending, thread_id)
            except Exception as e:
                logger.warning("CC: side intent execution failed: {}", e)
                await self._send_cc_message(
                    f"⚠️ Side action failed: {e}"
                )
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_discord_command_center.py tests/test_intent_classifier.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add nanobot/channels/discord_command_center.py tests/test_discord_command_center.py
git commit -m "feat(command-center): add best-effort side intent dispatch (Option B)"
```

---

### Task 7: Auto-discovery verification + config docs

**Files:**
- Test: `tests/test_channel_plugins.py` (extend)
- Create: `docs/discord-command-center.md`

Verify the new channel is auto-discovered, and write the minimal user-facing setup doc.

- [ ] **Step 1: Add auto-discovery test**

Add to `tests/test_channel_plugins.py`:

```python
def test_discord_command_center_is_auto_discovered():
    """discord_command_center must appear in discover_channel_names()."""
    from nanobot.channels.registry import discover_channel_names
    names = discover_channel_names()
    assert "discord_command_center" in names


def test_discord_command_center_channel_class_loads():
    """load_channel_class must return DiscordCommandCenterChannel."""
    from nanobot.channels.registry import load_channel_class
    from nanobot.channels.discord_command_center import DiscordCommandCenterChannel
    cls = load_channel_class("discord_command_center")
    assert cls is DiscordCommandCenterChannel
```

- [ ] **Step 2: Run to confirm they pass immediately**

```bash
pytest tests/test_channel_plugins.py::test_discord_command_center_is_auto_discovered \
       tests/test_channel_plugins.py::test_discord_command_center_channel_class_loads -v
```
Expected: both PASS (no code changes needed — auto-discovery just works).

- [ ] **Step 3: Run the full suite**

```bash
pytest tests/ -v --tb=short
```
Expected: all tests pass. Fix any regressions before proceeding.

- [ ] **Step 4: Write setup doc**

Create `docs/discord-command-center.md`:

```markdown
# Discord Command Center Setup

## What It Does

`#command-center` is a low-context routing channel. Each message you send there is classified into intents. You confirm with an emoji reaction, and work moves into a fresh Discord thread with isolated NanoBot context.

## Config (`~/.nanobot/config.json`)

Add alongside your existing `discord` block:

```json
"discord": {
  "enabled": true,
  "token": "<your_bot_token>",
  "allowFrom": ["<your_discord_user_id>"],
  "groupPolicy": "open",
  "ignoreChannelIds": ["<command_center_channel_id>"]
},
"discord_command_center": {
  "enabled": true,
  "token": "<same_bot_token>",
  "allowFrom": ["<your_discord_user_id>"],
  "commandCenterChannelId": "<channel_id>",
  "sessionCategoryId": null,
  "confirmationTimeoutS": 60,
  "classifierModel": null,
  "lowConfidenceThreshold": 0.5
}
```

- `commandCenterChannelId`: right-click `#command-center` → Copy Channel ID (requires Developer Mode)
- `sessionCategoryId`: optional Discord category ID for channel fallback (requires `MANAGE_CHANNELS`)
- `classifierModel`: `null` uses your agent's configured model; override with any LiteLLM model string
- `ignoreChannelIds` in `discord`: must include the same channel ID to prevent double-handling

## Required Bot Permissions

- `GUILD_MESSAGES` + `MESSAGE_CONTENT` (already needed for normal Discord)
- `GUILD_MESSAGE_REACTIONS` (for emoji confirmation)
- `CREATE_PUBLIC_THREADS` (for thread creation — preferred)
- `MANAGE_CHANNELS` (optional, for channel fallback only)

## How It Works

1. Type a message in `#command-center`
2. Bot classifies and shows top intents with 1️⃣/2️⃣/3️⃣
3. React to confirm (60s window)
4. Bot creates a thread and routes your original message there
5. Continue the conversation in the thread — it has its own isolated session
```

- [ ] **Step 5: Final commit**

```bash
git add tests/test_channel_plugins.py docs/discord-command-center.md
git commit -m "docs: add discord-command-center setup guide and discovery tests"
```

---

## Manual Smoke Test (after all tasks complete)

1. SSH into Dell: `ssh -i ~/.ssh/id_ed25519 jl@192.168.5.168`
2. Back up: `cp -r ~/nanobot-jl ~/nanobot-jl-backup-$(date +%Y%m%d)`
3. Pull intent-gate to Dell: from WSL, `git push origin intent-gate` then on Dell `cd ~/nanobot-jl && git fetch origin && git checkout intent-gate && git pull`
4. Add `ignoreChannelIds` and `discord_command_center` blocks to `~/.nanobot/config.json` (get `#command-center` channel ID from Discord)
5. Restart: `systemctl --user restart nanobot-gateway`
6. In Discord: type a mixed-intent message in `#command-center`
7. Verify: 👀 appears → intent menu posted → react 1️⃣ → thread created → ✅ confirmation
8. Verify: future messages in the thread get agent responses; `#command-center` shows no agent replies
