"""Discord Command Center channel — routing layer for #command-center."""

from __future__ import annotations

import asyncio
import json as _json
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, field_validator
from loguru import logger
from litellm import acompletion

from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel, DiscordConfig
from nanobot.config.schema import Base
from nanobot.intent.classifier import Intent, IntentClassifier, IntentResult

DISCORD_API_BASE = "https://discord.com/api/v10"


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
    guild_id: str | None = None


class CommandCenterRouter:
    """In-memory state machine for pending intent confirmations."""

    def __init__(self, channel: DiscordCommandCenterChannel):
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
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Classify intent and route directly — no confirmation menu."""
        ch = self._channel

        await ch._add_reaction(channel_id, message_id, "\U0001f440")

        try:
            result = await ch._classifier.classify(content, model, api_key=api_key, api_base=api_base)
        except Exception as e:
            logger.warning("CommandCenter: classification failed: {}", e)
            await ch._remove_reaction(channel_id, message_id, "\U0001f440")
            await ch._send_cc_message("\u26a0\ufe0f Classification failed. Try again.")
            return

        await ch._remove_reaction(channel_id, message_id, "\U0001f440")

        top = result.primary[0] if result.primary else None
        if not top or top.confidence < 0.3:
            await ch._send_cc_message(
                "I'm not here for messing around. "
                "If you want to chat, head over to the chat channel \U0001f44b"
            )
            return

        # Post brief confirmation and route directly
        pct = int(top.confidence * 100)
        await ch._send_cc_message(f"\U0001f3af **{top.label}** (~{pct}%) — routing...")

        pending = PendingConfirmation(
            original_message_id=message_id,
            original_channel_id=channel_id,
            original_sender_id=sender_id,
            original_content=content,
            intents=result,
            guild_id=guild_id,
        )
        await ch._route(pending, top)



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
        # Track sub-channels for auto-rename: channel_id -> [recent messages]
        self._created_channels: dict[str, list[str]] = {}
        self._renamed_channels: set[str] = set()

    def _resolved_classifier_params(self) -> dict[str, str | None]:
        """Resolve model, api_key, and api_base for the classifier from nanobot config."""
        model = self.config.classifier_model
        api_key: str | None = None
        api_base: str | None = None

        try:
            from nanobot.config.loader import load_config
            from nanobot.providers.registry import find_by_name
            cfg = load_config()

            if not model:
                model = cfg.agents.defaults.model

            api_key = cfg.get_api_key(model)
            api_base = cfg.get_api_base(model)

            # Apply litellm prefix (e.g. openrouter/ for OpenRouter provider)
            provider_name = cfg.get_provider_name(model)
            if provider_name:
                spec = find_by_name(provider_name)
                if spec and spec.litellm_prefix and not model.startswith(f"{spec.litellm_prefix}/"):
                    model = f"{spec.litellm_prefix}/{model}"
        except Exception:
            model = model or "anthropic/claude-haiku-4-5-20251001"

        return {"model": model, "api_key": api_key, "api_base": api_base}

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        """Process command_center_channel_id for routing, and sub-channels for rename."""
        author = payload.get("author") or {}
        if author.get("bot"):
            return

        channel_id = str(payload.get("channel_id", ""))

        # Track messages in sub-channels for auto-rename
        if channel_id in self._created_channels and channel_id not in self._renamed_channels:
            content = payload.get("content") or ""
            self._created_channels[channel_id].append(content)
            if len(self._created_channels[channel_id]) >= 3:
                await self._rename_channel(channel_id)
            return  # Don't re-route messages in sub-channels

        if channel_id != self.config.command_center_channel_id:
            return

        sender_id = str(author.get("id", ""))
        if not self.is_allowed(sender_id):
            return

        content = payload.get("content") or ""
        guild_id = payload.get("guild_id")
        message_id = str(payload.get("id", ""))

        params = self._resolved_classifier_params()
        await self._router.handle_message(
            message_id=message_id,
            channel_id=channel_id,
            sender_id=sender_id,
            content=content,
            guild_id=guild_id,
            model=params["model"],
            api_key=params["api_key"],
            api_base=params["api_base"],
        )


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
        """Create thread (or channel fallback), post handoff, publish InboundMessage."""
        thread_id = await self._create_thread(pending, selected)
        if thread_id is None:
            await self._send_cc_message("\u26a0\ufe0f Could not create session channel.")
            return

        # Track this channel for auto-rename
        self._created_channels[thread_id] = []

        # Post handoff message in the new thread
        handoff = f"\u25b6 {selected.label} \u2014 {pending.original_content[:80]}"
        if pending.intents.side:
            side_labels = ", ".join(s.label for s in pending.intents.side)
            handoff += f"\n\u23f3 Side actions will attempt: {side_labels}"
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
        await self._send_cc_message(f"\u2705 Routed to <#{thread_id}>")

        # Execute side intents best-effort (after routing succeeds)
        if pending.intents.side:
            try:
                await self._execute_side_intents(pending, thread_id)
            except Exception as e:
                logger.warning("CC: side intent execution failed: {}", e)
                await self._send_cc_message(f"\u26a0\ufe0f Side action failed: {e}")

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
                label_display = "\U0001f4dd Memo" if intent.label == "memo" else "\u23f0 Reminder"
                await self._send_cc_message(
                    f"{label_display} saved: {pending.original_content[:60]}"
                )
            except Exception as e:
                logger.warning("CC: side intent [{}] failed: {}", intent.label, e)
                await self._send_cc_message(f"\u26a0\ufe0f Side action [{intent.label}] failed.")

    async def _rename_channel(self, channel_id: str) -> None:
        """Generate a short Chinese title via LLM and rename the Discord channel."""
        self._renamed_channels.add(channel_id)

        messages = self._created_channels.get(channel_id, [])
        context = "\n".join(messages[:6])

        params = self._resolved_classifier_params()
        try:
            kwargs: dict = dict(
                model=params["model"],
                messages=[
                    {"role": "system", "content": (
                        "根據以下對話內容，生成一個簡短的中文標題（4-8個字），"
                        "用來描述這段對話的主題。只回覆標題本身，不要加標點符號或解釋。"
                    )},
                    {"role": "user", "content": context},
                ],
                temperature=0.3,
                max_tokens=30,
            )
            if params.get("api_key"):
                kwargs["api_key"] = params["api_key"]
            if params.get("api_base"):
                kwargs["api_base"] = params["api_base"]

            response = await acompletion(**kwargs)
            title = (response.choices[0].message.content or "").strip()

            if not title or len(title) > 30:
                logger.warning("CC: generated title too long or empty: {}", title)
                return

            safe_title = title.replace(" ", "-")[:100]

            if not self._http:
                return

            url = f"{DISCORD_API_BASE}/channels/{channel_id}"
            headers = {"Authorization": f"Bot {self.config.token}"}
            resp = await self._http.patch(url, headers=headers, json={"name": safe_title})
            resp.raise_for_status()
            logger.info("CC: renamed channel {} to '{}'", channel_id, safe_title)

            # Clean up message buffer to prevent memory leak
            self._created_channels.pop(channel_id, None)

        except Exception as e:
            logger.warning("CC: failed to rename channel {}: {}", channel_id, e)

    async def _gateway_loop(self) -> None:
        """Gateway loop dispatching MESSAGE_CREATE for command center and sub-channels."""
        if not self._ws:
            return

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
            elif op == 7:
                logger.info("Discord CC gateway: reconnect requested")
                break
            elif op == 9:
                logger.warning("Discord CC gateway: invalid session")
                break
