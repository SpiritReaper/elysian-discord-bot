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

CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
STATE_FILE = os.getenv("STATE_FILE", "elysian_state.json")

CATEGORY_NAME = "Form a Party"
JOIN_CHANNEL_NAME = "New Party"

# If true, empty temp channels found after startup/redeploy are removed.
# This keeps old orphaned temp channels from breaking the Form a Party category.
CLEAN_EMPTY_TEMP_CHANNELS_ON_STARTUP = os.getenv(
    "CLEAN_EMPTY_TEMP_CHANNELS_ON_STARTUP", "true"
).lower() in ("1", "true", "yes", "y")

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# channel_id -> owner_id
# This is saved during runtime and rebuilt from Discord after Railway redeploys.
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
restore_lock = asyncio.Lock()


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
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)

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
                str(channel_id): int(owner_id)
                for channel_id, owner_id in temporary_channels.items()
            },
            "total_temp_channels_created": int(total_temp_channels_created),
            "saved_at_utc": now_string(),
        }
        try:
            atomic_write_json(STATE_FILE, state)
        except Exception as e:
            add_error(e)


async def set_voice_channel_status(channel_id, status):
    url = f"https://discord.com/api/v10/channels/{channel_id}/voice-status"
    headers = {
        "Authorization": f"Bot {TOKEN}",
        "Content-Type": "application/json",
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


def first_non_bot_member(channel):
    for member in channel.members:
        if not member.bot:
            return member
    return None


async def recover_setup_from_discord():
    """Recover Form a Party setup after Railway redeploys.

    If CONFIG_FILE is missing, empty, or points to deleted channels, this scans Discord
    by name and saves the correct category_id and join_channel_id back to persistent
    storage. This prevents needing /setup after every redeploy.
    """
    config = await load_config()
    changed = False

    for guild in bot.guilds:
        guild_key = str(guild.id)
        guild_config = config.get(guild_key, {})

        category_id = guild_config.get("category_id")
        join_channel_id = guild_config.get("join_channel_id")

        category = guild.get_channel(category_id) if category_id else None
        join_channel = guild.get_channel(join_channel_id) if join_channel_id else None

        if category and join_channel:
            continue

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if not category:
            continue

        join_channel = discord.utils.get(category.voice_channels, name=JOIN_CHANNEL_NAME)
        if not join_channel:
            continue

        config[guild_key] = {
            "category_id": category.id,
            "join_channel_id": join_channel.id,
        }
        changed = True
        add_event(f"Recovered setup from Discord in {guild.name}")

    if changed:
        await save_config(config)

    return config


async def restore_temporary_channels():
    """Rebuild temporary channel ownership after Railway restarts/redeploys.

    Railway memory is wiped on redeploy, so the bot cannot rely only on the in-memory
    temporary_channels dict. This function uses a hybrid recovery strategy:

    1. Load saved state from elysian_state.json if it exists for this runtime.
    2. Scan Discord's Form a Party category for existing temp voice channels.
    3. Ignore the configured New Party join channel.
    4. Restore known channels from saved state when possible.
    5. Adopt active unknown temp channels by assigning the first non-bot member as owner.
    6. Delete empty orphan temp channels so the category stays clean.
    """
    global temporary_channels

    async with restore_lock:
        await load_state()
        config = await recover_setup_from_discord()

        restored = 0
        adopted = 0
        deleted = 0
        missing_removed = 0

        valid_channel_ids = set()

        for guild in bot.guilds:
            guild_config = config.get(str(guild.id))
            if not guild_config:
                continue

            category_id = guild_config.get("category_id")
            join_channel_id = guild_config.get("join_channel_id")
            category = guild.get_channel(category_id)

            if not category or not hasattr(category, "voice_channels"):
                add_error(f"Configured category not found in guild {guild.name}. Run /setup again.")
                continue

            for channel in list(category.voice_channels):
                if channel.id == join_channel_id or channel.name == JOIN_CHANNEL_NAME:
                    continue

                owner_member = first_non_bot_member(channel)

                # Existing known temp channel from saved runtime state.
                if channel.id in temporary_channels:
                    if owner_member:
                        # Keep saved owner if possible, but repair bad/old owners.
                        saved_owner_id = temporary_channels.get(channel.id)
                        saved_owner = guild.get_member(saved_owner_id)
                        if saved_owner is None:
                            temporary_channels[channel.id] = owner_member.id
                        valid_channel_ids.add(channel.id)
                        restored += 1
                    else:
                        temporary_channels.pop(channel.id, None)
                        if CLEAN_EMPTY_TEMP_CHANNELS_ON_STARTUP:
                            try:
                                await channel.delete(reason="Cleaning up empty temporary channel after restart")
                                deleted += 1
                            except Exception as e:
                                add_error(e)
                    continue

                # Unknown channel in Form a Party category after restart.
                # If occupied, adopt it so commands like /lock, /unlock, and /limit still work.
                if owner_member:
                    temporary_channels[channel.id] = owner_member.id
                    valid_channel_ids.add(channel.id)
                    adopted += 1
                    continue

                # Unknown and empty means it is likely an orphan temp channel.
                if CLEAN_EMPTY_TEMP_CHANNELS_ON_STARTUP:
                    try:
                        await channel.delete(reason="Cleaning up orphaned empty temporary channel after restart")
                        deleted += 1
                    except Exception as e:
                        add_error(e)

        # Remove saved channels that no longer exist in Discord.
        all_discord_channel_ids = {channel.id for guild in bot.guilds for channel in guild.channels}
        for channel_id in list(temporary_channels.keys()):
            if channel_id not in all_discord_channel_ids:
                temporary_channels.pop(channel_id, None)
                missing_removed += 1

        await save_state()
        add_event(
            "Restored temp channels after startup. "
            f"Restored: {restored}, adopted: {adopted}, deleted: {deleted}, "
            f"missing_removed: {missing_removed}"
        )


def authorized(request):
    if not STATS_API_KEY:
        return True
    auth_header = request.headers.get("Authorization", "")
    return auth_header == f"Bearer {STATS_API_KEY}"


def rate_limited(request, limit=60):
    ip = request.remote or "unknown"
    minute_key = utc_now().strftime("%Y%m%d%H%M")
    key = f"{ip}:{minute_key}"
    rate_limit_hits[key] = rate_limit_hits.get(key, 0) + 1

    if len(rate_limit_hits) > 500:
        current_hour = utc_now().strftime("%Y%m%d%H")
        for old_key in list(rate_limit_hits.keys()):
            if current_hour not in old_key:
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
        "tracked_temp_channels": temporary_channels,
        "total_temp_channels_created": total_temp_channels_created,
        "recent_errors": recent_errors,
        "recent_events": recent_events,
        "timestamp_eastern": utc_now()
        .astimezone(pytz.timezone("US/Eastern"))
        .strftime("%m/%d/%Y %I:%M:%S %p %Z"),
    }

    stats_cache["time"] = now_ts
    stats_cache["data"] = data
    return web.json_response(data)


async def health_handler(request):
    return web.json_response({
        "status": "ok",
        "bot_ready": bot.is_ready(),
        "port": PORT,
        "active_temp_channels": len(temporary_channels),
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

    # Small delay gives Discord's voice-state cache a moment to populate after reconnect.
    await asyncio.sleep(2)
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
    await asyncio.sleep(2)
    await restore_temporary_channels()


@bot.tree.command(name="setup", description="Create the Form a Party temp voice setup")
async def setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message(
            "You need Manage Channels permission to use this command.",
            ephemeral=True,
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
            category=category,
            reason="Form a Party setup join channel created",
        )

    config = await load_config()
    config[str(guild.id)] = {
        "category_id": category.id,
        "join_channel_id": join_channel.id,
    }
    await save_config(config)
    await restore_temporary_channels()
    add_event(f"/setup completed in {guild.name}")

    await interaction.response.send_message(
        f"Setup complete. Created **{CATEGORY_NAME}** with **{JOIN_CHANNEL_NAME}**.",
        ephemeral=True,
    )


@bot.tree.command(name="lock", description="Lock your temporary voice channel")
async def lock(interaction: discord.Interaction):
    # Defer immediately so Discord does not show "The application did not respond"
    # while permissions/state saving are being processed.
    await interaction.response.defer(ephemeral=True)

    try:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
                "You need to be inside your temporary voice channel to lock it.",
                ephemeral=True,
            )
            return

        channel = interaction.user.voice.channel

        # Safety recovery: if this channel exists in the Form a Party category but was not
        # tracked after a reconnect/redeploy, adopt it for the user running /lock.
        if channel.id not in temporary_channels:
            config = await load_config()
            guild_config = config.get(str(interaction.guild.id))

            if not guild_config:
                config = await recover_setup_from_discord()
                guild_config = config.get(str(interaction.guild.id))

            category_id = guild_config.get("category_id") if guild_config else None
            join_channel_id = guild_config.get("join_channel_id") if guild_config else None

            if channel.category and channel.category.id == category_id and channel.id != join_channel_id:
                temporary_channels[channel.id] = interaction.user.id
                await save_state()
                add_event(f"Auto-adopted {channel.name} for {interaction.user} during /lock")

        if temporary_channels.get(channel.id) != interaction.user.id:
            await interaction.followup.send(
                "You can only lock a temporary voice channel that you created.",
                ephemeral=True,
            )
            return

        everyone = interaction.guild.default_role

        await channel.set_permissions(everyone, connect=False)
        await channel.set_permissions(interaction.user, connect=True)
        add_event(f"{interaction.user} locked {channel.name}")

        await interaction.followup.send(
            f"Locked **{channel.name}**.",
            ephemeral=True,
        )

    except discord.Forbidden:
        add_error("Missing permission while trying to lock channel")
        await interaction.followup.send(
            "I do not have permission to lock this channel. Make sure my bot role has Manage Channels and is high enough in the role list.",
            ephemeral=True,
        )

    except Exception as e:
        add_error(e)
        await interaction.followup.send(
            f"Lock failed: {e}",
            ephemeral=True,
        )


@bot.tree.command(name="unlock", description="Unlock your temporary voice channel")
async def unlock(interaction: discord.Interaction):
    if not user_owns_channel(interaction):
        await interaction.response.send_message(
            "You can only unlock a temporary voice channel that you created.",
            ephemeral=True,
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
            ephemeral=True,
        )
        return

    if amount < 0 or amount > 99:
        await interaction.response.send_message(
            "Please choose a number between 0 and 99. Use 0 for no limit.",
            ephemeral=True,
        )
        return

    channel = interaction.user.voice.channel
    await channel.edit(user_limit=amount)

    limit_text = "no limit" if amount == 0 else str(amount)
    add_event(f"{interaction.user} set limit for {channel.name} to {limit_text}")

    await interaction.response.send_message(
        f"User limit for **{channel.name}** set to **{limit_text}**.",
        ephemeral=True,
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
            config = await recover_setup_from_discord()
            guild_config = config.get(str(member.guild.id))

        if not guild_config:
            return

        join_channel_id = guild_config.get("join_channel_id")
        category_id = guild_config.get("category_id")

        # User joined the New Party channel, so create their temp channel.
        if after.channel and after.channel.id == join_channel_id:
            category = member.guild.get_channel(category_id)
            if category is None:
                add_error(f"Category ID {category_id} not found. Run /setup again.")
                return

            eastern = pytz.timezone("US/Eastern")
            timestamp = utc_now().astimezone(eastern).strftime("%I:%M %p %Z")

            new_channel = await member.guild.create_voice_channel(
                name=member.display_name,
                category=category,
                reason="Temporary party channel created",
            )

            temporary_channels[new_channel.id] = member.id
            total_temp_channels_created += 1
            await save_state()

            await set_voice_channel_status(new_channel.id, f"Created at {timestamp}")
            await member.move_to(new_channel)
            add_event(f"Created temp channel {new_channel.name} for {member}")

        # A tracked temp channel became empty, so delete it.
        if before.channel and before.channel.id in temporary_channels:
            if len([m for m in before.channel.members if not m.bot]) == 0:
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
