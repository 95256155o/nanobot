# NanoBot Discord Command Center — Design Spec
**Date:** 2026-03-19
**Target:** First PR (nightly)

---

## 1. Goal

Turn one low-context Discord channel (`#command-center`) into a routing layer that classifies each user message, asks for quick emoji confirmation, then moves work into a fresh dedicated Discord thread (or channel fallback) with an isolated NanoBot session.

**First PR optimizes for:**
- Isolated routing flow — `#command-center` never executes agent work directly
- Minimal blast radius — zero changes to `discord.py` or core session logic
- Fresh per-thread session context — every routed thread always starts clean (no reuse in first PR)
- Best-effort side actions — side intent failures never block routing success

---

## 2. Architecture Overview

Two separate Discord gateway consumers run on the same bot token, each with explicit channel filtering so no message is handled by both:

```
Discord Gateway
        │
        ├── DiscordChannel (existing plugin)
        │       ignoreChannelIds: [command_center_channel_id]
        │       └── handles all channels EXCEPT #command-center
        │           → normal NanoBot agent behavior
        │
        └── DiscordCommandCenterChannel (new plugin, subclass of DiscordChannel)
                handles ONLY command_center_channel_id; ignores all other channels silently
                └── CommandCenterRouter [full routing flow]
```

**Two gateway connections, same token.** Discord permits this. Each plugin manages its own WebSocket connection lifecycle independently.

**`DiscordCommandCenterChannel` does NOT call `super()` to forward non-command-center messages.** It simply discards events from any channel other than `command_center_channel_id`. The existing `discord` plugin handles everything else. There is no shared event dispatch between the two plugins.

**`ignore_channel_ids` on the `discord` plugin** is a new optional field added to `DiscordConfig`. It filters out the specified channel IDs before `_handle_message_create` is called, so `#command-center` messages are never seen by the normal agent pipeline.

---

## 3. New Files

| File | Purpose |
|---|---|
| `nanobot/intent/__init__.py` | Package marker |
| `nanobot/intent/classifier.py` | Stateless `IntentClassifier` — LLM call, returns `IntentResult` |
| `nanobot/channels/discord_command_center.py` | `DiscordCommandCenterChannel` subclass + `CommandCenterRouter` |

## 4. Changed Files

| File | Change |
|---|---|
| `nanobot/channels/registry.py` | Register `"discord_command_center"` channel type |
| `nanobot/config/schema.py` | Add `CommandCenterConfig`; add `ignore_channel_ids: list[str]` to `DiscordConfig` |

---

## 5. Configuration

```json
"discord": {
  "enabled": true,
  "token": "<bot_token>",
  "allowFrom": ["<user_id>"],
  "groupPolicy": "open",
  "ignoreChannelIds": ["<command_center_channel_id>"]
},
"discord_command_center": {
  "enabled": true,
  "token": "<same_bot_token>",
  "allowFrom": ["<user_id>"],
  "commandCenterChannelId": "<channel_id>",
  "sessionCategoryId": null,
  "confirmationTimeoutS": 60,
  "classifierModel": null,
  "lowConfidenceThreshold": 0.5
}
```

- `classifierModel`: `null` means use the agent's globally configured model (resolved from `agents.defaults.model` at runtime, never hardcoded). Override with any LiteLLM-compatible model string.
- `sessionCategoryId`: Discord category ID for fallback channel creation. `null` disables fallback.
- `confirmationTimeoutS`: Valid range 10–300. Values outside this range are clamped at startup with a warning.

---

## 6. Components

### 6.1 `IntentClassifier` (`nanobot/intent/classifier.py`)

```python
@dataclass
class Intent:
    label: str          # e.g. "code_task"
    description: str    # short human-readable summary
    confidence: float   # 0.0–1.0

@dataclass
class IntentResult:
    primary: list[Intent]   # top 1–3, sorted by confidence desc
    side: list[Intent]      # background actions (remind, memo, etc.)
```

- Single async method: `classify(text: str, model: str) -> IntentResult`
- `model` is required — the caller always resolves the model string from config before calling. `IntentClassifier` never reads config directly.
- Calls LLM with a compact system prompt listing known intent categories
- Stateless — no session history, no memory. Raw text only.
- Known intents (first pass): `code_task`, `research`, `write`, `remind`, `memo`, `chat`, `system_cmd`
- Side intents: `remind`, `memo` (never become primary routing targets)

### 6.2 `CommandCenterRouter` (`discord_command_center.py`)

**State machine per pending confirmation** (keyed by bot confirmation message ID):

```
IDLE → CLASSIFYING → AWAITING_REACTION → ROUTING → DONE
                                       ↘ TIMED_OUT
```

**In-memory state only.** `_pending: dict[str, PendingConfirmation]` starts empty on every bot start. No persistence, no startup sweep needed — cleared automatically on restart. User simply sends again.

```python
@dataclass
class PendingConfirmation:
    original_message_id: str
    original_channel_id: str
    original_sender_id: str           # only this user's reactions count
    original_content: str
    intents: IntentResult
    confirmation_message_id: str
    timeout_handle: asyncio.TimerHandle
    reactions_posted: bool = False    # False until all emoji posted
    pending_reactions: list = field(default_factory=list)  # buffer for early reactions
```

**Reaction race handling:** Reactions that arrive while `reactions_posted = False` are appended to `pending_reactions`. When `reactions_posted` is set to `True` (after all three emoji are posted), `pending_reactions` is replayed immediately.

### 6.3 `DiscordCommandCenterChannel`

Subclasses `DiscordChannel`. Overrides `_gateway_loop` to also dispatch `MESSAGE_REACTION_ADD` events. Events from channels other than `command_center_channel_id` are silently discarded — not forwarded to `super()`.

**Message handling:**
1. Receive message in `command_center_channel_id`; discard anything else silently
2. Add 👀 reaction (processing indicator; failure is silent — cosmetic only)
3. Call `IntentClassifier.classify()` with the resolved model string
4. Remove 👀 reaction (success or failure; failure is silent — cosmetic only)
5. If `top_confidence < low_confidence_threshold`:
   - Post: `"❓ Not sure what you need. Reply with one of: chat, code, research, write, memo, remind"`
   - Stop (no pending state created)
6. Else: post intent menu, react 1️⃣/2️⃣/3️⃣ sequentially, set `reactions_posted = True`, replay buffered reactions, store `PendingConfirmation`

**Reaction handling:**
- Ignore reactions from anyone except `original_sender_id`
- Ignore reactions if `reactions_posted = False` (buffer them instead)
- On valid 1️⃣/2️⃣/3️⃣: resolve selection, cancel timeout, proceed to routing

**Routing:**
1. Try `POST /channels/{command_center_channel_id}/threads` (public thread on original message)
2. On failure due to permission error (`403`): skip retry, go directly to step 4
3. On failure due to transient error (`5xx`): retry once, then go to step 4
4. Channel fallback: `POST /guilds/{guild_id}/channels` with `parent_id = session_category_id`
5. On channel creation failure: post `"⚠️ Could not create session channel."`, clean up, stop
6. Post handoff in new thread/channel:
   - `"▶ {intent_label} — {original_content[:80]}"`
   - If side intents present, append: `"\n⏳ Side actions will attempt: {labels}"` (promise of attempt, not execution)
7. Publish `InboundMessage(chat_id=thread_id, content=original_content, session_key_override=None)`
   - `content` is the user's original message text verbatim — the agent's first response is a direct reply to the original request
   - No wrapper or routing prefix is added; the handoff message posted in step 6 provides the routing context visually
8. Post in `#command-center`: `"✅ Routed to <#{thread_id}>"` (clickable Discord mention)
9. Execute side intents best-effort (see below)

**Side intents (after routing succeeds, step 9):**

Side intents (`memo`, `remind`) are executed via the agent bus. **The `chat_id` used for side intents must NOT be `thread_id`**, because that would write memo/remind turns into the fresh session history, contaminating it for future routing.

Two options are acknowledged; the implementation plan must choose one:
- **Option A — ephemeral DM chat_id**: route side intents through a synthetic or DM-based `chat_id` that is never surfaced as a routed session.
- **Option B — direct tool dispatch**: implement `remind` and `memo` as direct async function calls (not agent turns) that write to their target storage without going through the LLM conversation loop at all.

Either way: each failure reported in `#command-center` separately (`"⚠️ Side action [memo] failed."`), never blocking routing.

---

## 7. Session Key After Handoff

No `session_key_override` is set. The thread/channel's Discord ID becomes the natural session key `discord:<thread_id>` through normal `BaseChannel._handle_message()` behavior. All future messages in that thread route to the same isolated session automatically.

**Every routed thread/channel starts with a fresh, empty session.** No thread reuse in the first PR — a new thread is always created.

**How `#command-center` is kept free of agent session history:**
`DiscordCommandCenterChannel` never publishes an `InboundMessage` with `chat_id = command_center_channel_id`. All command-center messages are handled entirely within `CommandCenterRouter` in-process. Because no message with that `chat_id` is ever put on the agent bus, the `SessionManager` never creates or writes a session file keyed `discord:<command_center_channel_id>`. This is structural enforcement, not a runtime guard.

---

## 8. Error Handling

| Condition | Behavior |
|---|---|
| Classifier call fails | Remove 👀 (silent); post `"⚠️ Classification failed. Try again."` |
| Top confidence < threshold | Remove 👀 (silent); post clarification prompt with manual fallback labels |
| 👀 reaction POST fails | Silent continue — cosmetic only, no effect on routing |
| 👀 reaction DELETE fails | Silent continue — cosmetic only |
| Reaction timeout (60s) | Post `"⏱️ Timed out. Send again when ready."`; remove pending |
| Reaction from wrong user | Ignore silently |
| Reaction while `reactions_posted = False` | Buffer; replay when flag set |
| Thread creation fails (403 permission) | Skip retry; go directly to channel fallback |
| Thread creation fails (5xx transient) | Retry once; then channel fallback |
| Channel creation fails | Post `"⚠️ Could not create session channel."`; clean up |
| Side intent fails | Post `"⚠️ Side action [label] failed."` in `#command-center`; routing unaffected |
| Bot restart | `_pending` starts empty; any in-flight confirmations expire silently |

---

## 9. Risks & Open Questions

| # | Risk | Mitigation |
|---|---|---|
| R1 | Duplicate events: both Discord plugins on same token | `ignore_channel_ids` in `DiscordConfig` filters `#command-center` from normal plugin; `DiscordCommandCenterChannel` discards non-CC events |
| R2 | `MANAGE_THREADS` not granted | Graceful fallback to channel creation; 403 skips retry |
| R3 | `MANAGE_CHANNELS` not granted (fallback) | Error posted in `#command-center`; no silent failure |
| R4 | User reacts before all 3 emoji posted | Reaction buffer replayed when `reactions_posted = True` |
| R5 | Side intent writes contaminate fresh session | Implementation must choose Option A (ephemeral chat_id) or Option B (direct tool dispatch) — see Section 6.3 |

---

## 10. Out of Scope (First PR)

- Context/token usage indicator (`/status` command)
- Thread reuse (`reuseWindowH`) — deferred; session history implications need separate design
- `MANAGE_CHANNELS` permission setup guide
- Multi-user `#command-center` support
- Persistent `_pending` across restarts
- Thread archiving / cleanup automation
- Latency indicator (`"🔍 Classifying..."` message for slow model calls) — needs defined lifecycle (when posted, when deleted)

---

## 11. Success Criteria

A user types one mixed-intent message in `#command-center`, sees top-3 intents with confidence scores, reacts to confirm, and continues work in a fresh routed thread with isolated session context. Side intents execute after routing without contaminating the thread session.
