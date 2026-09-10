import asyncio
import datetime
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import json
import logging
import os

load_dotenv()

DATA_FILE = os.getenv("DATA_FILE", "data/staff_data.json")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
FAILED_REPORT_CHANNEL_ID = int(os.getenv("FAILED_REPORT_CHANNEL_ID", 0))
FOUNDER_AND_OWNER_ROLE_ID = int(os.getenv("FOUNDER_AND_OWNER_ROLE_ID"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID"))
MESSAGE_THRESHOLD = 50
CO_OWNER_ROLE_ID = int(os.getenv("CO_OWNER_ROLE_ID"))
AUTHORIZED_USER_ID = int(os.getenv("AUTHORIZED_USER_ID"))
ALLOWED_CHANNELS = set(int(x) for x in os.getenv("ALLOWED_CHANNELS", "").split(",") if x.strip())

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

staff_members_data = []
data_dirty = False

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=".", intents=intents, help_command=None)

bot = Bot()

def load_staff_data():
    global staff_members_data, data_dirty
    if not os.path.exists(DATA_FILE):
        staff_members_data = []
        data_dirty = True
        print(f"[INFO] No data file found. Creating new empty staff list.")
        return
    try:
        with open(DATA_FILE, "r") as f:
            staff_members_data = json.load(f)
            print(f"[INFO] Loaded staff data for {len(staff_members_data)} members.")
    except json.JSONDecodeError:
        staff_members_data = []
        data_dirty = True
        print("[ERROR] JSON decode error. Starting with empty staff data.")

def save_staff_data():
    global data_dirty
    dir_path = os.path.dirname(DATA_FILE)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(staff_members_data, f, indent=4)
    data_dirty = False
    print("[INFO] Staff data saved.")

async def sync_staff():
    global staff_members_data, data_dirty
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("[WARN] Guild not found for syncing staff.")
        return
    staff_role = guild.get_role(STAFF_ROLE_ID)
    if not staff_role:
        print("[WARN] Staff role not found in guild.")
        return

    current_staff_ids = {m.id for m in guild.members if staff_role in m.roles}
    stored_ids = {s["id"] for s in staff_members_data}

    for member_id in current_staff_ids - stored_ids:
        staff_members_data.append({"id": member_id, "messages": 0, "failed_days": 0, "bank": 0})
        print(f"[INFO] Added new staff member with ID {member_id}.")

    staff_members_data[:] = [s for s in staff_members_data if s["id"] in current_staff_ids]

    data_dirty = True
    save_staff_data()

@tasks.loop(minutes=5)
async def auto_save():
    if data_dirty:
        print("[INFO] Auto-save triggered.")
        save_staff_data()

@bot.event
async def on_ready():
    print(f"[INFO] Bot connected as {bot.user}.")
    load_staff_data()
    await sync_staff()
    if not auto_save.is_running():
        auto_save.start()
    if not daily_check.is_running():
        daily_check.start()

@bot.event
async def on_message(message: discord.Message):
    global staff_members_data, data_dirty
    if message.author.bot:
        return
    if ALLOWED_CHANNELS and message.channel.id not in ALLOWED_CHANNELS:
        await bot.process_commands(message)
        return
    for staff in staff_members_data:
        if staff["id"] == message.author.id:
            staff["messages"] += 1
            data_dirty = True
            print(f"[INFO] Incremented messages for {message.author.display_name}: {staff['messages']}")
            break
    await bot.process_commands(message)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    global staff_members_data, data_dirty
    staff_role = before.guild.get_role(STAFF_ROLE_ID)
    if not staff_role:
        return

    if staff_role not in before.roles and staff_role in after.roles:
        if not any(s["id"] == after.id for s in staff_members_data):
            staff_members_data.append({"id": after.id, "messages": 0, "failed_days": 0, "bank": 0})
            data_dirty = True
            print(f"[INFO] {after.display_name} got staff role and was added.")

    if staff_role in before.roles and staff_role not in after.roles:
        staff_members_data[:] = [s for s in staff_members_data if s["id"] != after.id]
        data_dirty = True
        print(f"[INFO] {after.display_name} lost staff role and was removed.")

@bot.event
async def on_member_remove(member: discord.Member):
    global staff_members_data, data_dirty
    if any(s["id"] == member.id for s in staff_members_data):
        staff_members_data[:] = [s for s in staff_members_data if s["id"] != member.id]
        data_dirty = True
        print(f"[INFO] {member.display_name} left the server and was removed from staff data.")

@bot.command(name="h")
async def help_command(ctx):
    await ctx.send(
        "📋 **Staff Bot Commands** 📋\n\n"
        "**.m @staff-member**\nCheck message count of a staff member.\n\n"
        "**.d @staff-member**\nCheck failed days of a staff member.\n\n"
        "**.b @staff-member**\nCheck message bank of a staff member.\n\n"
        "**.l**\nList all staff members and their stats.\n\n"
        "**.sd @staff-member <days>**\nSet failed days for a staff member to a specific amount (Founder/Owner and Co-Owner only)"
    )

@bot.command(name="l")
async def list_staff(ctx):
    if not staff_members_data:
        await ctx.send("No staff data found.")
        return
    embed = discord.Embed(title="📊 Staff Activity Overview", color=discord.Color.blue())
    guild = bot.get_guild(GUILD_ID)
    for staff in staff_members_data:
        member = guild.get_member(staff["id"])
        name = member.display_name if member else f"Unknown ({staff['id']})"
        embed.add_field(
            name=name,
            value=f"Messages: {staff['messages']}\nFailed Days: {staff['failed_days']}\nBank: {staff['bank']}",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="m")
async def check_messages(ctx, member: discord.Member = None):
    if not member:
        await ctx.send("Please mention a staff member. Usage: `.m @staff-member`")
        return
    for staff in staff_members_data:
        if staff["id"] == member.id:
            await ctx.send(f"{member.display_name} has sent {staff['messages']} messages today.")
            return
    await ctx.send("That user is not registered as staff.")

@bot.command(name="d")
async def check_days(ctx, member: discord.Member = None):
    if not member:
        await ctx.send("Please mention a staff member. Usage: `.d @staff-member`")
        return
    for staff in staff_members_data:
        if staff["id"] == member.id:
            await ctx.send(f"{member.display_name} has {staff['failed_days']} failed days.")
            return
    await ctx.send("That user is not registered as staff.")

@bot.command(name="b")
async def check_bank(ctx, member: discord.Member = None):
    if not member:
        await ctx.send("Please mention a staff member. Usage: `.b @staff-member`")
        return
    for staff in staff_members_data:
        if staff["id"] == member.id:
            await ctx.send(f"{member.display_name} has {staff['bank']} messages in their bank.")
            return
    await ctx.send("That user is not registered as staff.")

def has_sd_permission(ctx):
    role_ids = [r.id for r in ctx.author.roles]
    return (
        FOUNDER_AND_OWNER_ROLE_ID in role_ids or
        CO_OWNER_ROLE_ID in role_ids or
        ctx.author.id == AUTHORIZED_USER_ID
    )

@bot.command(name="sd")
async def set_days(ctx, target: str = None, days: int = None):
    global data_dirty
    if not has_sd_permission(ctx):
        await ctx.send("You do not have permission to use this command.")
        return
    if target is None:
        await ctx.send("Please provide a member and number of days. Usage: `.sd @staff-member <days>` or `.sd @everyone <days>`")
        return
    if days is None:
        await ctx.send("Please provide the number of days. Usage: `.sd @staff-member <days>` or `.sd @everyone <days>`")
        return
    if days < 0:
        await ctx.send("Days cannot be negative.")
        return
    if target.lower() == "@everyone":
        for staff in staff_members_data:
            staff["failed_days"] = days
        data_dirty = True
        save_staff_data()
        await ctx.send(f"Set failed days to {days} for all staff members.")
        return
    if ctx.message.mentions:
        member = ctx.message.mentions[0]
        for staff in staff_members_data:
            if staff["id"] == member.id:
                staff["failed_days"] = days
                data_dirty = True
                save_staff_data()
                await ctx.send(f"Set failed days for {member.display_name} to {days}.")
                return
        await ctx.send("That user is not registered as staff.")
        return
    await ctx.send("Invalid usage. Usage: `.sd @staff-member <days>` or `.sd @everyone <days>`")

@tasks.loop(hours=24)
async def daily_check():
    try:
        now_ist = datetime.datetime.now(IST)
        target = now_ist.replace(hour=1, minute=0, second=0, microsecond=0)
        if now_ist >= target:
            target += datetime.timedelta(days=1)
        await asyncio.sleep((target - now_ist).total_seconds())

        global staff_members_data, data_dirty
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return

        failed_today = []

        for staff in staff_members_data:
            if staff["messages"] > MESSAGE_THRESHOLD:
                extra = staff["messages"] - MESSAGE_THRESHOLD
                staff["bank"] += extra // 2

            needed = MESSAGE_THRESHOLD - staff["messages"]

            if needed > 0:
                if staff["bank"] >= needed:
                    staff["bank"] -= needed
                    staff["messages"] += needed
                else:
                    staff["failed_days"] += 1
                    member = guild.get_member(staff["id"])
                    name = member.display_name if member else f"Unknown ({staff['id']})"
                    failed_today.append(
                      f"{name}: {staff['messages']} messages "
                      f"(Bank had: {staff['bank']})"
                    )

            staff["messages"] = 0

        data_dirty = True
        save_staff_data()

        if FAILED_REPORT_CHANNEL_ID:
            channel = guild.get_channel(FAILED_REPORT_CHANNEL_ID)
            if channel:
                if failed_today:
                    report = "📉 **Failed Staff Today:**\n" + "\n".join(failed_today)
                else:
                    report = "🎉 No failed staff today!"
                await channel.send(report)
    except Exception as e:
        print(f"[ERROR] daily_check failed: {e}")

try:
    bot.run(DISCORD_TOKEN, log_handler=handler, log_level=logging.INFO)
finally:
    if data_dirty:
        save_staff_data()
