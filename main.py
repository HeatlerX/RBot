import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import pytz
import os
import asyncio
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
import uvicorn
import threading

TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
ROLE_ID = int(os.environ["ROLE_ID"])

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tracked_messages = {}

app = FastAPI()

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "Bot is running"

def run_webserver():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

@bot.event
async def on_ready():
    print(f"เข้าสู่ระบบในชื่อ {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced Slash Commands: {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"Sync Error: {e}")
    send_message_at_time.start()

@tasks.loop(minutes=1)
async def send_message_at_time():
    tz = pytz.timezone("Asia/Bangkok")
    now = datetime.datetime.now(tz)
    if now.hour == 12 and now.minute == 0:
        await send_vote_message("แดกข้าวเที่ยงยัง\n✅: 0 ❌: 0", mention_role_id=ROLE_ID)
    elif now.hour == 17 and now.minute == 0:
        await send_vote_message("แดกข้าวเย็นยัง\n✅: 0 ❌: 0", mention_role_id=ROLE_ID)

async def send_vote_message(content, mention_role_id=None):
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"ไม่พบ channel ID: {CHANNEL_ID}")
        return

    mention_text = f"<@&{mention_role_id}>\n" if mention_role_id else ""
    sent_msg = await channel.send(
        mention_text + content,
        allowed_mentions=discord.AllowedMentions(roles=True)
    )
    await sent_msg.add_reaction("✅")
    await sent_msg.add_reaction("❌")
    tracked_messages[sent_msg.id] = {"message": sent_msg, "type": "yes_no"}

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    message = reaction.message
    if message.id in tracked_messages:
        tracked = tracked_messages[message.id]

        if tracked["type"] == "yes_no":
            if reaction.emoji not in ["✅", "❌"]:
                await reaction.remove(user)
                return

            opposite = "❌" if reaction.emoji == "✅" else "✅"
            for react in message.reactions:
                if react.emoji == opposite:
                    async for r_user in react.users():
                        if r_user.id == user.id:
                            await react.remove(user)

        elif tracked["type"] == "multi_option":
            if reaction.emoji not in tracked["emojis"]:
                await reaction.remove(user)
                return

            for react in message.reactions:
                if react.emoji in tracked["emojis"] and react.emoji != reaction.emoji:
                    async for r_user in react.users():
                        if r_user.id == user.id:
                            await react.remove(user)

        await update_vote_count(message)

@bot.event
async def on_reaction_remove(reaction, user):
    if user.bot:
        return
    if reaction.message.id in tracked_messages:
        await update_vote_count(reaction.message)

async def update_vote_count(message):
    tracked = tracked_messages.get(message.id)
    if not tracked:
        return

    lines = message.content.split("\n")
    mention_line = lines[0] if lines[0].startswith("<@&") else ""
    question_line = lines[1] if mention_line and len(lines) > 1 else lines[0]

    if tracked["type"] == "yes_no":
        yes_mentions = []
        no_mentions = []

        for reaction in message.reactions:
            if reaction.emoji == "✅":
                users = [user async for user in reaction.users() if not user.bot]
                yes_mentions = [user.mention for user in users]
            elif reaction.emoji == "❌":
                users = [user async for user in reaction.users() if not user.bot]
                no_mentions = [user.mention for user in users]

        new_content = (
            (mention_line + "\n" if mention_line else "") +
            f"{question_line}\n" +
            f"✅: {len(yes_mentions)} คน ({', '.join(yes_mentions) if yes_mentions else 'ไม่มี'})\n" +
            f"❌: {len(no_mentions)} คน ({', '.join(no_mentions) if no_mentions else 'ไม่มี'})"
        )
        await message.edit(content=new_content)

    elif tracked["type"] == "multi_option":
        emojis = tracked["emojis"]
        options = tracked["options"]
        vote_data = {emoji: [] for emoji in emojis}

        for reaction in message.reactions:
            if reaction.emoji in emojis:
                users = [user async for user in reaction.users() if not user.bot]
                vote_data[reaction.emoji] = [user.mention for user in users]

        new_lines = []
        if mention_line:
            new_lines.append(mention_line)
        new_lines.append(question_line)

        for i, emoji in enumerate(emojis):
            users = vote_data[emoji]
            count = len(users)
            names = ", ".join(users) if users else "ไม่มี"
            new_lines.append(f"{emoji} {options[i]} - {count} คน ({names})")

        await message.edit(content="\n".join(new_lines))

@bot.tree.command(name="poll", description="สร้างโพลตัวเลือกหลายข้อ (สูงสุด 9 ตัวเลือก)")
@app_commands.describe(
    question="คำถามที่ต้องการถาม",
    options="ตัวเลือกคั่นด้วยเครื่องหมายจุลภาค เช่น: ร้าน 1, ร้าน 2, ร้าน 3"
)
async def poll(interaction: discord.Interaction, question: str, options: str):
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    choices = [opt.strip() for opt in options.split(",")]

    if not (2 <= len(choices) <= len(number_emojis)):
        await interaction.response.send_message("ใส่ตัวเลือกอย่างน้อย 2 อย่าง และไม่เกิน 9 อย่าง", ephemeral=True)
        return

    content = f"<@&{ROLE_ID}>\n{question}"
    for i, choice in enumerate(choices):
        content += f"\n{number_emojis[i]} {choice} - 0 คน"

    msg = await interaction.channel.send(content, allowed_mentions=discord.AllowedMentions(roles=True))
    for i in range(len(choices)):
        await msg.add_reaction(number_emojis[i])

    tracked_messages[msg.id] = {
        "message": msg,
        "type": "multi_option",
        "emojis": number_emojis[:len(choices)],
        "options": choices
    }

    await interaction.response.send_message("สร้างโพลเรียบร้อย!", ephemeral=True)

@bot.tree.command(name="ikwai")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("ngo")

# รัน webserver ใน thread แยก เพื่อไม่ให้บล็อกบอท
threading.Thread(target=run_webserver, daemon=True).start()

# รันบอท (blocking call)
bot.run(TOKEN)
