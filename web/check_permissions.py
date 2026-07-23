"""
Discord permission probe for the task threads.

Run INSIDE the web (or taskbot) container, where DISCORD_TOKEN / GUILD_ID /
CHANNEL_ID are in the environment:

    docker exec taskbot-web python check_permissions.py

It reports, read-only (it never deletes anything):
  1. Who posted the existing tasks — humans or the bot.
  2. The bot's effective permissions on the task channel.
  3. Whether "Manage Messages" is present, i.e. whether the web UI's Delete
     will work for HUMAN-posted tasks (deleting other users' messages needs it;
     the bot can always delete its own).

Nothing here prints the token.
"""

import sys

import requests

import config
import discord_api as discord

MANAGE_MESSAGES = 1 << 13   # 0x2000
ADMINISTRATOR = 1 << 3      # 0x8


def _get(path):
    r = requests.get(f"https://discord.com/api/v10{path}",
                     headers={"Authorization": f"Bot {config.DISCORD_TOKEN}"})
    r.raise_for_status()
    return r.json()


def compute_channel_perms(guild_id, channel_id):
    """Standard Discord permission resolution for the bot in one channel."""
    me = _get("/users/@me")
    bot_id = me["id"]

    member = _get(f"/guilds/{guild_id}/members/{bot_id}")
    member_role_ids = set(member.get("roles", []))

    roles = {r["id"]: r for r in _get(f"/guilds/{guild_id}/roles")}
    everyone = roles.get(guild_id, {"permissions": "0"})

    # Base permissions from @everyone + assigned roles
    perms = int(everyone.get("permissions", "0"))
    for rid in member_role_ids:
        perms |= int(roles.get(rid, {}).get("permissions", "0"))

    if perms & ADMINISTRATOR:
        return bot_id, perms, True, "ADMINISTRATOR (implies all permissions)"

    # Channel overwrites (type 0 = role, type 1 = member)
    channel = _get(f"/channels/{channel_id}")
    overwrites = {o["id"]: o for o in channel.get("permission_overwrites", [])}

    # @everyone overwrite
    ov = overwrites.get(guild_id)
    if ov:
        perms &= ~int(ov.get("deny", "0"))
        perms |= int(ov.get("allow", "0"))

    # Role overwrites (accumulate)
    allow_all = deny_all = 0
    for rid in member_role_ids:
        ov = overwrites.get(rid)
        if ov:
            deny_all |= int(ov.get("deny", "0"))
            allow_all |= int(ov.get("allow", "0"))
    perms &= ~deny_all
    perms |= allow_all

    # Member-specific overwrite (highest precedence)
    ov = overwrites.get(bot_id)
    if ov:
        perms &= ~int(ov.get("deny", "0"))
        perms |= int(ov.get("allow", "0"))

    return bot_id, perms, bool(perms & MANAGE_MESSAGES), "resolved from roles + channel overwrites"


def main():
    threads = discord.get_all_threads()
    print(f"Channel under test: {config.CHANNEL_ID}\n")

    # 1. Sample authorship of existing tasks
    for name in config.USERS:
        thread = discord.find_thread(threads, name)
        if not thread:
            print(f"  {name}: no thread found")
            continue
        msgs = discord.get_thread_messages(thread["id"])
        human = sum(1 for m in msgs if not m.get("author", {}).get("bot", False)
                    and m.get("content", "").strip())
        botm = sum(1 for m in msgs if m.get("author", {}).get("bot", False))
        print(f"  {name}: {human} human-posted message(s), {botm} bot message(s)")

    # 2 + 3. Effective permission on the channel
    try:
        bot_id, perms, has_manage, how = compute_channel_perms(config.GUILD_ID, config.CHANNEL_ID)
    except Exception as e:
        print(f"\n[error] Could not compute permissions: {e}")
        sys.exit(1)

    print(f"\nBot user id: {bot_id}")
    print(f"Manage Messages: {'YES' if has_manage else 'NO'}  ({how})")
    if has_manage:
        print("=> Delete WILL work for human-posted tasks.")
    else:
        print("=> Delete will only work on the bot's OWN messages. Human-posted "
              "tasks CANNOT be deleted until Manage Messages is granted in Discord.")


if __name__ == "__main__":
    main()
