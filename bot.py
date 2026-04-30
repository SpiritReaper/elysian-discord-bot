import os
import json
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

CATEGORY_NAME = "Form a Party"
JOIN_CHANNEL_NAME = "New Party"

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

temporary_channels = {}
bot_start_time = datetime.now(pytz.utc)
total_temp_channels_created = 0
recent_errors = []
stats_server_started = False


def add_error(error_message):
    timestamp = datetime.now(pytz.utc).strftime("%Y-%m-%d %I:%M:%S %p UTC")
    recent_errors.append({"time": timestamp, "error": str(error_message)})

    if len(recent_errors) > 10:
        recent_errors.pop(0)


def load_config():
    try:
        with open(CONFIG_FILE, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}
    except Exception as e:
        add_error(e)
        return {}


def save_config(data):
    try:
        with open(CONFIG_FILE, "w") as file:
            json.dump(data, file, indent=4)
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


async def stats_handler(request):
    if STATS_API_KEY:
        auth_header = request.headers.get("Authorization", "")
        expected = f"Bearer {STATS_API_KEY}"

        if auth_header != expected:
            return web.json_response({"error": "Unauthorized"}, status=401)

    uptime_seconds = int((datetime.now(pytz.utc) - bot_start_time).total_seconds())

    data = {
        "bot_online": bot.is_ready(),
        "bot_name": str(bot.user) if bot.user else None,
        "uptime": format_uptime(uptime_seconds),
        "uptime_seconds": uptime_seconds,
        "servers": len(bot.guilds),
        "active_temp_channels": len(temporary_channels),
        "total_temp_channels_created": total_temp_channels_created,
        "recent_errors": recent_errors,
        "timestamp_eastern": datetime.now(pytz.utc)
            .astimezone(pytz.timezone("US/Eastern"))
            .strftime("%m/%d/%Y %I:%M:%S %p %Z")
    }

    return web.json_response(data)


async def health_handler(request):
    return web.json_response({
        "status": "ok",
        "bot_ready": bot.is_ready(),
        "port": PORT
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

    await bot.tree.sync()

    print(f"Logged in as {bot.user}")
    print("Slash commands synced globally")


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

    join_channel = discord.utils.get(
        category.voice_channels,
        name=JOIN_CHANNEL_NAME
    )

    if join_channel is None:
        join_channel = await guild.create_voice_channel(
            name=JOIN_CHANNEL_NAME,
            category=category
        )

    config = load_config()
    config[str(guild.id)] = {
        "category_id": category.id,
        "join_channel_id": join_channel.id
    }
    save_config(config)

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

    await interaction.response.send_message(
        f"Locked **{channel.name}**.",
        ephemeral=True
    )


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

    await interaction.response.send_message(
        f"Unlocked **{channel.name}**.",
        ephemeral=True
    )


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
        config = load_config()
        guild_config = config.get(str(member.guild.id))

        if not guild_config:
            return

        join_channel_id = guild_config.get("join_channel_id")
        category_id = guild_config.get("category_id")

        if after.channel and after.channel.id == join_channel_id:
            category = member.guild.get_channel(category_id)

            eastern = pytz.timezone("US/Eastern")
            timestamp = datetime.now(pytz.utc).astimezone(eastern).strftime("%I:%M %p %Z")

            new_channel = await member.guild.create_voice_channel(
                name=member.display_name,
                category=category
            )

            temporary_channels[new_channel.id] = member.id
            total_temp_channels_created += 1

            await set_voice_channel_status(
                new_channel.id,
                f"Created at {timestamp}"
            )

            await member.move_to(new_channel)

        if before.channel and before.channel.id in temporary_channels:
            if len(before.channel.members) == 0:
                del temporary_channels[before.channel.id]
                await before.channel.delete()

    except Exception as e:
        add_error(e)


if TOKEN is None:
    raise ValueError("DISCORD_TOKEN was not found in .env")

bot.run(TOKEN)
