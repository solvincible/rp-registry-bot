import os
import discord
from discord import app_commands
import aiosqlite
intents = discord.Intents.default()
intents.message_content = True
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
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
DB = "characters.db"
MAX_CLAIMS = 4
STAFF_ROLE_ID = 1491359486370385930
# Channels — hardcoded IDs provided by server owner
SUBMISSIONS_CHANNEL_ID = 1492096016470773810
APPROVED_CHANNEL_ID    = 1492095997675962428
ALLOWED_CHANNEL_IDS    = {SUBMISSIONS_CHANNEL_ID, APPROVED_CHANNEL_ID}
def parse_channel_id(value):
    if not value or value == "0":
        return 0
    if value.startswith("http"):
        return int(value.rstrip("/").split("/")[-1])
    return int(value)
STAFF_CHANNEL_ID = parse_channel_id(os.environ.get("STAFF_CHANNEL_ID", "0"))
# -------------------------
# DATABASE SETUP
# -------------------------
bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
# ─── DATABASE ─────────────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                name             TEXT PRIMARY KEY,
                alias            TEXT,
                wiki             TEXT,
                image            TEXT,
                description      TEXT,
                owner_id         INTEGER,
                owner_name       TEXT,
                approved_msg_id  INTEGER
            )
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
        # Migrate existing DB: add approved_msg_id column if missing
        try:
            await db.execute("ALTER TABLE characters ADD COLUMN approved_msg_id INTEGER")
        except Exception:
            pass
        await db.commit()
async def get_character(name: str):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT * FROM characters WHERE name = ?", (name.lower(),)
        )
        return await cursor.fetchone()
async def user_claim_count(user_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM characters WHERE owner_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
async def delete_approved_message(name_key: str):
    """Delete the character's post from the approved channel if it exists."""
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT approved_msg_id FROM characters WHERE name = ?", (name_key,)
        )
        row = await cursor.fetchone()
    if row and row[0]:
        approved_channel = client.get_channel(APPROVED_CHANNEL_ID)
        if approved_channel:
            try:
                msg = await approved_channel.fetch_message(row[0])
                await msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
# -------------------------
# CHANNEL RESTRICTION CHECK
# -------------------------
def allowed_channel_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.channel_id not in ALLOWED_CHANNEL_IDS:
            await interaction.response.send_message(
                "This command can only be used in the designated channels.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)
# -------------------------
# ON READY
# -------------------------
@client.event
async def on_ready():
    await init_db()
    for guild in client.guilds:
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        print(f"Synced to: {guild.name}")
    print(f"Logged in as {client.user}")
# -------------------------
# PING (test)
# -------------------------
@tree.command(name="ping", description="Check if the bot is alive")
@allowed_channel_only()
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Pong! {round(client.latency * 1000)}ms", ephemeral=True
    )
# -------------------------
# SUBMIT (inline → staff approval)
# -------------------------
@tree.command(name="submit", description="Submit a character for staff approval")
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
    name="Character name (e.g. Wanda Maximoff)",
    alias="Alias or hero name (e.g. Scarlet Witch)",
    wiki_link="Link to the character's wiki page",
    description="Brief description of the character",
    image="Attach an image file",
    image_url="Or paste an image URL instead of attaching",
    name="Character's full name",
    alias="Alias / codename",
    wiki="Link to character wiki page",
    description="Short character description",
    image="Optional character image"
)
@app_commands.rename(wiki_link="wiki-link", image_url="image-url")
@allowed_channel_only()
async def submit(
    interaction: discord.Interaction,
    name: str,
    alias: str,
    wiki_link: str,
    description: str,
    image: discord.Attachment = None,
    image_url: str = None,
):
    existing = await get_character(name)
    if existing:
        await interaction.response.send_message(
            f"**{name}** is already in the registry.", ephemeral=True
        )
        return
    img = image.url if image else (image_url or "")
    submission = {
        "user_id": interaction.user.id,
        "user_name": str(interaction.user),
        "name": name.strip(),
        "alias": alias.strip(),
        "wiki": wiki_link.strip(),
        "image": img,
        "description": description.strip(),
    }
    staff_channel = client.get_channel(STAFF_CHANNEL_ID)
    if not staff_channel:
        await interaction.response.send_message(
            "Staff channel not found. Contact an admin.", ephemeral=True
        )
        return
    embed = discord.Embed(title=f"New Submission: {submission['name']}", color=discord.Color.orange())
    embed.add_field(name="Alias", value=submission["alias"], inline=True)
    embed.add_field(name="Submitted by", value=interaction.user.mention, inline=True)
    embed.add_field(name="Wiki", value=submission["wiki"], inline=False)
    embed.add_field(name="Description", value=submission["description"], inline=False)
    if submission["image"]:
        embed.set_image(url=submission["image"])
    await staff_channel.send(embed=embed, view=ApprovalView(submission))
    # Public confirmation so everyone can see proof of submission
    await interaction.response.send_message(
        f"{interaction.user.mention} has submitted **{submission['name']}** ({submission['alias']}) for staff review."
    )
# -------------------------
# APPROVAL BUTTONS
# -------------------------
class ApprovalView(discord.ui.View):
    def __init__(self, submission: dict):
        super().__init__(timeout=None)
        self.submission = submission
    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        name_key = self.submission["name"].lower()
        user_id = self.submission["user_id"]
        count = await user_claim_count(user_id)
        if count >= MAX_CLAIMS:
            await interaction.response.send_message(
                f"Cannot approve — {self.submission['user_name']} already has {MAX_CLAIMS} active claims.",
                ephemeral=True
            )
            return
        approved_channel = client.get_channel(APPROVED_CHANNEL_ID)
        approved_msg_id = None
        if approved_channel:
            embed = discord.Embed(
                title=f"{self.submission['name']} ({self.submission['alias']})",
                description=self.submission["description"],
                color=discord.Color.green()
            )
            embed.add_field(name="Wiki", value=self.submission["wiki"], inline=False)
            embed.add_field(name="Claimed by", value=self.submission["user_name"], inline=False)
            if self.submission["image"]:
                embed.set_image(url=self.submission["image"])
            msg = await approved_channel.send(embed=embed)
            approved_msg_id = msg.id
        async with aiosqlite.connect(DB) as db:
 ...
[truncated]
[truncated]
[truncated]
-1
+1
[truncated]
[truncated]
