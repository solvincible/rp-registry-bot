import discord
from discord import app_commands
import aiosqlite
import os

intents = discord.Intents.default()
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

DB = "characters.db"
CLAIM_LIMIT = 4
STAFF_ROLE = "Staff"

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            guild_id INTEGER,
            name TEXT,
            alias TEXT,
            wiki TEXT,
            image TEXT,
            description TEXT,
            owner_id INTEGER,
            owner_name TEXT
        )
        """)
        await db.commit()

@bot.event
async def on_ready():
    await init_db()
    await tree.sync()
    print(f"Logged in as {bot.user}")

async def claim_count(guild_id, user_id):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM characters WHERE guild_id=? AND owner_id=?",
            (guild_id, user_id)
        )
        result = await cursor.fetchone()
        return result[0]

async def character_taken(guild_id, name):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )
        return await cursor.fetchone()

@tree.command(name="claim")
async def claim(interaction: discord.Interaction,
                name: str,
                alias: str,
                wiki: str,
                description: str,
                image: discord.Attachment = None):

    guild_id = interaction.guild.id

    count = await claim_count(guild_id, interaction.user.id)

    if count >= CLAIM_LIMIT:
        await interaction.response.send_message(
            f"You already have {CLAIM_LIMIT} claims.",
            ephemeral=True
        )
        return

    if await character_taken(guild_id, name):
        await interaction.response.send_message(
            "Character already claimed.",
            ephemeral=True
        )
        return

    img = image.url if image else ""

    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        INSERT INTO characters VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            guild_id,
            name.lower(),
            alias,
            wiki,
            img,
            description,
            interaction.user.id,
            interaction.user.name
        ))
        await db.commit()

    await interaction.response.send_message(
        f"{name} claimed by {interaction.user.mention}"
    )

@tree.command(name="drop")
async def drop(interaction: discord.Interaction, name: str):

    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT owner_id FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )

        result = await cursor.fetchone()

        if not result:
            await interaction.response.send_message(
                "Character not found.",
                ephemeral=True
            )
            return

        if result[0] != interaction.user.id:
            await interaction.response.send_message(
                "You don't own this character.",
                ephemeral=True
            )
            return

        await db.execute(
            "DELETE FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )
        await db.commit()

    await interaction.response.send_message(
        f"{name} dropped."
    )

@tree.command(name="remove_character")
async def remove_character(interaction: discord.Interaction, name: str):

    roles = [role.name for role in interaction.user.roles]

    if STAFF_ROLE not in roles:
        await interaction.response.send_message(
            "Staff only.",
            ephemeral=True
        )
        return

    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "DELETE FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )
        await db.commit()

    await interaction.response.send_message(
        f"{name} removed by staff."
    )

@tree.command(name="character")
async def character(interaction: discord.Interaction, name: str):

    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )

        char = await cursor.fetchone()

    if not char:
        await interaction.response.send_message(
            "Character not found.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"{char[1].title()} ({char[2]})",
        description=char[5]
    )

    embed.add_field(name="Owner", value=char[7])
    embed.add_field(name="Wiki", value=char[3])

    if char[4]:
        embed.set_image(url=char[4])

    await interaction.response.send_message(embed=embed)

@tree.command(name="roster")
async def roster(interaction: discord.Interaction):

    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT name, owner_name FROM characters WHERE guild_id=?",
            (guild_id,)
        )

        rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("No characters yet.")
        return

    text = "\n".join(
        [f"{r[0].title()} — {r[1]}" for r in rows]
    )

    await interaction.response.send_message(text[:2000])

@bot.event
async def on_member_remove(member):

    guild_id = member.guild.id

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "DELETE FROM characters WHERE guild_id=? AND owner_id=?",
            (guild_id, member.id)
        )

        await db.commit()

bot.run(os.getenv("TOKEN"))