"""Discord Command Center channel — routing layer for #command-center."""

from __future__ import annotations

import asyncio
import json as _json
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, field_validator
from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel, DiscordConfig
from nanobot.config.schema import Base
from nanobot.intent.classifier import Intent, IntentClassifier, IntentResult

DISCORD_API_BASE = "https://discord.com/api/v10"
_REACTION_NUMBERS = ["1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3"]


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
        """Full classify->post-menu->arm-timeout flow."""
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

        top_confidence = result.primary[0].confidence if result.primary else 0.0
        if top_confidence < ch.config.low_confidence_threshold:
            await ch._send_cc_message(
                "\u2753 Not sure what you need. Reply with one of: "
                "chat, code, research, write, memo, remind"
            )
            return

        # Build and post the intent menu
        lines = ["**Detected intent \u2014 react to confirm:**"]
        for i, intent in enumerate(result.primary[:3]):
            pct = int(intent.confidence * 100)
            lines.append(f"{_REACTION_NUMBERS[i]} **{intent.label}** \u2014 {intent.description} (~{pct}%)")
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
        """Post 1/2/3 reactions sequentially, then set reactions_posted=True."""
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
        await self._channel._send_cc_message("\u23f1\ufe0f Timed out. Send again when ready.")

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
        try:
            from nanobot.config.loader import get_config
            cfg = get_config()
            return cfg.agents.defaults.model
        except Exception:
            return "anthropic/claude-haiku-4-5-20251001"

    def _resolved_api_key(self) -> str | None:
        """Resolve API key from nanobot config for the classifier model."""
        try:
            from nanobot.config.loader import get_config
            cfg = get_config()
            return cfg.agents.defaults.get_api_key()
        except Exception:
            return None

    def _resolved_api_base(self) -> str | None:
        """Resolve API base URL from nanobot config for the classifier model."""
        try:
            from nanobot.config.loader import get_config
            cfg = get_config()
            return cfg.agents.defaults.get_api_base()
        except Exception:
            return None

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        """Override: only process command_center_channel_id; discard everything else."""
        author = payload.get("author") or {}
        if author.get("bot"):
            return

        channel_id = str(payload.get("channel_id", ""))
        if channel_id != self.config.command_center_channel_id:
            return  # not our channel — discard silently

        sender_id = str(author.get("id", ""))
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
            api_key=self._resolved_api_key(),
            api_base=self._resolved_api_base(),
        )

    async def _handle_reaction_add(self, payload: dict[str, Any]) -> None:
        """Handle MESSAGE_REACTION_ADD events."""
        channel_id = str(payload.get("channel_id", ""))
        if channel_id != self.config.command_center_channel_id:
            return

        user_id = str(
            (payload.get("member") or {}).get("user", {}).get("id", "")
            or payload.get("user_id", "")
        )
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
        """Create thread (or channel fallback), post handoff, publish InboundMessage."""
        thread_id = await self._create_thread(pending, selected)
        if thread_id is None:
            await self._send_cc_message("\u26a0\ufe0f Could not create session channel.")
            return

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

    async def _gateway_loop(self) -> None:
        """Extended gateway loop that also dispatches MESSAGE_REACTION_ADD."""
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
            elif op == 0 and event_type == "MESSAGE_REACTION_ADD":
                await self._handle_reaction_add(payload)
            elif op == 7:
                logger.info("Discord CC gateway: reconnect requested")
                break
            elif op == 9:
                logger.warning("Discord CC gateway: invalid session")
                break
