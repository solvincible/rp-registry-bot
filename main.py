import discord
from discord import app_commands
import aiosqlite
import os

# ─── CONFIG ───────────────────────────────────────────────────────────────────
# These are fallback defaults used before /setup has been run in a guild.
# After /setup is used, all values are stored per-guild in the database.
CLAIM_LIMIT         = 4
APPROVAL_CHANNEL_ID = 1492095997675962428
PENDING_CHANNEL_ID  = 1492096016470773810
STAFF_ROLE_ID       = 1491359486370385930

# Set DB_PATH env var on Railway to point at your mounted volume, e.g. /data/characters.db
DB = os.getenv("DB_PATH", "characters.db")

# Ensure the directory for the database file exists
_db_dir = os.path.dirname(DB)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
# ──────────────────────────────────────────────────────────────────────────────

# ─── GUILD CONFIG HELPERS ─────────────────────────────────────────────────────
# These replace all direct references to the global ID constants so that each
# guild can have its own channels and roles configured via /setup.

async def get_guild_config(guild_id: int) -> dict:
    """Return the stored config for a guild, falling back to global defaults."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT key, value FROM guild_config WHERE guild_id=?", (guild_id,)
        )
        rows = await cur.fetchall()
    cfg = {row[0]: int(row[1]) for row in rows if row[1]}
    return cfg


async def cfg(guild_id: int, key: str):
    """Return a single config value for a guild, or the hardcoded fallback."""
    fallbacks = {
        "approval_channel_id": APPROVAL_CHANNEL_ID,
        "pending_channel_id":  PENDING_CHANNEL_ID,
        "staff_role_id":       STAFF_ROLE_ID,
        "claim_limit":         CLAIM_LIMIT,
    }
    data = await get_guild_config(guild_id)
    return data.get(key, fallbacks.get(key))


async def set_guild_cfg(guild_id: int, key: str, value: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO guild_config (guild_id, key, value)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value""",
            (guild_id, key, value)
        )
        await db.commit()
# ──────────────────────────────────────────────────────────────────────────────

intents         = discord.Intents.default()
intents.members = True

bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# ─── DATABASE ─────────────────────────────────────────────────────────────────
# Column indexes:
#  0=id  1=guild_id  2=name  3=alias  4=wiki  5=image  6=description
#  7=owner_id  8=owner_name  9=status  10=approved_msg_id  11=pending_msg_id
#  12=fandom  13=team

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id  INTEGER NOT NULL,
            key       TEXT    NOT NULL,
            value     TEXT    NOT NULL,
            PRIMARY KEY (guild_id, key)
        )
        """)
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
            pending_msg_id  INTEGER DEFAULT NULL,
            fandom          TEXT    DEFAULT '',
            team            TEXT    DEFAULT ''
        )
        """)
        for col, default in [("fandom", "''"), ("team", "''")]:
            try:
                await db.execute(f"ALTER TABLE characters ADD COLUMN {col} TEXT DEFAULT {default}")
            except Exception:
                pass
        await db.commit()


# ─── HELPERS ──────────────────────────────────────────────────────────────────

async def is_staff(member: discord.Member) -> bool:
    staff_role_id = await cfg(member.guild.id, "staff_role_id")
    return any(r.id == staff_role_id for r in member.roles)


def char_fandom(char: tuple) -> str:
    return char[12] if len(char) > 12 else ""

def char_team(char: tuple) -> str:
    return char[13] if len(char) > 13 else ""


async def claim_count(guild_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM characters WHERE guild_id=? AND owner_id=? AND status IN ('approved','hiatus')",
            (guild_id, user_id)
        )
        return (await cur.fetchone())[0]


def _build_meta_tags(fandom: str, team: str) -> str:
    tags = []
    if fandom: tags.append(f"`{fandom}`")
    if team:   tags.append(f"`{team}`")
    return "  ".join(tags) if tags else ""


def pending_embed(char_name: str, alias: str, fandom: str, team: str, wiki: str,
                  description: str, image: str, owner: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title=f"{char_name.title()}  ·  {alias}",
        description=description,
        colour=discord.Colour.from_rgb(255, 180, 0),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Submitted by", value=owner.mention if owner else "Unknown", inline=True)
    embed.add_field(name="Fandom",       value=fandom or "—",                         inline=True)
    if team:
        embed.add_field(name="Team", value=team, inline=True)
    embed.add_field(name="Wiki", value=wiki, inline=True)
    if image:
        embed.set_thumbnail(url=image)
    embed.set_footer(text="Awaiting staff review")
    return embed


def approved_embed(char_name: str, alias: str, fandom: str, team: str, wiki: str,
                   description: str, image: str, owner: discord.Member,
                   hiatus: bool = False) -> discord.Embed:
    colour = discord.Colour.from_rgb(130, 130, 150) if hiatus else discord.Colour.from_rgb(87, 242, 135)
    embed  = discord.Embed(
        title=f"{char_name.title()}  ·  {alias}",
        description=description,
        colour=colour,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Claimed by", value=owner.mention if owner else "Unknown", inline=True)
    embed.add_field(name="Fandom",     value=fandom or "—",                         inline=True)
    if team:
        embed.add_field(name="Team", value=team, inline=True)
    embed.add_field(name="Wiki", value=wiki, inline=True)
    if image:
        embed.set_image(url=image)
    embed.set_footer(text="⏸  On Hiatus" if hiatus else "✓  Approved")
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
    approval_ch = await cfg(guild.id, "approval_channel_id")
    pending_ch  = await cfg(guild.id, "pending_channel_id")
    await delete_message_safe(guild, approval_ch, char[10])
    await delete_message_safe(guild, pending_ch,  char[11])
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM characters WHERE id=?", (char[0],))
        await db.commit()


# ─── EDIT CHARACTER MODAL ─────────────────────────────────────────────────────

class EditCharacterModal(discord.ui.Modal, title="Edit Character"):
    def __init__(self, char: tuple):
        super().__init__()
        self.char_id = char[0]

        self.desc_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            default=char[6] or "",
            required=False,
            max_length=1000
        )
        self.wiki_input = discord.ui.TextInput(
            label="Wiki URL",
            default=char[4] or "",
            required=False
        )
        self.fandom_input = discord.ui.TextInput(
            label="Fandom",
            default=char_fandom(char),
            required=False,
            max_length=100
        )
        self.team_input = discord.ui.TextInput(
            label="Team",
            default=char_team(char),
            required=False,
            max_length=100
        )
        self.image_input = discord.ui.TextInput(
            label="Image URL",
            default=char[5] or "",
            required=False
        )
        self.add_item(self.desc_input)
        self.add_item(self.wiki_input)
        self.add_item(self.fandom_input)
        self.add_item(self.team_input)
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction):
        desc   = self.desc_input.value.strip()
        wiki   = self.wiki_input.value.strip()
        fandom = self.fandom_input.value.strip()
        team   = self.team_input.value.strip()
        image  = self.image_input.value.strip()

        async with aiosqlite.connect(DB) as db:
            await db.execute(
                """UPDATE characters
                   SET description=?, wiki=?, fandom=?, team=?, image=?
                   WHERE id=?""",
                (desc, wiki, fandom, team, image, self.char_id)
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM characters WHERE id=?", (self.char_id,))
            char = await cur.fetchone()

        # Update the live embed in the approved channel if it exists
        if char and char[10]:
            try:
                owner   = interaction.guild.get_member(char[7])
                approval_ch_id = await cfg(interaction.guild.id, "approval_channel_id")
                channel = interaction.guild.get_channel(approval_ch_id)
                if channel:
                    msg   = await channel.fetch_message(char[10])
                    embed = approved_embed(
                        char[2], char[3], char_fandom(char), char_team(char),
                        char[4], char[6], char[5], owner,
                        hiatus=(char[9] == "hiatus")
                    )
                    await msg.edit(embed=embed)
            except Exception:
                pass

        await interaction.response.send_message(
            f"**{char[2].title()}** updated.", ephemeral=True
        )


# ─── DENIAL REASON MODAL ──────────────────────────────────────────────────────

class DenyReasonModal(discord.ui.Modal, title="Deny Claim"):
    reason = discord.ui.TextInput(
        label="Reason for denial",
        placeholder="Let the player know why their claim was denied...",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=False
    )

    def __init__(self, message_id: int):
        super().__init__()
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                "SELECT * FROM characters WHERE pending_msg_id=?",
                (self.message_id,)
            )
            char = await cur.fetchone()

        if not char:
            await interaction.response.send_message("Character not found.", ephemeral=True)
            return

        if char[9] != "pending":
            await interaction.response.send_message("This claim is no longer pending.", ephemeral=True)
            return

        owner       = interaction.guild.get_member(char[7])
        reason_text = str(self.reason.value).strip() if self.reason.value else None

        try:
            pending_ch_id = await cfg(interaction.guild.id, "pending_channel_id")
            channel = interaction.guild.get_channel(pending_ch_id)
            msg     = await channel.fetch_message(char[11])

            denial = discord.Embed(
                title="Claim Denied",
                description=f"**{char[2].title()}** · {char[3]}",
                colour=discord.Colour.from_rgb(237, 66, 69),
                timestamp=discord.utils.utcnow()
            )
            denial.add_field(name="Submitted by", value=f"<@{char[7]}>",         inline=True)
            denial.add_field(name="Denied by",    value=interaction.user.mention, inline=True)
            if reason_text:
                denial.add_field(name="Reason", value=reason_text, inline=False)
            denial.set_footer(text="✗  This claim has been closed.")

            await msg.edit(content="", embed=denial, view=None)
        except Exception:
            pass

        async with aiosqlite.connect(DB) as db:
            await db.execute("DELETE FROM characters WHERE id=?", (char[0],))
            await db.commit()

        if owner:
            try:
                dm = f"Your claim for **{char[2].title()}** on **{interaction.guild.name}** was denied by staff."
                if reason_text:
                    dm += f"\n\n**Reason:** {reason_text}"
                await owner.send(dm)
            except discord.Forbidden:
                pass

        await interaction.response.send_message(f"**{char[2].title()}** denied.", ephemeral=True)


# ─── APPROVAL VIEW ────────────────────────────────────────────────────────────

class ApprovalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="btn_approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await self._approve(interaction)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="btn_deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(DenyReasonModal(interaction.message.id))

    async def _approve(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                "SELECT * FROM characters WHERE pending_msg_id=?",
                (interaction.message.id,)
            )
            char = await cur.fetchone()

        if not char or char[9] != "pending":
            await interaction.response.send_message("This claim is no longer pending.", ephemeral=True)
            return

        owner = interaction.guild.get_member(char[7])

        approval_ch_id   = await cfg(interaction.guild.id, "approval_channel_id")
        approved_channel = interaction.guild.get_channel(approval_ch_id)
        approved_msg_id  = None
        if approved_channel:
            embed = approved_embed(
                char[2], char[3], char_fandom(char), char_team(char),
                char[4], char[6], char[5], owner
            )
            msg             = await approved_channel.send(embed=embed)
            approved_msg_id = msg.id

        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "UPDATE characters SET status='approved', approved_msg_id=? WHERE id=?",
                (approved_msg_id, char[0])
            )
            await db.commit()

        try:
            receipt = discord.Embed(
                title="Claim Approved",
                description=f"**{char[2].title()}** · {char[3]}",
                colour=discord.Colour.from_rgb(87, 242, 135),
                timestamp=discord.utils.utcnow()
            )
            receipt.add_field(name="Claimed by",  value=f"<@{char[7]}>",         inline=True)
            receipt.add_field(name="Approved by", value=interaction.user.mention, inline=True)
            if approved_msg_id:
                receipt.add_field(name="Posted to", value=f"<#{approval_ch_id}>", inline=False)
            receipt.set_footer(text="✓  This claim is closed.")
            await interaction.message.edit(content="", embed=receipt, view=None)
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
            f"**{char[2].title()}** approved and posted to <#{approval_ch_id}>.", ephemeral=True
        )


# ─── /claim ───────────────────────────────────────────────────────────────────

@tree.command(name="claim", description="Submit a character claim for staff approval.")
@app_commands.describe(
    name="Character's full name",
    alias="Alias / codename",
    fandom="The fandom this character is from",
    team="Team or group the character belongs to (optional)",
    wiki="Link to character wiki page",
    description="Short character description",
    image="Upload a character image",
    image_url="Or paste an image URL instead of uploading"
)
async def claim(
    interaction: discord.Interaction,
    name: str,
    alias: str,
    fandom: str,
    wiki: str,
    description: str,
    team: str = "",
    image: discord.Attachment = None,
    image_url: str = ""
):
    pending_ch_id = await cfg(interaction.guild.id, "pending_channel_id")
    if interaction.channel_id != pending_ch_id:
        await interaction.response.send_message(
            f"Claims can only be submitted in <#{pending_ch_id}>.", ephemeral=True
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

    claim_lim = await cfg(guild_id, "claim_limit")
    if await claim_count(guild_id, interaction.user.id) >= claim_lim:
        await interaction.response.send_message(
            f"You already have {claim_lim} approved characters. Drop one before claiming another.",
            ephemeral=True
        )
        return

    img = image_url.strip() or (image.url if image else "")

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO characters
               (guild_id, name, alias, wiki, image, description, owner_id, owner_name, status, fandom, team)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (guild_id, name.lower(), alias, wiki, img,
             description, interaction.user.id, interaction.user.name, fandom, team)
        )
        await db.commit()

    embed = pending_embed(name, alias, fandom, team, wiki, description, img, interaction.user)
    view  = ApprovalView()
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


# ─── /edit ────────────────────────────────────────────────────────────────────

@tree.command(name="edit", description="Edit one of your approved character's details.")
@app_commands.describe(name="The character's name")
async def edit(interaction: discord.Interaction, name: str):
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

    if char[7] != interaction.user.id and not await is_staff(interaction.user):
        await interaction.response.send_message("You don't own this character.", ephemeral=True)
        return

    if char[9] == "pending":
        await interaction.response.send_message(
            "You can't edit a pending claim. Wait for it to be reviewed first.", ephemeral=True
        )
        return

    await interaction.response.send_modal(EditCharacterModal(char))


# ─── /available ───────────────────────────────────────────────────────────────

@tree.command(name="available", description="Check if a character name or alias is free to claim.")
@app_commands.describe(name="The character name or alias to check")
async def available(interaction: discord.Interaction, name: str):
    guild_id = interaction.guild.id
    q        = name.lower().strip()

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT name, alias, owner_name, status FROM characters WHERE guild_id=? AND (name=? OR alias=?)",
            (guild_id, q, q)
        )
        char = await cur.fetchone()

    if not char:
        embed = discord.Embed(
            title="Available",
            description=f"**{name.title()}** is free — no one has claimed this name or alias.",
            colour=discord.Colour.from_rgb(87, 242, 135),
            timestamp=discord.utils.utcnow()
        )
    else:
        status_word = "pending approval" if char[3] == "pending" else "already claimed"
        embed = discord.Embed(
            title="Unavailable",
            description=f"**{name.title()}** is {status_word}.",
            colour=discord.Colour.from_rgb(237, 66, 69),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Owner",  value=char[2],  inline=True)
        embed.add_field(name="Status", value=char[3].title(), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── /whois ───────────────────────────────────────────────────────────────────

@tree.command(name="whois", description="View all characters owned by a specific user.")
@app_commands.describe(user="The user to look up")
async def whois(interaction: discord.Interaction, user: discord.Member):
    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT name, alias, fandom, team, status FROM characters WHERE guild_id=? AND owner_id=? ORDER BY name",
            (guild_id, user.id)
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message(
            f"{user.display_name} has no characters.", ephemeral=True
        )
        return

    approved = [r for r in rows if r[4] == "approved"]
    hiatus   = [r for r in rows if r[4] == "hiatus"]
    pending  = [r for r in rows if r[4] == "pending"]

    def fmt(r):
        tags = _build_meta_tags(r[2], r[3])
        return f"**{r[0].title()}** · {r[1]}" + (f"  {tags}" if tags else "")

    embed = discord.Embed(
        title=f"Characters — {user.display_name}",
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    if approved:
        embed.add_field(
            name=f"Approved ({len(approved)}/{await cfg(guild_id, 'claim_limit')})",
            value="\n".join(fmt(r) for r in approved),
            inline=False
        )
    if hiatus:
        embed.add_field(
            name="On Hiatus",
            value="\n".join(fmt(r) for r in hiatus),
            inline=False
        )
    if pending:
        embed.add_field(
            name="Pending Review",
            value="\n".join(fmt(r) for r in pending),
            inline=False
        )

    await interaction.response.send_message(embed=embed)


# ─── /fandom ──────────────────────────────────────────────────────────────────

@tree.command(name="fandom", description="List all approved characters from a specific fandom.")
@app_commands.describe(name="The fandom name to look up")
async def fandom_cmd(interaction: discord.Interaction, name: str):
    guild_id = interaction.guild.id
    q        = name.lower().strip()

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """SELECT name, alias, team, owner_name, status FROM characters
               WHERE guild_id=? AND fandom LIKE ? ORDER BY name""",
            (guild_id, f"%{q}%")
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message(
            f"No characters found for fandom **{name}**.", ephemeral=True
        )
        return

    status_icon = {"approved": "✓", "hiatus": "⏸", "pending": "…"}
    lines = []
    for r in rows:
        team_tag = f"  `{r[2]}`" if r[2] else ""
        lines.append(f"{status_icon.get(r[4], '?')}  **{r[0].title()}** · {r[1]}{team_tag} — {r[3]}")

    embed = discord.Embed(
        title=f"Fandom: {name.title()}",
        description="\n".join(lines)[:4096],
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"{len(rows)} character{'s' if len(rows) != 1 else ''}")
    await interaction.response.send_message(embed=embed)


# ─── /hiatus (staff) ──────────────────────────────────────────────────────────

@tree.command(name="hiatus", description="[Staff] Toggle hiatus status on a character.")
@app_commands.describe(name="The character's name")
async def hiatus(interaction: discord.Interaction, name: str):
    if not await is_staff(interaction.user):
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

    if char[9] == "pending":
        await interaction.response.send_message(
            "Can't put a pending character on hiatus.", ephemeral=True
        )
        return

    new_status = "approved" if char[9] == "hiatus" else "hiatus"

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE characters SET status=? WHERE id=?",
            (new_status, char[0])
        )
        await db.commit()

    # Update the approved channel embed
    if char[10]:
        try:
            owner          = interaction.guild.get_member(char[7])
            approval_ch_id = await cfg(guild_id, "approval_channel_id")
            channel = interaction.guild.get_channel(approval_ch_id)
            if channel:
                msg   = await channel.fetch_message(char[10])
                embed = approved_embed(
                    char[2], char[3], char_fandom(char), char_team(char),
                    char[4], char[6], char[5], owner,
                    hiatus=(new_status == "hiatus")
                )
                await msg.edit(embed=embed)
        except Exception:
            pass

    action = "put on hiatus" if new_status == "hiatus" else "returned from hiatus"
    await interaction.response.send_message(
        f"**{char[2].title()}** has been {action}.", ephemeral=True
    )


# ─── /forcetransfer (staff) ───────────────────────────────────────────────────

@tree.command(name="forcetransfer", description="[Staff] Transfer any character to another user.")
@app_commands.describe(name="The character's name", user="The user to transfer to")
async def forcetransfer(interaction: discord.Interaction, name: str, user: discord.Member):
    if not await is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    guild_id = interaction.guild.id

    if user.bot:
        await interaction.response.send_message("Cannot transfer to a bot.", ephemeral=True)
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

    claim_lim = await cfg(guild_id, "claim_limit")
    if await claim_count(guild_id, user.id) >= claim_lim:
        await interaction.response.send_message(
            f"{user.display_name} already has {claim_lim} approved characters.", ephemeral=True
        )
        return

    prev_owner = interaction.guild.get_member(char[7])

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE characters SET owner_id=?, owner_name=? WHERE id=?",
            (user.id, user.name, char[0])
        )
        await db.commit()

    if char[10]:
        try:
            approval_ch_id = await cfg(guild_id, "approval_channel_id")
            channel = interaction.guild.get_channel(approval_ch_id)
            if channel:
                msg   = await channel.fetch_message(char[10])
                embed = approved_embed(
                    char[2], char[3], char_fandom(char), char_team(char),
                    char[4], char[6], char[5], user
                )
                await msg.edit(embed=embed)
        except Exception:
            pass

    await interaction.response.send_message(
        f"**{char[2].title()}** force-transferred to {user.mention} "
        f"(previously {prev_owner.mention if prev_owner else char[8]})."
    )


# ─── /pending (staff) ─────────────────────────────────────────────────────────

@tree.command(name="pending", description="[Staff] View all currently pending claims.")
async def pending(interaction: discord.Interaction):
    if not await is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """SELECT name, alias, fandom, team, owner_name, pending_msg_id FROM characters
               WHERE guild_id=? AND status='pending' ORDER BY id""",
            (guild_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("No pending claims right now.", ephemeral=True)
        return

    lines = []
    for r in rows:
        tags = _build_meta_tags(r[2], r[3])
        pending_ch_id = await cfg(guild_id, "pending_channel_id")
        jump = f"https://discord.com/channels/{guild_id}/{pending_ch_id}/{r[5]}" if r[5] else ""
        line = f"**{r[0].title()}** · {r[1]}" + (f"  {tags}" if tags else "") + f" — {r[4]}"
        if jump:
            line += f"  [↗]({jump})"
        lines.append(line)

    embed = discord.Embed(
        title=f"Pending Claims  ({len(rows)})",
        description="\n".join(lines)[:4096],
        colour=discord.Colour.from_rgb(255, 180, 0),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Click ↗ to jump to a submission")
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    if not await is_staff(interaction.user):
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
    await interaction.response.send_message(f"**{name.title()}** removed.", ephemeral=True)


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

    claim_lim = await cfg(guild_id, "claim_limit")
    if await claim_count(guild_id, user.id) >= claim_lim:
        await interaction.response.send_message(
            f"{user.display_name} already has {claim_lim} approved characters.", ephemeral=True
        )
        return

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE characters SET owner_id=?, owner_name=? WHERE id=?",
            (user.id, user.name, char[0])
        )
        await db.commit()

    if char[10]:
        try:
            approval_ch_id = await cfg(guild_id, "approval_channel_id")
            channel = interaction.guild.get_channel(approval_ch_id)
            if channel:
                msg   = await channel.fetch_message(char[10])
                embed = approved_embed(
                    char[2], char[3], char_fandom(char), char_team(char),
                    char[4], char[6], char[5], user
                )
                await msg.edit(embed=embed)
        except Exception:
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
            "SELECT name, alias, fandom, team, status FROM characters WHERE guild_id=? AND owner_id=? ORDER BY name",
            (guild_id, interaction.user.id)
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("You have no active claims.", ephemeral=True)
        return

    def fmt(r):
        tags = _build_meta_tags(r[2], r[3])
        return f"**{r[0].title()}** · {r[1]}" + (f"  {tags}" if tags else "")

    approved = [r for r in rows if r[4] == "approved"]
    hiatus   = [r for r in rows if r[4] == "hiatus"]
    pending  = [r for r in rows if r[4] == "pending"]

    embed = discord.Embed(
        title=f"Claims — {interaction.user.display_name}",
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    if approved:
        embed.add_field(
            name=f"Approved ({len(approved)}/{await cfg(guild_id, 'claim_limit')})",
            value="\n".join(fmt(r) for r in approved),
            inline=False
        )
    if hiatus:
        embed.add_field(
            name="On Hiatus",
            value="\n".join(fmt(r) for r in hiatus),
            inline=False
        )
    if pending:
        embed.add_field(
            name="Pending Review",
            value="\n".join(fmt(r) for r in pending),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── /search ──────────────────────────────────────────────────────────────────

@tree.command(name="search", description="Search characters by name, alias, fandom, team, or owner.")
@app_commands.describe(query="Name, alias, fandom, team, or username to search for")
async def search(interaction: discord.Interaction, query: str):
    guild_id = interaction.guild.id
    q        = query.lower().strip().lstrip("@")

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """SELECT DISTINCT name, alias, fandom, team, owner_name, status FROM characters
               WHERE guild_id=?
               AND (name LIKE ? OR alias LIKE ? OR fandom LIKE ? OR team LIKE ? OR owner_name LIKE ?)""",
            (guild_id, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%")
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message(
            f"No characters found matching **{query}**.", ephemeral=True
        )
        return

    status_icon = {"approved": "✓", "hiatus": "⏸", "pending": "…"}
    lines = []
    for r in rows:
        tags = _build_meta_tags(r[2], r[3])
        lines.append(
            f"{status_icon.get(r[5], '?')}  **{r[0].title()}** ({r[1]})"
            + (f"  {tags}" if tags else "")
            + f" — {r[4]}"
        )

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
    if char[9] in ("approved", "hiatus"):
        embed = approved_embed(
            char[2], char[3], char_fandom(char), char_team(char),
            char[4], char[6], char[5], owner,
            hiatus=(char[9] == "hiatus")
        )
    else:
        embed = pending_embed(
            char[2], char[3], char_fandom(char), char_team(char),
            char[4], char[6], char[5], owner
        )

    await interaction.response.send_message(embed=embed)


# ─── /roster ──────────────────────────────────────────────────────────────────

@tree.command(name="roster", description="View all approved characters in this server.")
async def roster(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """SELECT name, alias, fandom, team, owner_name, status FROM characters
               WHERE guild_id=? AND status IN ('approved','hiatus') ORDER BY name""",
            (guild_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("No approved characters yet.", ephemeral=True)
        return

    status_icon = {"approved": "✓", "hiatus": "⏸"}
    lines = []
    for r in rows:
        tags = _build_meta_tags(r[2], r[3])
        lines.append(
            f"{status_icon.get(r[5], '✓')}  **{r[0].title()}** · {r[1]}"
            + (f"  {tags}" if tags else "")
            + f"  —  {r[4]}"
        )

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
    embed.set_footer(text=f"{len(rows)} character{'s' if len(rows) != 1 else ''}")
    await interaction.response.send_message(embed=embed)

    for chunk in chunks[1:]:
        e = discord.Embed(description=chunk, colour=discord.Colour.blurple())
        await interaction.followup.send(embed=e)


# ─── /stats ───────────────────────────────────────────────────────────────────

@tree.command(name="stats", description="View character statistics for this server.")
async def stats(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT status, COUNT(*) FROM characters WHERE guild_id=? GROUP BY status",
            (guild_id,)
        )
        status_counts = dict(await cur.fetchall())

        cur = await db.execute(
            """SELECT fandom, COUNT(*) as c FROM characters
               WHERE guild_id=? AND status='approved' AND fandom != ''
               GROUP BY fandom ORDER BY c DESC LIMIT 5""",
            (guild_id,)
        )
        top_fandoms = await cur.fetchall()

        cur = await db.execute(
            """SELECT owner_name, COUNT(*) as c FROM characters
               WHERE guild_id=? AND status='approved'
               GROUP BY owner_id ORDER BY c DESC LIMIT 5""",
            (guild_id,)
        )
        top_claimers = await cur.fetchall()

    approved = status_counts.get("approved", 0)
    pending  = status_counts.get("pending",  0)
    hiatus   = status_counts.get("hiatus",   0)

    embed = discord.Embed(
        title="Server Stats",
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(
        name="Characters",
        value=f"✓ Approved: **{approved}**\n⏸ Hiatus: **{hiatus}**\n… Pending: **{pending}**",
        inline=True
    )
    if top_fandoms:
        embed.add_field(
            name="Top Fandoms",
            value="\n".join(f"`{r[0]}` — {r[1]}" for r in top_fandoms),
            inline=True
        )
    if top_claimers:
        embed.add_field(
            name="Top Claimers",
            value="\n".join(f"{r[0]} — {r[1]}" for r in top_claimers),
            inline=True
        )

    await interaction.response.send_message(embed=embed)


# ─── /setup ───────────────────────────────────────────────────────────────────

@tree.command(name="setup", description="[Staff] Configure the bot's channels, roles, and claim limit for this server.")
@app_commands.describe(
    pending_channel  = "Channel where players submit claims (e.g. #character-claims)",
    approval_channel = "Channel where approved characters are posted (e.g. #approved-characters)",
    staff_role       = "Role that can approve/deny claims",
    claim_limit      = "Maximum number of approved characters per player (default 4)"
)
async def setup(
    interaction: discord.Interaction,
    pending_channel:  discord.TextChannel,
    approval_channel: discord.TextChannel,
    staff_role:       discord.Role,
    claim_limit:      app_commands.Range[int, 1, 20] = 4
):
    # Only members who already have the configured staff role (or Administrator) may run setup
    is_admin = interaction.user.guild_permissions.administrator
    already_staff = await is_staff(interaction.user)
    if not is_admin and not already_staff:
        await interaction.response.send_message(
            "You need the staff role or Administrator permission to run setup.",
            ephemeral=True
        )
        return

    guild_id = interaction.guild.id

    await set_guild_cfg(guild_id, "pending_channel_id",  pending_channel.id)
    await set_guild_cfg(guild_id, "approval_channel_id", approval_channel.id)
    await set_guild_cfg(guild_id, "staff_role_id",       staff_role.id)
    await set_guild_cfg(guild_id, "claim_limit",         claim_limit)

    embed = discord.Embed(
        title="Setup Complete",
        description="The bot has been configured for this server. You can re-run `/setup` at any time to change these settings.",
        colour=discord.Colour.from_rgb(87, 242, 135),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Submissions channel", value=pending_channel.mention,  inline=True)
    embed.add_field(name="Approved channel",    value=approval_channel.mention, inline=True)
    embed.add_field(name="Staff role",          value=staff_role.mention,       inline=True)
    embed.add_field(name="Claim limit",         value=str(claim_limit),         inline=True)
    embed.set_footer(text=f"Configured by {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)


@tree.command(name="config", description="[Staff] View the current bot configuration for this server.")
async def config_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator and not await is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    g_cfg    = await get_guild_config(guild_id)

    def mention_ch(key, fallback):
        val = g_cfg.get(key, fallback)
        return f"<#{val}>" if val else "Not set"

    def mention_role(key, fallback):
        val = g_cfg.get(key, fallback)
        return f"<@&{val}>" if val else "Not set"

    using_defaults = not g_cfg
    embed = discord.Embed(
        title="Bot Configuration",
        description=(
            "⚠️ **No setup has been run yet — using hardcoded defaults.**\nRun `/setup` to configure the bot for this server."
            if using_defaults else
            "Current configuration for this server. Run `/setup` to change any of these."
        ),
        colour=discord.Colour.from_rgb(255, 180, 0) if using_defaults else discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Submissions channel", value=mention_ch("pending_channel_id",   PENDING_CHANNEL_ID),  inline=True)
    embed.add_field(name="Approved channel",    value=mention_ch("approval_channel_id",  APPROVAL_CHANNEL_ID), inline=True)
    embed.add_field(name="Staff role",          value=mention_role("staff_role_id",      STAFF_ROLE_ID),       inline=True)
    embed.add_field(name="Claim limit",         value=str(g_cfg.get("claim_limit", CLAIM_LIMIT)),              inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── /help ────────────────────────────────────────────────────────────────────

@tree.command(name="help", description="View all available commands.")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Commands",
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(
        name="Player Commands",
        value=(
            "`/claim` — Submit a new character claim\n"
            "`/edit` — Edit your character's details\n"
            "`/drop` — Drop one of your characters\n"
            "`/transfer` — Transfer a character to another user\n"
            "`/myclaims` — View your characters\n"
            "`/character` — Look up a character's profile\n"
            "`/whois` — See all characters owned by a user\n"
            "`/available` — Check if a name/alias is free\n"
            "`/fandom` — List characters from a specific fandom\n"
            "`/search` — Search by name, alias, fandom, team, or owner\n"
            "`/roster` — View all approved characters\n"
            "`/stats` — Server character statistics"
        ),
        inline=False
    )
    embed.add_field(
        name="Staff Commands",
        value=(
            "`/setup` — Configure channels, roles, and claim limit\n"
            "`/config` — View the current bot configuration\n"
            "`/pending` — View all pending claims with jump links\n"
            "`/hiatus` — Toggle hiatus on a character\n"
            "`/forcetransfer` — Transfer any character without owner consent\n"
            "`/remove_character` — Force-remove any character"
        ),
        inline=False
    )
    embed.set_footer(text="Approve and Deny are buttons on each submission.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── AUTO-DROP ON MEMBER LEAVE ────────────────────────────────────────────────

@bot.event
async def on_member_remove(member: discord.Member):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND owner_id=?",
            (member.guild.id, member.id)
        )
        chars = await cur.fetchall()

    if not chars:
        return

    pending_ch_id   = await cfg(member.guild.id, "pending_channel_id")
    pending_channel = member.guild.get_channel(pending_ch_id)

    for char in chars:
        await _remove_character(member.guild, char)

        if pending_channel:
            notice = discord.Embed(
                title="Character Dropped",
                description=f"**{char[2].title()}** · {char[3]}",
                colour=discord.Colour.from_rgb(150, 150, 150),
                timestamp=discord.utils.utcnow()
            )
            notice.add_field(name="Previously owned by", value=char[8],               inline=True)
            notice.add_field(name="Reason",              value="User left the server", inline=True)
            notice.set_footer(text="This character is now available to claim.")
            await pending_channel.send(embed=notice)


# ─── STARTUP ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await init_db()
    bot.add_view(ApprovalView())

    for guild in bot.guilds:
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to: {guild.name} ({guild.id})")

    tree.clear_commands(guild=None)
    await tree.sync()

    print(f"Online — {bot.user} | In {len(bot.guilds)} guild(s)")


bot.run(os.getenv("DISCORD_TOKEN"))
