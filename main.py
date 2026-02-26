import asyncio
import datetime
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import json
import logging
import os

load_dotenv()

DATA_FILE = os.getenv("DATA_FILE", "/home/data/staff_data.json")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
FAILED_REPORT_CHANNEL_ID = int(os.getenv("FAILED_REPORT_CHANNEL_ID", 0))
FOUNDER_AND_OWNER_ROLE_ID = int(os.getenv("FOUNDER_AND_OWNER_ROLE_ID"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID"))
MESSAGE_THRESHOLD = int(os.getenv("MESSAGE_THRESHOLD", 200))

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
        staff_members_data.append({"id": member_id, "messages": 0, "failed_days": 0})
        print(f"[INFO] Added new staff member with ID {member_id}.")

    staff_members_data[:] = [s for s in staff_members_data if s["id"] in current_staff_ids]

    data_dirty = True

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
            staff_members_data.append({"id": after.id, "messages": 0, "failed_days": 0})
            data_dirty = True
            print(f"[INFO] {after.display_name} got staff role and was added.")

    if staff_role in before.roles and staff_role not in after.roles:
        staff_members_data[:] = [s for s in staff_members_data if s["id"] != after.id]
        data_dirty = True
        print(f"[INFO] {after.display_name} lost staff role and was removed.")

# ---------------- Abbreviated commands ----------------

@bot.command(name="h")
async def help_command(ctx):
    print(f"[COMMAND] .h called by {ctx.author}")
    await ctx.send(
        "📋 **Staff Bot Commands** 📋\n\n"
        "**.d @staff-member**\n`Check failed days of a staff member.`\n\n"
        "**.l**\n`List all staff members and their stats.`\n\n"
        "**.rd @staff-member**\n`Reset failed days for a staff member (Founder/Owner only)`"
    )

@bot.command(name="l")
async def list_staff(ctx):
    print(f"[COMMAND] .l called by {ctx.author}")
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
            value=f"Messages: {staff['messages']}\nFailed Days: {staff['failed_days']}",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="d")
async def check_days(ctx, member: discord.Member):
    print(f"[COMMAND] .d called by {ctx.author} for {member}")
    for staff in staff_members_data:
        if staff["id"] == member.id:
            await ctx.send(f"{member.display_name} has {staff['failed_days']} failed days.")
            print(f"[INFO] {member.display_name} has {staff['failed_days']} failed days.")
            return
    await ctx.send("That user is not registered as staff.")

@bot.command(name="rd")
async def reset_days(ctx, target=None):
    global data_dirty
    print(f"[COMMAND] .rd called by {ctx.author} with target {target}")
    if FOUNDER_AND_OWNER_ROLE_ID not in [r.id for r in ctx.author.roles]:
        await ctx.send("You do not have permission to use this command.")
        return
    if not target:
        await ctx.send("Please mention a staff member or @everyone.")
        return
    if target == "@everyone":
        for staff in staff_members_data:
            staff["failed_days"] = 0
        data_dirty = True
        await ctx.send("Reset failed days for all staff members.")
        print("[INFO] Reset failed days for all staff.")
        return
    if ctx.message.mentions:
        member = ctx.message.mentions[0]
        for staff in staff_members_data:
            if staff["id"] == member.id:
                staff["failed_days"] = 0
                data_dirty = True
                await ctx.send(f"Reset failed days for {member.display_name}.")
                print(f"[INFO] Reset failed days for {member.display_name}")
                return
        await ctx.send("That user is not registered as staff.")
        return
    await ctx.send("Invalid input. Mention a user or use @everyone.")

# ---------------- Daily check loop ----------------

@tasks.loop(hours=24)
async def daily_check():
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ist = now_utc.astimezone(ist)
    target = now_ist.replace(hour=23, minute=55, second=0, microsecond=0)
    if now_ist >= target:
        target += datetime.timedelta(days=1)
    await asyncio.sleep((target - now_ist).total_seconds())

    global staff_members_data, data_dirty
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("[WARN] Guild not found for daily check.")
        return

    failed_today = []
    for staff in staff_members_data:
        if staff["messages"] < MESSAGE_THRESHOLD:
            staff["failed_days"] += 1
            member = guild.get_member(staff["id"])
            name = member.display_name if member else f"Unknown ({staff['id']})"
            failed_today.append(f"{name}: {staff['messages']} messages")
            print(f"[INFO] {name} failed today with {staff['messages']} messages")
        staff["messages"] = 0

    data_dirty = True

    if FAILED_REPORT_CHANNEL_ID:
        channel = guild.get_channel(FAILED_REPORT_CHANNEL_ID)
        if channel:
            if failed_today:
                report = "📉 **Failed Staff Today:**\n" + "\n".join(failed_today)
            else:
                report = "🎉 No failed staff today!"
            await channel.send(report)
            print(f"[INFO] Daily report sent.")
        else:
            print("⚠️ FAILED_REPORT_CHANNEL_ID not found in guild.")
    else:
        print("⚠️ FAILED_REPORT_CHANNEL_ID not set; skipping daily report.")

# ---------------- Run bot ----------------

try:
    bot.run(DISCORD_TOKEN, log_handler=handler, log_level=logging.INFO)
finally:
    if data_dirty:
        save_staff_data()