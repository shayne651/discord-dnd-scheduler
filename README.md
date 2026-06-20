# D&D Session Scheduler Bot

A Discord bot for scheduling weekly D&D sessions, handling rescheduling polls, and notifying the DM of end-time requests.

---

## Disclaimer

This is a purely vibecoded app built for low-stakes personal use and local/private hosting only. It is not intended for production or public deployment.

The code was written collaboratively with an AI assistant. The author reviews changes loosely but is not well versed in the Discord API and does not verify that everything is implemented correctly. Use at your own risk — no guarantees around correctness, security, or stability.

---

## Setup (first time)

### 1. Create a Discord Application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application**, give it a name (e.g. "DnD Scheduler")
3. Go to **Bot** in the sidebar → click **Add Bot**
4. Under **Privileged Gateway Intents**, enable **Server Members Intent**
5. Click **Reset Token** and copy the token (you'll need it in step 4)

### 2. Invite the bot to your server

1. Go to **OAuth2 → URL Generator**
2. Under **Scopes**, check: `bot` and `applications.commands`
3. Under **Bot Permissions**, check: `Send Messages`, `Read Messages/View Channels`, `Embed Links`, `Manage Messages`, `Manage Roles`
   > ⚠️ For `/wipe` to remove roles, the bot's own role must sit **above** the player and DM roles in **Server Settings → Roles**. Drag it up if needed.
4. Copy the generated URL, paste it in your browser, and add it to your server

### 3. Enable Developer Mode in Discord

Go to **User Settings → Advanced → Developer Mode** (toggle on).  
This lets you right-click channels/roles to copy their IDs.

### 4. Configure the bot

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | How to get it |
|---|---|
| `DISCORD_BOT_TOKEN` | From step 1 above |
| `GUILD_ID` | Right-click your server name → Copy Server ID |
| `POLL_CHANNEL_ID` | Right-click the channel for polls → Copy Channel ID |
| `DM_NOTIFY_CHANNEL_ID` | Right-click the DM's private channel → Copy Channel ID |
| `SESSION_DAY` | e.g. `Saturday` |
| `PLAYER_ROLE_NAME` | The name of your player role (e.g. `DnD Player`) |
| `DM_ROLE_NAME` | The name of your DM role (e.g. `Dungeon Master`) |

### 5. Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> Requires Python 3.10 or newer. Check with: `python3 --version`

### 6. Run the bot

```bash
source .venv/bin/activate       # if not already active
python bot.py
```

You should see:
```
[DB] Running migrations...
[DB] Migrations up to date.
[Bot] Starting...
[Bot] Logged in as DnD Scheduler#1234
[Bot] Slash commands synced.
```

### 7. First-time Discord setup

In your Discord server, run these slash commands once:

```
/setday Saturday           ← or whichever day you normally play
/setchannel poll #your-poll-channel
/setchannel dm_notify #your-dm-channel
/setrole @DnD Player
/setdmrole @Dungeon Master
```

Then run `/status` to confirm everything is set.

---

## Commands

### Player commands (anyone with the player role)

| Command | What it does |
|---|---|
| `/cantmake` | Declare you can't make the session this week. A day-picker appears — select all days you're free. This triggers the scheduling poll. |
| `/endtime 10pm` | Notify the DM that you need to end by a specific time. Doesn't affect the poll. |
| `/myvotes` | See which days you've voted for and which days you have blocked. |
| `/blockday Tuesday` | Permanently block a day from all future polls (e.g. you work every Tuesday). Removes your existing vote for that day if a poll is open. |
| `/unblockday Tuesday` | Remove a personal day block. The day reappears in the current poll immediately. |
| `/setnick Gandalf` | Set your character name — shown in italics next to your Discord name in polls. |

### Admin commands (DM role only)

| Command | What it does |
|---|---|
| `/setschedule Saturday 7pm` | Set the regular session day and start time |
| `/setday Saturday` | Quick shortcut to change the default session day. If a cycle is currently auto-scheduled (no open poll), its event is moved to match. |
| `/setrecurrence 2` | Set how often (in weeks) the campaign repeats — `1` for weekly, `2` for every other week, etc. |
| `/blockcampaignday Friday` | Permanently block a day for the whole campaign (not just one player) — excluded from every poll. |
| `/unblockcampaignday Friday` | Remove a campaign-wide day block. |
| `/setchannel` | Set the poll or DM notification channel |
| `/setvoicechannel` | Set the voice channel used for the regular session's Discord Scheduled Event |
| `/setdmprivatevoice` | Set the private voice channel for pulling players aside (see below) |
| `/setrole` | Set the player role |
| `/setdmrole` | Set the DM role |
| `/startpoll` | Manually open a poll for this week, overriding any auto-scheduled event for the current cycle |
| `/resetpoll` | Cancel and delete the current poll |
| `/wipe` | **End-of-campaign reset** — removes player/DM roles from all members, clears all session data, votes, day blocks, character names, and cancels any pending auto-scheduled event. Shows a confirmation prompt first. |
| `/status` | Show current configuration, session schedule, recurrence, campaign-wide and per-player blocked days |

---

## DM private room

`/init` creates a second voice channel — **DM Private Room** — alongside the main session voice channel. PCs can't see or join it; only the DM role can. Use it to pull one player (or a few) aside without the rest of the table hearing, e.g. a private conversation when something happens to their character.

To get someone in there, either:
- **Move them**: right-click their name while they're in the main voice channel → **Move To** → DM Private Room (works even though they don't have Connect on it, since the DM role has **Move Members**), or
- **Grant them temporary access**: add a per-member permission overwrite on the channel in Server Settings, then remove it after.

If you ever need to repoint it (e.g. you recreated the channel manually), use `/setdmprivatevoice`.

---

## How scheduling works

### Default cadence (no action needed)

The campaign has a **default day** and a **recurrence** (how often, in weeks, it repeats — set during `/init` or via `/setrecurrence`). Every cycle, the bot automatically creates the Discord Scheduled Event for the default day with no polling required, then queues up the next cycle.

- During `/init`, you either pick a fixed day (the first event is created immediately) or type `poll` (a poll is posted immediately instead, and the winning day becomes the new default).
- `/setday` changes the default day going forward (and moves any already-scheduled event to match).
- Campaign-wide blocked days (`/blockcampaignday`) and per-player blocks (`/blockday`) are always excluded.

### Overriding a cycle with a poll

1. A player runs `/cantmake` and picks which days they're free (or the DM runs `/startpoll`). This cancels any auto-scheduled event for the current cycle and opens a poll instead.
2. A poll appears in the poll channel — everyone (including the DM) clicks every day that works for them.
3. **When a day gets votes from every player:** the poll closes, that day is announced and **becomes the new default day** for future cycles.
4. **If multiple days work for everyone:** a tiebreaker poll runs — most votes wins.
5. **If someone clicks "No days work for me":** they're asked to confirm, then the poll closes and everyone is notified D&D is off this week — the next cycle is still queued up per the recurrence cadence.
6. **If everyone votes but no day has consensus:** the bot nudges everyone to vote for more days.

---

## Adding database migrations

To change the database schema in the future, create a new file in `migrations/`:

```
migrations/002_add_something.sql
```

Name it with the next number in sequence. The bot applies it automatically on next start.

---

## Keeping the bot running

To keep the bot running after you close your terminal, use one of:

- **macOS/Linux:** `nohup python bot.py &` or a `systemd` service
- **Windows:** run it in a window you leave open, or use Task Scheduler
- **Cloud:** deploy to a free tier on [Railway](https://railway.app), [Fly.io](https://fly.io), or a small VPS
