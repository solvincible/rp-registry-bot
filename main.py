import discord
from discord import app_commands
import aiosqlite
import os

# ─── CONFIG ───────────────────────────────────────────────────────────────────
CLAIM_LIMIT         = 4
APPROVAL_CHANNEL_ID = 1492095997675962428
PENDING_CHANNEL_ID  = 1492096016470773810
STAFF_ROLE_ID       = 1491359486370385930
# ──────────────────────────────────────────────────────────────────────────────

DB = "characters.db"

intents         = discord.Intents.default()
intents.members = True

bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# ─── DATABASE ─────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id        INTEGER,
            name            TEXT,
            alias           TEXT,
            wiki            TEXT,
            image           TEXT,
            description     TEXT,
            owner_id        INTEGER,
            owner_name      TEXT,
            status          TEXT    DEFAULT 'pending',
            approved_msg_id INTEGER DEFAULT NULL,
            pending_msg_id  INTEGER DEFAULT NULL
        )
        """)
        await db.commit()


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def is_staff(member: discord.Member) -> bool:
    return any(r.id == STAFF_ROLE_ID for r in member.roles)


async def claim_count(guild_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM characters WHERE guild_id=? AND owner_id=? AND status='approved'",
            (guild_id, user_id)
        )
        return (await cur.fetchone())[0]


def pending_embed(char_name: str, alias: str, wiki: str,
                  description: str, image: str, owner: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title=f"{char_name.title()}  ·  {alias}",
        description=description,
        colour=discord.Colour.from_rgb(255, 180, 0),   # warm gold — awaiting review
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Submitted by", value=owner.mention if owner else "Unknown", inline=True)
    embed.add_field(name="Wiki",         value=wiki,                                  inline=True)
    if image:
        embed.set_thumbnail(url=image)
    embed.set_footer(text="Awaiting staff review")
    return embed


def approved_embed(char_name: str, alias: str, wiki: str,
                   description: str, image: str, owner: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title=f"{char_name.title()}  ·  {alias}",
        description=description,
        colour=discord.Colour.from_rgb(87, 242, 135),   # mint green — approved
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Claimed by", value=owner.mention if owner else "Unknown", inline=True)
    embed.add_field(name="Wiki",       value=wiki,                                   inline=True)
    if image:
        embed.set_image(url=image)
    embed.set_footer(text="✓  Approved")
    return embed


async def delete_message_safe(guild: discord.Guild, channel_id: int, message_id: int):
    if not channel_id or not message_id:
        return
    try:
        ch = guild.get_channel(channel_id)
        if ch:
            msg = await ch.fetch_message(message_id)
            await msg.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def _remove_character(guild: discord.Guild, char: tuple):
    await delete_message_safe(guild, APPROVAL_CHANNEL_ID, char[10])
    await delete_message_safe(guild, PENDING_CHANNEL_ID,  char[11])
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM characters WHERE id=?", (char[0],))
        await db.commit()


# ─── /claim ───────────────────────────────────────────────────────────────────

@tree.command(name="claim", description="Submit a character claim for staff approval.")
@app_commands.describe(
    name="Character's full name",
    alias="Alias / codename",
    wiki="Link to character wiki page",
    description="Short character description",
    image="Optional character image"
)
async def claim(
    interaction: discord.Interaction,
    name: str,
    alias: str,
    wiki: str,
    description: str,
    image: discord.Attachment = None
):
    if interaction.channel_id != PENDING_CHANNEL_ID:
        await interaction.response.send_message(
            f"Claims can only be submitted in <#{PENDING_CHANNEL_ID}>.", ephemeral=True
        )
        return

    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT 1 FROM characters WHERE guild_id=? AND (name=? OR alias=?)",
            (guild_id, name.lower(), alias.lower())
        )
        if await cur.fetchone():
            await interaction.response.send_message(
                "A character with that name or alias is already claimed or pending approval.",
                ephemeral=True
            )
            return

    if await claim_count(guild_id, interaction.user.id) >= CLAIM_LIMIT:
        await interaction.response.send_message(
            f"You already have {CLAIM_LIMIT} approved characters. Drop one before claiming another.",
            ephemeral=True
        )
        return

    img_url = image.url if image else ""

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO characters
               (guild_id, name, alias, wiki, image, description, owner_id, owner_name, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (guild_id, name.lower(), alias, wiki, img_url,
             description, interaction.user.id, interaction.user.name)
        )
        await db.commit()

    embed = pending_embed(name, alias, wiki, description, img_url, interaction.user)
    view  = ApprovalView(name.lower(), guild_id)
    msg   = await interaction.channel.send(
        content=f"{interaction.user.mention} submitted a claim — pending staff review.",
        embed=embed,
        view=view
    )

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE characters SET pending_msg_id=? WHERE guild_id=? AND name=?",
            (msg.id, guild_id, name.lower())
        )
        await db.commit()

    await interaction.response.send_message(
        f"**{name.title()}** submitted. You'll be notified once staff reviews it.", ephemeral=True
    )


# ─── APPROVAL VIEW ────────────────────────────────────────────────────────────

class ApprovalView(discord.ui.View):
    def __init__(self, char_name: str, guild_id: int):
        super().__init__(timeout=None)
        self.char_name = char_name
        self.guild_id  = guild_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="btn_approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await self._approve(interaction)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="btn_deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await self._deny(interaction)

    async def _approve(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                "SELECT * FROM characters WHERE guild_id=? AND name=?",
                (self.guild_id, self.char_name)
            )
            char = await cur.fetchone()

        if not char or char[9] != "pending":
            await interaction.response.send_message("This claim is no longer pending.", ephemeral=True)
            return

        owner = interaction.guild.get_member(char[7])

        # Post full embed to approved channel
        approved_channel  = interaction.guild.get_channel(APPROVAL_CHANNEL_ID)
        approved_msg_id   = None
        if approved_channel:
            embed = approved_embed(char[2], char[3], char[4], char[6], char[5], owner)
            msg   = await approved_channel.send(embed=embed)
            approved_msg_id = msg.id

        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "UPDATE characters SET status='approved', approved_msg_id=? WHERE guild_id=? AND name=?",
                (approved_msg_id, self.guild_id, self.char_name)
            )
            await db.commit()

        # Replace the pending embed with a compact approval receipt
        try:
            channel = interaction.guild.get_channel(PENDING_CHANNEL_ID)
            msg     = await channel.fetch_message(char[11])

            receipt = discord.Embed(
                title="Claim Approved",
                description=f"**{char[2].title()}** · {char[3]}",
                colour=discord.Colour.from_rgb(87, 242, 135),
                timestamp=discord.utils.utcnow()
            )
            receipt.add_field(name="Claimed by",  value=f"<@{char[7]}>",            inline=True)
            receipt.add_field(name="Approved by", value=interaction.user.mention,    inline=True)
            if char[10]:
                receipt.add_field(
                    name="Posted to",
                    value=f"<#{APPROVAL_CHANNEL_ID}>",
                    inline=False
                )
            receipt.set_footer(text="✓  This claim is closed.")

            await msg.edit(content="", embed=receipt, view=None)
        except Exception:
            pass

        if owner:
            try:
                await owner.send(
                    f"Your claim for **{char[2].title()}** on **{interaction.guild.name}** was approved!"
                )
            except discord.Forbidden:
                pass

        await interaction.response.send_message(
            f"**{char[2].title()}** approved and posted to <#{APPROVAL_CHANNEL_ID}>.", ephemeral=True
        )

    async def _deny(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                "SELECT * FROM characters WHERE guild_id=? AND name=?",
                (self.guild_id, self.char_name)
            )
            char = await cur.fetchone()

        if not char:
            await interaction.response.send_message("Character not found.", ephemeral=True)
            return

        if char[9] != "pending":
            await interaction.response.send_message("This claim is no longer pending.", ephemeral=True)
            return

        owner = interaction.guild.get_member(char[7])

        # Replace the pending embed with a clean denial card
        try:
            channel = interaction.guild.get_channel(PENDING_CHANNEL_ID)
            msg     = await channel.fetch_message(char[11])

            denial = discord.Embed(
                title="Claim Denied",
                description=f"**{char[2].title()}** · {char[3]}",
                colour=discord.Colour.from_rgb(237, 66, 69),
                timestamp=discord.utils.utcnow()
            )
            denial.add_field(name="Submitted by", value=f"<@{char[7]}>",         inline=True)
            denial.add_field(name="Denied by",    value=interaction.user.mention, inline=True)
            denial.set_footer(text="✗  This claim has been closed.")

            await msg.edit(content="", embed=denial, view=None)
        except Exception:
            pass

        # Remove from DB
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "DELETE FROM characters WHERE guild_id=? AND name=?",
                (self.guild_id, self.char_name)
            )
            await db.commit()

        if owner:
            try:
                await owner.send(
                    f"Your claim for **{char[2].title()}** on **{interaction.guild.name}** was denied by staff."
                )
            except discord.Forbidden:
                pass

        await interaction.response.send_message(
            f"**{char[2].title()}** denied.", ephemeral=True
        )


# ─── /drop ────────────────────────────────────────────────────────────────────

@tree.command(name="drop", description="Drop one of your claimed characters.")
@app_commands.describe(name="The character's name")
async def drop(interaction: discord.Interaction, name: str):
    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )
        char = await cur.fetchone()

    if not char:
        await interaction.response.send_message("Character not found.", ephemeral=True)
        return

    if char[7] != interaction.user.id:
        await interaction.response.send_message("You don't own this character.", ephemeral=True)
        return

    await _remove_character(interaction.guild, char)
    await interaction.response.send_message(
        f"**{name.title()}** has been dropped and is now available.", ephemeral=True
    )


# ─── /remove_character (staff) ────────────────────────────────────────────────

@tree.command(name="remove_character", description="[Staff] Force-remove any character.")
@app_commands.describe(name="The character's name")
async def remove_character(interaction: discord.Interaction, name: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )
        char = await cur.fetchone()

    if not char:
        await interaction.response.send_message("Character not found.", ephemeral=True)
        return

    await _remove_character(interaction.guild, char)
    await interaction.response.send_message(
        f"**{name.title()}** removed.", ephemeral=True
    )


# ─── /transfer ────────────────────────────────────────────────────────────────

@tree.command(name="transfer", description="Transfer one of your characters to another user.")
@app_commands.describe(name="The character's name", user="The user to transfer to")
async def transfer(interaction: discord.Interaction, name: str, user: discord.Member):
    guild_id = interaction.guild.id

    if user.bot:
        await interaction.response.send_message("Cannot transfer to a bot.", ephemeral=True)
        return

    if user.id == interaction.user.id:
        await interaction.response.send_message("You can't transfer to yourself.", ephemeral=True)
        return

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )
        char = await cur.fetchone()

    if not char:
        await interaction.response.send_message("Character not found.", ephemeral=True)
        return

    if char[7] != interaction.user.id:
        await interaction.response.send_message("You don't own this character.", ephemeral=True)
        return

    if await claim_count(guild_id, user.id) >= CLAIM_LIMIT:
        await interaction.response.send_message(
            f"{user.display_name} already has {CLAIM_LIMIT} approved characters.", ephemeral=True
        )
        return

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE characters SET owner_id=?, owner_name=? WHERE guild_id=? AND name=?",
            (user.id, user.name, guild_id, name.lower())
        )
        await db.commit()

    # Update the approved embed in-place
    if char[10]:
        try:
            approved_channel = interaction.guild.get_channel(APPROVAL_CHANNEL_ID)
            if approved_channel:
                msg   = await approved_channel.fetch_message(char[10])
                embed = approved_embed(char[2], char[3], char[4], char[6], char[5], user)
                await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    await interaction.response.send_message(
        f"**{name.title()}** transferred from {interaction.user.mention} to {user.mention}."
    )


# ─── /myclaims ────────────────────────────────────────────────────────────────

@tree.command(name="myclaims", description="See all characters you currently own.")
async def myclaims(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT name, alias, status FROM characters WHERE guild_id=? AND owner_id=? ORDER BY name",
            (guild_id, interaction.user.id)
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("You have no active claims.", ephemeral=True)
        return

    approved = [r for r in rows if r[2] == "approved"]
    pending  = [r for r in rows if r[2] == "pending"]

    embed = discord.Embed(
        title=f"Claims — {interaction.user.display_name}",
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    if approved:
        embed.add_field(
            name=f"Approved ({len(approved)}/{CLAIM_LIMIT})",
            value="\n".join(f"**{r[0].title()}** · {r[1]}" for r in approved),
            inline=False
        )
    if pending:
        embed.add_field(
            name="Pending Review",
            value="\n".join(f"**{r[0].title()}** · {r[1]}" for r in pending),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── /search ──────────────────────────────────────────────────────────────────

@tree.command(name="search", description="Search for characters by name, alias, or owner.")
@app_commands.describe(query="Name, alias, or username to search for")
async def search(interaction: discord.Interaction, query: str):
    guild_id = interaction.guild.id
    q        = query.lower().strip().lstrip("@")

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """SELECT name, alias, owner_name, status FROM characters
               WHERE guild_id=? AND (name LIKE ? OR alias LIKE ? OR owner_name LIKE ?)""",
            (guild_id, f"%{q}%", f"%{q}%", f"%{q}%")
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message(
            f"No characters found matching **{query}**.", ephemeral=True
        )
        return

    status_icon = {"approved": "✓", "pending": "…"}
    lines = [
        f"{status_icon.get(r[3], '?')}  **{r[0].title()}** ({r[1]}) — {r[2]}"
        for r in rows
    ]
    embed = discord.Embed(
        title=f"Results for \"{query}\"",
        description="\n".join(lines)[:4096],
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"{len(rows)} result{'s' if len(rows) != 1 else ''} found")
    await interaction.response.send_message(embed=embed)


# ─── /character ───────────────────────────────────────────────────────────────

@tree.command(name="character", description="Look up a character's full profile.")
@app_commands.describe(name="The character's name")
async def character(interaction: discord.Interaction, name: str):
    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND name=?",
            (guild_id, name.lower())
        )
        char = await cur.fetchone()

    if not char:
        await interaction.response.send_message("Character not found.", ephemeral=True)
        return

    owner = interaction.guild.get_member(char[7])
    if char[9] == "approved":
        embed = approved_embed(char[2], char[3], char[4], char[6], char[5], owner)
    else:
        embed = pending_embed(char[2], char[3], char[4], char[6], char[5], owner)

    await interaction.response.send_message(embed=embed)


# ─── /roster ──────────────────────────────────────────────────────────────────

@tree.command(name="roster", description="View all approved characters in this server.")
async def roster(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT name, alias, owner_name FROM characters WHERE guild_id=? AND status='approved' ORDER BY name",
            (guild_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("No approved characters yet.", ephemeral=True)
        return

    lines = [f"**{r[0].title()}** · {r[1]}  —  {r[2]}" for r in rows]

    # Chunk into pages if needed
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > 4000:
            chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)

    embed = discord.Embed(
        title="Character Roster",
        description=chunks[0],
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"{len(rows)} character{'s' if len(rows) != 1 else ''} approved")
    await interaction.response.send_message(embed=embed)

    for chunk in chunks[1:]:
        e = discord.Embed(description=chunk, colour=discord.Colour.blurple())
        await interaction.followup.send(embed=e)


# ─── AUTO-DROP ON MEMBER LEAVE ────────────────────────────────────────────────

@bot.event
async def on_member_remove(member: discord.Member):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND owner_id=?",
            (member.guild.id, member.id)
        )
        chars = await cur.fetchall()

    for char in chars:
        await _remove_character(member.guild, char)


# ─── STARTUP ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await init_db()
    for guild in bot.guilds:
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        print(f"Synced to: {guild.name}")
    print(f"Online — {bot.user}")


bot.run(os.getenv("DISCORD_TOKEN"))
