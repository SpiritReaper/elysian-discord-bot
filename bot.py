import os
import json
import asyncio
import discord
import aiohttp
import pytz
from aiohttp import web
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
STATS_API_KEY = os.getenv("STATS_API_KEY", "")
PORT = int(os.environ.get("PORT", 8080))

CONFIG_FILE = "config.json"
STATE_FILE = "elysian_state.json"

CATEGORY_NAME = "Form a Party"
JOIN_CHANNEL_NAME = "New Party"

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# channel_id -> owner_id
# This is restored from disk on startup so temporary channels still work after Railway redeploys.
temporary_channels = {}
bot_start_time = datetime.now(pytz.utc)
total_temp_channels_created = 0
recent_errors = []
recent_events = []
stats_server_started = False
stats_cache = {"time": 0, "data": None}
rate_limit_hits = {}

state_lock = asyncio.Lock()
config_lock = asyncio.Lock()


def utc_now():
    return datetime.now(pytz.utc)


def now_string():
    return utc_now().strftime("%Y-%m-%d %I:%M:%S %p UTC")


def add_error(error_message):
    recent_errors.append({"time": now_string(), "error": str(error_message)})
    if len(recent_errors) > 10:
        recent_errors.pop(0)


def add_event(message):
    recent_events.append({"time": now_string(), "event": str(message)})
    if len(recent_events) > 25:
        recent_events.pop(0)


def atomic_write_json(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    os.replace(tmp_path, path)


def load_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default
    except Exception as e:
        add_error(e)
        return default


async def load_config():
    async with config_lock:
        return load_json_file(CONFIG_FILE, {})


async def save_config(data):
    async with config_lock:
        try:
            atomic_write_json(CONFIG_FILE, data)
        except Exception as e:
            add_error(e)


async def load_state():
    global temporary_channels, total_temp_channels_created
    async with state_lock:
        state = load_json_file(STATE_FILE, {})
        temporary_channels = {
            int(channel_id): int(owner_id)
            for channel_id, owner_id in state.get("temporary_channels", {}).items()
        }
        total_temp_channels_created = int(state.get("total_temp_channels_created", 0))


async def save_state():
    async with state_lock:
        state = {
            "temporary_channels": {
                str(channel_id): owner_id
                for channel_id, owner_id in temporary_channels.items()
            },
            "total_temp_channels_created": total_temp_channels_created,
            "saved_at_utc": now_string()
        }
        try:
            atomic_write_json(STATE_FILE, state)
        except Exception as e:
            add_error(e)


async def set_voice_channel_status(channel_id, status):
    url = f"https://discord.com/api/v10/channels/{channel_id}/voice-status"
    headers = {
        "Authorization": f"Bot {TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"status": status}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json=payload) as response:
                if response.status not in (200, 204):
                    error_text = await response.text()
                    add_error(f"Failed to set voice status: {response.status} {error_text}")
    except Exception as e:
        add_error(e)


def user_owns_channel(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return False

    channel = interaction.user.voice.channel
    owner_id = temporary_channels.get(channel.id)
    return owner_id == interaction.user.id


def format_uptime(seconds):
    days = seconds // 86400
    seconds %= 86400
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return f"{days}d {hours}h {minutes}m {seconds}s"


async def restore_temporary_channels():
    """Restore temp channel ownership after Railway restarts/redeploys.

    If a persisted temp channel still exists and has members, ownership is kept.
    If a category contains old temp channels that were not in the persisted state, ownership is assigned to
    the first non-bot member so lock/unlock/limit still work after a redeploy.
    Empty temp channels are cleaned up.
    """
    await load_state()
    config = await load_config()
    restored = 0
    adopted = 0
    deleted = 0

    for guild in bot.guilds:
        guild_config = config.get(str(guild.id))
        if not guild_config:
            continue

        category = guild.get_channel(guild_config.get("category_id"))
        join_channel_id = guild_config.get("join_channel_id")
        if not category:
            continue

        for channel in list(category.voice_channels):
            if channel.id == join_channel_id:
                continue

            non_bot_members = [member for member in channel.members if not member.bot]

            if channel.id in temporary_channels:
                if non_bot_members:
                    restored += 1
                else:
                    temporary_channels.pop(channel.id, None)
                    try:
                        await channel.delete(reason="Cleaning up empty temporary channel after restart")
                        deleted += 1
                    except Exception as e:
                        add_error(e)
                continue

            if non_bot_members:
                temporary_channels[channel.id] = non_bot_members[0].id
                adopted += 1
            else:
                try:
                    await channel.delete(reason="Cleaning up orphaned empty temporary channel after restart")
                    deleted += 1
                except Exception as e:
                    add_error(e)

    await save_state()
    add_event(f"Restored temp channels after startup. Restored: {restored}, adopted: {adopted}, deleted: {deleted}")


def authorized(request):
    if not STATS_API_KEY:
        return True
    auth_header = request.headers.get("Authorization", "")
    return auth_header == f"Bearer {STATS_API_KEY}"


def rate_limited(request, limit=60):
    # Lightweight local protection: 60 stats requests per minute per IP.
    ip = request.remote or "unknown"
    minute_key = utc_now().strftime("%Y%m%d%H%M")
    key = f"{ip}:{minute_key}"
    rate_limit_hits[key] = rate_limit_hits.get(key, 0) + 1

    # Clear old keys occasionally.
    if len(rate_limit_hits) > 500:
        current_prefix = utc_now().strftime("%Y%m%d%H")
        for old_key in list(rate_limit_hits.keys()):
            if current_prefix not in old_key:
                rate_limit_hits.pop(old_key, None)

    return rate_limit_hits[key] > limit


async def stats_handler(request):
    if not authorized(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    if rate_limited(request):
        return web.json_response({"error": "Too many requests"}, status=429)

    now_ts = utc_now().timestamp()
    if stats_cache["data"] is not None and now_ts - stats_cache["time"] < 2:
        return web.json_response(stats_cache["data"])

    uptime_seconds = int((utc_now() - bot_start_time).total_seconds())
    data = {
        "bot_online": bot.is_ready(),
        "bot_name": str(bot.user) if bot.user else None,
        "uptime": format_uptime(uptime_seconds),
        "uptime_seconds": uptime_seconds,
        "servers": len(bot.guilds),
        "active_temp_channels": len(temporary_channels),
        "total_temp_channels_created": total_temp_channels_created,
        "recent_errors": recent_errors,
        "recent_events": recent_events,
        "timestamp_eastern": utc_now()
            .astimezone(pytz.timezone("US/Eastern"))
            .strftime("%m/%d/%Y %I:%M:%S %p %Z")
    }

    stats_cache["time"] = now_ts
    stats_cache["data"] = data
    return web.json_response(data)


async def health_handler(request):
    return web.json_response({
        "status": "ok",
        "bot_ready": bot.is_ready(),
        "port": PORT,
        "active_temp_channels": len(temporary_channels)
    })


async def start_stats_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/stats", stats_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Stats server running on 0.0.0.0:{PORT}")


@bot.event
async def on_ready():
    global stats_server_started

    if not stats_server_started:
        await start_stats_server()
        stats_server_started = True

    await restore_temporary_channels()
    await bot.tree.sync()

    add_event(f"Bot ready as {bot.user}")
    print(f"Logged in as {bot.user}")
    print("Slash commands synced globally")


@bot.event
async def on_disconnect():
    add_error("Bot disconnected from Discord")


@bot.event
async def on_resumed():
    add_event("Bot connection resumed")


@bot.tree.command(name="setup", description="Create the Form a Party temp voice setup")
async def setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message(
            "You need Manage Channels permission to use this command.",
            ephemeral=True
        )
        return

    guild = interaction.guild
    category = discord.utils.get(guild.categories, name=CATEGORY_NAME)

    if category is None:
        category = await guild.create_category(CATEGORY_NAME)

    join_channel = discord.utils.get(category.voice_channels, name=JOIN_CHANNEL_NAME)

    if join_channel is None:
        join_channel = await guild.create_voice_channel(
            name=JOIN_CHANNEL_NAME,
            category=category
        )

    config = await load_config()
    config[str(guild.id)] = {
        "category_id": category.id,
        "join_channel_id": join_channel.id
    }
    await save_config(config)
    add_event(f"/setup completed in {guild.name}")

    await interaction.response.send_message(
        f"Setup complete. Created **{CATEGORY_NAME}** with **{JOIN_CHANNEL_NAME}**.",
        ephemeral=True
    )


@bot.tree.command(name="lock", description="Lock your temporary voice channel")
async def lock(interaction: discord.Interaction):
    if not user_owns_channel(interaction):
        await interaction.response.send_message(
            "You can only lock a temporary voice channel that you created.",
            ephemeral=True
        )
        return

    channel = interaction.user.voice.channel
    everyone = interaction.guild.default_role

    await channel.set_permissions(everyone, connect=False)
    await channel.set_permissions(interaction.user, connect=True)
    add_event(f"{interaction.user} locked {channel.name}")

    await interaction.response.send_message(f"Locked **{channel.name}**.", ephemeral=True)


@bot.tree.command(name="unlock", description="Unlock your temporary voice channel")
async def unlock(interaction: discord.Interaction):
    if not user_owns_channel(interaction):
        await interaction.response.send_message(
            "You can only unlock a temporary voice channel that you created.",
            ephemeral=True
        )
        return

    channel = interaction.user.voice.channel
    everyone = interaction.guild.default_role

    await channel.set_permissions(everyone, connect=True)
    add_event(f"{interaction.user} unlocked {channel.name}")

    await interaction.response.send_message(f"Unlocked **{channel.name}**.", ephemeral=True)


@bot.tree.command(name="limit", description="Set a user limit for your temporary voice channel")
@app_commands.describe(amount="Number of users allowed in your voice channel")
async def limit(interaction: discord.Interaction, amount: int):
    if not user_owns_channel(interaction):
        await interaction.response.send_message(
            "You can only set the limit for a temporary voice channel that you created.",
            ephemeral=True
        )
        return

    if amount < 0 or amount > 99:
        await interaction.response.send_message(
            "Please choose a number between 0 and 99. Use 0 for no limit.",
            ephemeral=True
        )
        return

    channel = interaction.user.voice.channel
    await channel.edit(user_limit=amount)

    limit_text = "no limit" if amount == 0 else str(amount)
    add_event(f"{interaction.user} set limit for {channel.name} to {limit_text}")

    await interaction.response.send_message(
        f"User limit for **{channel.name}** set to **{limit_text}**.",
        ephemeral=True
    )


@bot.event
async def on_voice_state_update(member, before, after):
    global total_temp_channels_created

    if member.bot:
        return

    try:
        config = await load_config()
        guild_config = config.get(str(member.guild.id))

        if not guild_config:
            return

        join_channel_id = guild_config.get("join_channel_id")
        category_id = guild_config.get("category_id")

        if after.channel and after.channel.id == join_channel_id:
            category = member.guild.get_channel(category_id)

            eastern = pytz.timezone("US/Eastern")
            timestamp = utc_now().astimezone(eastern).strftime("%I:%M %p %Z")

            new_channel = await member.guild.create_voice_channel(
                name=member.display_name,
                category=category,
                reason="Temporary party channel created"
            )

            temporary_channels[new_channel.id] = member.id
            total_temp_channels_created += 1
            await save_state()

            await set_voice_channel_status(new_channel.id, f"Created at {timestamp}")
            await member.move_to(new_channel)
            add_event(f"Created temp channel {new_channel.name} for {member}")

        if before.channel and before.channel.id in temporary_channels:
            if len(before.channel.members) == 0:
                old_name = before.channel.name
                temporary_channels.pop(before.channel.id, None)
                await save_state()
                await before.channel.delete(reason="Temporary party channel empty")
                add_event(f"Deleted empty temp channel {old_name}")

    except Exception as e:
        add_error(e)


if TOKEN is None:
    raise ValueError("DISCORD_TOKEN was not found in .env or Railway Variables")

bot.run(TOKEN)
