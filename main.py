import discord
from discord import app_commands
import aiosqlite
import os

# ─── CONFIG ───────────────────────────────────────────────────────────────────
CLAIM_LIMIT         = 4

APPROVAL_CHANNEL_ID = 1492095997675962428   # approved characters shown here
PENDING_CHANNEL_ID  = 1492096016470773810   # submissions posted here; /claim only works here
STAFF_ROLE_ID       = 1491359486370385930   # only this role can approve/deny
# ──────────────────────────────────────────────────────────────────────────────

DB = "characters.db"

intents = discord.Intents.default()
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


def build_embed(char_name: str, alias: str, wiki: str, description: str,
                image: str, owner: discord.Member, status: str) -> discord.Embed:
    colour = discord.Colour.gold() if status == "pending" else discord.Colour.green()
    embed  = discord.Embed(
        title=f"{char_name.title()}  ·  {alias}",
        description=description,
        colour=colour
    )
    embed.add_field(name="Owner", value=owner.mention if owner else "Unknown", inline=True)
    embed.add_field(name="Wiki",  value=wiki,                                  inline=True)
    if image:
        embed.set_image(url=image)
    embed.set_footer(text=f"Status: {status.upper()}")
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
    """Clean up embeds and delete from DB."""
    # columns: 0=id 1=guild_id 2=name 3=alias 4=wiki 5=image
    #          6=description 7=owner_id 8=owner_name 9=status
    #          10=approved_msg_id 11=pending_msg_id
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
    # Must be used in the submissions channel only
    if interaction.channel_id != PENDING_CHANNEL_ID:
        await interaction.response.send_message(
            f"❌ Claims can only be submitted in <#{PENDING_CHANNEL_ID}>.",
            ephemeral=True
        )
        return

    guild_id = interaction.guild.id

    # Duplicate check (name OR alias, any status)
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT 1 FROM characters WHERE guild_id=? AND (name=? OR alias=?)",
            (guild_id, name.lower(), alias.lower())
        )
        if await cur.fetchone():
            await interaction.response.send_message(
                "❌ A character with that name or alias is already claimed or pending approval.",
                ephemeral=True
            )
            return

    # Approved claim cap
    if await claim_count(guild_id, interaction.user.id) >= CLAIM_LIMIT:
        await interaction.response.send_message(
            f"❌ You already have {CLAIM_LIMIT} approved characters. Drop one first.",
            ephemeral=True
        )
        return

    img_url = image.url if image else ""

    # Insert as pending
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO characters
               (guild_id, name, alias, wiki, image, description, owner_id, owner_name, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (guild_id, name.lower(), alias, wiki, img_url,
             description, interaction.user.id, interaction.user.name)
        )
        await db.commit()

    # Post submission embed with Approve / Deny buttons
    embed = build_embed(name, alias, wiki, description, img_url, interaction.user, "pending")
    view  = ApprovalView(name.lower(), guild_id)
    msg   = await interaction.channel.send(
        content=f"📋 {interaction.user.mention} has submitted **{name.title()}** — awaiting staff review.",
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
        f"✅ **{name.title()}** submitted! Staff will review your claim shortly.",
        ephemeral=True
    )


# ─── APPROVAL VIEW ────────────────────────────────────────────────────────────

class ApprovalView(discord.ui.View):
    def __init__(self, char_name: str, guild_id: int):
        super().__init__(timeout=None)
        self.char_name = char_name
        self.guild_id  = guild_id

    @discord.ui.button(label="Approve ✅", style=discord.ButtonStyle.success, custom_id="btn_approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only staff can approve claims.", ephemeral=True)
            return
        await self._approve(interaction)

    @discord.ui.button(label="Deny ❌", style=discord.ButtonStyle.danger, custom_id="btn_deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only staff can deny claims.", ephemeral=True)
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

        # Post to approved channel
        approved_channel = interaction.guild.get_channel(APPROVAL_CHANNEL_ID)
        approved_msg_id  = None
        if approved_channel:
            embed = build_embed(char[2], char[3], char[4], char[6], char[5], owner, "approved")
            msg   = await approved_channel.send(embed=embed)
            approved_msg_id = msg.id

        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "UPDATE characters SET status='approved', approved_msg_id=? WHERE guild_id=? AND name=?",
                (approved_msg_id, self.guild_id, self.char_name)
            )
            await db.commit()

        # Delete the pending embed
try:
    channel = interaction.guild.get_channel(PENDING_CHANNEL_ID)
    msg = await channel.fetch_message(char[11])

    embed = msg.embeds[0]
    embed.colour = discord.Colour.green()
    embed.set_footer(text="APPROVED")

    await msg.edit(content="📌 CLAIM APPROVED", embed=embed, view=None)
except:
    pass
        # DM owner
        if owner:
            try:
                await owner.send(
                    f"✅ Your claim for **{char[2].title()}** on **{interaction.guild.name}** has been approved!"
                )
            except discord.Forbidden:
                pass

        await interaction.response.send_message(
            f"✅ **{char[2].title()}** approved and posted to <#{APPROVAL_CHANNEL_ID}>.",
            ephemeral=True
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

        owner = interaction.guild.get_member(char[7])
        if owner:
            try:
                await owner.send(
                    f"❌ Your claim for **{char[2].title()}** on **{interaction.guild.name}** was denied by staff."
                )
            except discord.Forbidden:
                pass

try:
    channel = interaction.guild.get_channel(PENDING_CHANNEL_ID)
    msg = await channel.fetch_message(char[11])

    embed = msg.embeds[0]
    embed.colour = discord.Colour.red()
    embed.set_footer(text="DENIED")

    await msg.edit(content="📌 CLAIM DENIED", embed=embed, view=None)
except:
    pass

        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "DELETE FROM characters WHERE guild_id=? AND name=?",
                (self.guild_id, self.char_name)
            )
            await db.commit()

        await interaction.response.send_message(
            f"❌ **{char[2].title()}** denied and removed.", ephemeral=True
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
    await interaction.response.send_message(f"🗑️ **{name.title()}** has been dropped.")


# ─── /remove_character (staff) ────────────────────────────────────────────────

@tree.command(name="remove_character", description="[Staff] Force-remove any character.")
@app_commands.describe(name="The character's name")
async def remove_character(interaction: discord.Interaction, name: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
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
    await interaction.response.send_message(f"🗑️ **{name.title()}** removed by staff.")


# ─── /transfer ────────────────────────────────────────────────────────────────

@tree.command(name="transfer", description="Transfer one of your characters to another user.")
@app_commands.describe(
    name="The character's name",
    user="The user to transfer to"
)
async def transfer(interaction: discord.Interaction, name: str, user: discord.Member):
    guild_id = interaction.guild.id

    if user.bot:
        await interaction.response.send_message("❌ Cannot transfer to a bot.", ephemeral=True)
        return

    if user.id == interaction.user.id:
        await interaction.response.send_message("❌ You can't transfer to yourself.", ephemeral=True)
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
            f"❌ {user.display_name} already has {CLAIM_LIMIT} approved characters.",
            ephemeral=True
        )
        return

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE characters SET owner_id=?, owner_name=? WHERE guild_id=? AND name=?",
            (user.id, user.name, guild_id, name.lower())
        )
        await db.commit()

    # Update the approved embed in-place if it exists
    if char[10]:
        try:
            approved_channel = interaction.guild.get_channel(APPROVAL_CHANNEL_ID)
            if approved_channel:
                msg   = await approved_channel.fetch_message(char[10])
                embed = build_embed(char[2], char[3], char[4], char[6], char[5], user, "approved")
                await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    await interaction.response.send_message(
        f"✅ **{name.title()}** transferred from {interaction.user.mention} to {user.mention}."
    )


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

    lines = [
        f"**{r[0].title()}** ({r[1]}) — {r[2]}  `[{r[3].upper()}]`"
        for r in rows
    ]
    embed = discord.Embed(
        title=f"Search: \"{query}\"",
        description="\n".join(lines)[:4096],
        colour=discord.Colour.blurple()
    )
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
    embed = build_embed(char[2], char[3], char[4], char[6], char[5], owner, char[9])
    await interaction.response.send_message(embed=embed)


# ─── /roster ──────────────────────────────────────────────────────────────────

@tree.command(name="roster", description="View all approved characters in this server.")
async def roster(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT name, alias, owner_name FROM characters WHERE guild_id=? AND status='approved'",
            (guild_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("No approved characters yet.")
        return

    lines = [f"**{r[0].title()}** ({r[1]}) — {r[2]}" for r in rows]
    await interaction.response.send_message("\n".join(lines)[:2000])


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
    await tree.sync()
    print(f"Logged in as {bot.user}")


bot.run(os.getenv("TOKEN"))
