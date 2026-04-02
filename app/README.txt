============================================
 Discord To-Do Widget — Quick Start Guide
============================================

REQUIREMENTS
------------
- Windows 10/11
- Python 3.8 or later  (download at python.org — check "Add to PATH" during install)


HOW TO BUILD THE EXE
--------------------
1. Open this folder in File Explorer
2. Double-click  build.bat
3. Wait for it to finish (first run takes ~2 minutes)
4. Your exe will be at:  dist\DiscordTodo.exe

That's it. You can copy DiscordTodo.exe + config.json anywhere you like.
IMPORTANT: config.json must always stay in the same folder as the exe.


HOW TO RUN (after building)
----------------------------
Double-click DiscordTodo.exe

- The widget appears in the bottom-right corner of your screen
- It's a yellow post-it note, always on top, and draggable
- It shows every message in the "Josh" Discord thread, except taskbot
- It auto-refreshes every day at 8:35 PM Eastern Time


WIDGET CONTROLS
---------------
  Drag title bar   — Move the widget anywhere on screen
  ⚙ (gear icon)   — Open Settings (change thread name, filter user)
  ↻ Refresh       — Manually fetch latest messages from Discord
  ✕              — Close the widget


CHANGING THE THREAD NAME
------------------------
You can point this widget at any thread, not just "Josh":

Option A — Edit config.json directly:
  Open config.json in Notepad, change "thread_name": "Josh" to any thread name.
  Restart the app.

Option B — In the running app:
  Click the ⚙ gear icon → change Thread Name → Save & Refresh.
  The new name is saved automatically.

This makes it easy to share with others — each person just sets their own thread name.


SHARING WITH OTHERS IN YOUR GROUP
-----------------------------------
To give a copy to someone else:
1. Copy  DiscordTodo.exe  and  config.json  to a folder
2. In their config.json, change "thread_name" to their Discord thread name
3. They double-click the exe — done. No Python needed on their machine.


HOW "COMPLETE" WORKS
----------------------
There are no checkboxes. Tasks live in Discord.
- Add a task: Post a message in the thread
- Complete a task: Delete the message in Discord
- Next refresh (or ↻ Refresh): the deleted message disappears from the list


CONFIG FILE (config.json)
--------------------------
{
  "token":       "your-bot-token",         ← Discord bot token
  "guild_id":    "your-guild-id",          ← Server (Guild) ID
  "channel_id":  "your-channel-id",        ← Channel containing the thread
  "thread_name": "Josh",                   ← Thread name to read (EDITABLE)
  "filter_user": "taskbot"                 ← Username to exclude (EDITABLE)
}


TROUBLESHOOTING
---------------
"Thread not found"
  → Make sure the thread name in config.json matches exactly (case-insensitive)
  → Make sure the bot has permission to view the channel and thread

"API error 401"
  → Your bot token is invalid or expired. Generate a new one at discord.com/developers

"API error 403"
  → The bot doesn't have permission to read that channel/thread.
    Add the bot to the server and grant it Read Messages access.

Widget won't start / crashes instantly
  → Check that config.json is in the same folder as DiscordTodo.exe
  → Try running from terminal:  cd dist && DiscordTodo.exe
    (windowed builds hide console errors — running from terminal shows them)
