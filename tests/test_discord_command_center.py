"""Tests for DiscordCommandCenterChannel and CommandCenterRouter."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call as mock_call
from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel, DiscordConfig
from nanobot.intent.classifier import Intent, IntentResult


async def _make_discord(ignore_ids: list[str]) -> DiscordChannel:
    config = DiscordConfig(
        enabled=True,
        token="test-token",
        allow_from=["user123"],
        group_policy="open",
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


# --- Task 3: CommandCenterConfig tests ---

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


# --- Task 4: Router tests ---

from nanobot.channels.discord_command_center import (
    DiscordCommandCenterChannel,
)


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
async def test_trash_confidence_posts_dismissal():
    """If top intent confidence < 0.3, post dismissal, no pending state."""
    ch = _make_channel()
    low_result = IntentResult(
        primary=[Intent("chat", "gibberish", 0.2)]
    )

    with patch("nanobot.channels.discord_command_center.IntentClassifier") as MockCLF:
        MockCLF.return_value.classify = AsyncMock(return_value=low_result)
        ch._classifier = MockCLF()
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
    assert "messing around" in msg_content


@pytest.mark.asyncio
async def test_confident_intent_routes_directly():
    """Confidence >= 0.3 should route directly without reaction menu."""
    ch = _make_channel()
    high_result = IntentResult(
        primary=[Intent("code_task", "fix the bug", 0.88)]
    )

    with patch("nanobot.channels.discord_command_center.IntentClassifier") as MockCLF:
        MockCLF.return_value.classify = AsyncMock(return_value=high_result)
        ch._classifier = MockCLF()
        with patch.object(ch, "_add_reaction", AsyncMock()):
            with patch.object(ch, "_remove_reaction", AsyncMock()):
                with patch.object(ch, "_route", AsyncMock()) as mock_route:
                    with patch.object(ch, "_send_cc_message", AsyncMock()):
                        payload = {
                            "id": "msg1", "channel_id": "cc123", "guild_id": "g1",
                            "author": {"id": "user1", "bot": False},
                            "content": "fix the login bug", "attachments": [],
                        }
                        await ch._handle_message_create(payload)

    mock_route.assert_called_once()
    args = mock_route.call_args
    assert args[0][1].label == "code_task"


@pytest.mark.asyncio
async def test_no_pending_state_after_route():
    """Direct routing should leave no pending state."""
    ch = _make_channel()
    high_result = IntentResult(
        primary=[Intent("code_task", "fix bug", 0.88)]
    )

    with patch("nanobot.channels.discord_command_center.IntentClassifier") as MockCLF:
        MockCLF.return_value.classify = AsyncMock(return_value=high_result)
        ch._classifier = MockCLF()
        with patch.object(ch, "_add_reaction", AsyncMock()):
            with patch.object(ch, "_remove_reaction", AsyncMock()):
                with patch.object(ch, "_route", AsyncMock()):
                    with patch.object(ch, "_send_cc_message", AsyncMock()):
                        payload = {
                            "id": "msg1", "channel_id": "cc123", "guild_id": "g1",
                            "author": {"id": "user1", "bot": False},
                            "content": "fix the login bug", "attachments": [],
                        }
                        await ch._handle_message_create(payload)

    assert not hasattr(ch._router, '_pending') or len(ch._router._pending) == 0


# --- Task 5: Routing tests ---

from nanobot.channels.discord_command_center import PendingConfirmation


def _make_pending(ch: DiscordCommandCenterChannel) -> PendingConfirmation:
    return PendingConfirmation(
        original_message_id="orig1",
        original_channel_id="cc123",
        original_sender_id="user1",
        original_content="fix the login bug in auth.py",
        intents=IntentResult(primary=[Intent("code_task", "fix auth", 0.88)]),
        guild_id="guild1",
    )


@pytest.mark.asyncio
async def test_route_creates_thread_and_publishes_message():
    """Happy path: thread is created and InboundMessage is published."""
    ch = _make_channel()
    pending = _make_pending(ch)

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
    """Thread creation 403 -> falls back to channel creation without retry."""
    ch = _make_channel()
    pending = _make_pending(ch)

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

    # One thread attempt (no retry on 403), one channel creation, one handoff post
    assert call_count["n"] == 3
    assert len(published) == 1
    assert published[0].chat_id == "newchan888"


@pytest.mark.asyncio
async def test_route_posts_error_when_both_fail():
    """If thread and channel creation both fail, error is posted in #command-center."""
    ch = _make_channel()
    pending = _make_pending(ch)

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


# --- Task 6: Side intent tests ---


@pytest.mark.asyncio
async def test_side_intents_do_not_contaminate_thread_session():
    """Side intents must not publish to the routed thread_id."""
    ch = _make_channel()
    pending = _make_pending(ch)
    pending.intents.side = [Intent("memo", "save progress note", 0.92)]

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
    # Error was reported in #command-center
    assert any("⚠️" in m for m in warning_messages)


# --- TASK 3+4: New tests for direct route + auto-rename ---


@pytest.mark.asyncio
async def test_rename_tracked_after_creation():
    """Channels created by _route should be tracked for rename."""
    ch = _make_channel()
    pending = _make_pending(ch)

    thread_response = MagicMock(
        status_code=200, raise_for_status=MagicMock(),
        json=MagicMock(return_value={"id": "thread999"}),
    )
    ch._http.post = AsyncMock(return_value=thread_response)
    ch.bus.publish_inbound = AsyncMock()

    selected = Intent("code_task", "fix auth", 0.88)
    with patch.object(ch, "_send_cc_message", AsyncMock()):
        await ch._route(pending, selected)

    assert "thread999" in ch._created_channels


@pytest.mark.asyncio
async def test_rename_triggers_after_3_turns():
    """After 3 user messages in a tracked channel, rename should trigger."""
    ch = _make_channel()
    ch._created_channels = {"thread999": []}
    ch._renamed_channels = set()

    with patch.object(ch, "_rename_channel", AsyncMock()) as mock_rename:
        for i in range(3):
            payload = {
                "id": f"msg{i}", "channel_id": "thread999", "guild_id": "g1",
                "author": {"id": "user1", "bot": False},
                "content": f"message {i}", "attachments": [],
            }
            await ch._handle_message_create(payload)

    mock_rename.assert_called_once_with("thread999")


@pytest.mark.asyncio
async def test_rename_does_not_retrigger():
    """After rename, further messages should not trigger another rename."""
    ch = _make_channel()
    ch._created_channels = {"thread999": []}
    ch._renamed_channels = {"thread999"}

    with patch.object(ch, "_rename_channel", AsyncMock()) as mock_rename:
        for i in range(5):
            payload = {
                "id": f"msg{i}", "channel_id": "thread999", "guild_id": "g1",
                "author": {"id": "user1", "bot": False},
                "content": f"message {i}", "attachments": [],
            }
            await ch._handle_message_create(payload)

    mock_rename.assert_not_called()


@pytest.mark.asyncio
async def test_rename_channel_calls_llm_and_patches():
    """_rename_channel should call LLM for title, then PATCH Discord API."""
    ch = _make_channel()
    ch._created_channels = {"thread999": ["fix the bug", "sure", "done"]}
    ch._renamed_channels = set()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "修復登入錯誤"

    patch_resp = MagicMock(status_code=200, raise_for_status=MagicMock())
    ch._http.patch = AsyncMock(return_value=patch_resp)

    with patch("nanobot.channels.discord_command_center.acompletion", AsyncMock(return_value=mock_response)):
        with patch.object(ch, "_resolved_classifier_params", return_value={
            "model": "test-model", "api_key": "key", "api_base": None
        }):
            await ch._rename_channel("thread999")

    # Channel was renamed via PATCH
    ch._http.patch.assert_called_once()
    call_kwargs = ch._http.patch.call_args
    assert "thread999" in call_kwargs[0][0]
    assert call_kwargs[1]["json"]["name"] == "修復登入錯誤"

    # Marked as renamed
    assert "thread999" in ch._renamed_channels

    # Buffer cleaned up
    assert "thread999" not in ch._created_channels
