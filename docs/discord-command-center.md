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

- `commandCenterChannelId`: right-click `#command-center` -> Copy Channel ID (requires Developer Mode)
- `sessionCategoryId`: optional Discord category ID for channel fallback (requires `MANAGE_CHANNELS`)
- `classifierModel`: `null` uses your agent's configured model; override with any LiteLLM model string
- `ignoreChannelIds` in `discord`: must include the same channel ID to prevent double-handling

## Required Bot Permissions

- `GUILD_MESSAGES` + `MESSAGE_CONTENT` (already needed for normal Discord)
- `GUILD_MESSAGE_REACTIONS` (for emoji confirmation)
- `CREATE_PUBLIC_THREADS` (for thread creation -- preferred)
- `MANAGE_CHANNELS` (optional, for channel fallback only)

## How It Works

1. Type a message in `#command-center`
2. Bot classifies and shows top intents with 1/2/3
3. React to confirm (60s window)
4. Bot creates a thread and routes your original message there
5. Continue the conversation in the thread -- it has its own isolated session
