# NanoBot Discord Command Center — Design Spec
**Date:** 2026-03-19
**Branch:** `intent-gate`
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

```
Discord Gateway (one connection, one bot token)
        │
        ▼
DiscordCommandCenterChannel   [NEW — subclass of DiscordChannel]
        │
        ├── message.channel_id == command_center_channel_id?
        │       └── CommandCenterRouter  [handles full routing flow]
        │
        └── all other messages → super()._handle_message_create()
                                  └── normal NanoBot agent behavior

DiscordChannel (existing)
        └── ignore_channel_ids: [command_center_channel_id]
            (prevents duplicate handling of same events)
```

**Key constraint:** Only one handler processes each Discord message. The existing `DiscordChannel` config accepts a new optional `ignore_channel_ids` list; `#command-center` is added there. `DiscordCommandCenterChannel` handles that channel exclusively.

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
| `nanobot/config/schema.py` | Add `CommandCenterConfig`; add `ignore_channel_ids` to `DiscordConfig` |

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
- `model` is required — the caller always resolves the model string from config before calling. `IntentClassifier` never reads config directly. Passing an empty string or calling with no model is a caller contract violation.
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

**Reaction race handling:** Reactions that arrive while `reactions_posted = False` are appended to `pending_reactions`. When `reactions_posted` is set to `True` (after all three emoji are posted), `pending_reactions` is replayed immediately. This ensures a fast user who reacts before all three emoji are posted is not silently ignored.

### 6.3 `DiscordCommandCenterChannel`

Subclasses `DiscordChannel`. Overrides `_gateway_loop` to also dispatch `MESSAGE_REACTION_ADD` events. All other gateway events fall through to `super()`.

**Message handling:**
1. Receive message in `command_center_channel_id`
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
2. On failure due to permission error (`403`): skip retry, go directly to step 3
3. On failure due to transient error (`5xx`): retry once, then go to step 3
4. Channel fallback: `POST /guilds/{guild_id}/channels` with `parent_id = session_category_id`
5. On channel creation failure: post `"⚠️ Could not create session channel."`, clean up, stop
6. Post handoff in new thread/channel:
   - `"▶ {intent} — {original_content[:80]}"`
   - If side intents present, append: `"\n⏳ Side actions will attempt: {labels}"` (this is a promise of attempt, not execution)
7. Publish `InboundMessage(chat_id=thread_id, session_key_override=None)` → session = `discord:<thread_id>`
8. Post in `#command-center`: `"✅ Routed to <#{thread_id}>"` (clickable Discord mention)
9. Execute side intents best-effort (see below)

**Side intents (after routing succeeds, step 9):**
- Execute via agent bus with `chat_id = thread_id` (the newly created session thread, not `#command-center`)
- Each failure reported in `#command-center` separately: `"⚠️ Side action [memo] failed."`
- Never block or roll back routing

---

## 7. Session Key After Handoff

No `session_key_override` is set. The thread/channel's Discord ID becomes the natural session key `discord:<thread_id>` through normal `BaseChannel._handle_message()` behavior. All future messages in that thread route to the same isolated session automatically.

**Every routed thread/channel starts with a fresh, empty session.** No thread reuse in the first PR — a new thread is always created. Thread reuse is deferred to a follow-up PR where session history implications can be addressed explicitly.

**`#command-center` never accumulates agent conversation history.** It is routing infrastructure only.

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
| R1 | Duplicate events if both Discord plugins active on same token | `ignore_channel_ids` in base `DiscordConfig` prevents double-handling |
| R2 | `MANAGE_THREADS` not granted | Graceful fallback to channel creation; 403 skips retry |
| R3 | `MANAGE_CHANNELS` not granted (fallback) | Error posted in `#command-center`; no silent failure |
| R4 | User reacts before all 3 emoji posted | Reaction buffer replayed when `reactions_posted = True` |
| R5 | Side intent rate-limited | Caught, reported separately in `#command-center`, routing continues |

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

A user types one mixed-intent message in `#command-center`, sees top-3 intents with confidence scores, reacts to confirm, and continues work in a fresh routed thread with isolated session context. Side intents execute after routing without blocking it.
