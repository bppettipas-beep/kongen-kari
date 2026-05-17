import asyncio
import io
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import json
import random
import re
import time as _time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


def _is_admin(user: discord.Member | discord.User) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


class RestrictedCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _is_admin(interaction.user):
            return True

        settings = load_guild_settings().get(str(interaction.guild_id), {})

        # ticket/close is public — anyone in the right channel can use it
        cmd = interaction.command
        is_ticket_close = (
            cmd is not None
            and cmd.name == "close"
            and getattr(cmd.parent, "name", None) == "ticket"
        )

        if not is_ticket_close:
            permitted = settings.get("permitted_roles", [])
            has_perm  = (
                isinstance(interaction.user, discord.Member)
                and any(r.id in permitted for r in interaction.user.roles)
            )
            if not has_perm:
                await interaction.response.send_message(
                    "You don't have permission to use bot commands. "
                    "A server admin can grant your role access with `/rolepermission`.",
                    ephemeral=True,
                )
                return False

        if not settings.get("enabled") or not settings.get("command_channels"):
            return True
        if interaction.channel_id in settings["command_channels"]:
            return True
        try:
            if _tkt_by_channel(str(interaction.channel_id)):
                return True
        except Exception:
            pass
        await interaction.response.send_message(
            "Commands are not allowed in this channel.", ephemeral=True
        )
        return False


bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=RestrictedCommandTree)

GUILD_IDS = [
    discord.Object(id=1488636168709996548),
    discord.Object(id=1466873878973386931),
]
GOLD     = 0xFFD700
MEDALS   = ["🥇", "🥈", "🥉"]
DATA_DIR    = os.getenv("DATA_DIR", ".")
LB_FILE     = os.path.join(DATA_DIR, "leaderboards.json")
CC_FILE     = os.path.join(DATA_DIR, "chat_counters.json")
GW_FILE     = os.path.join(DATA_DIR, "giveaways.json")
GS_FILE     = os.path.join(DATA_DIR, "guild_settings.json")
TICKET_DB   = os.path.join(DATA_DIR, "tickets.db")

COMMAND_RE = re.compile(r"^[^\w\s]\w", re.UNICODE)

GW_YELLOW = 0xFFD700

RESTORE_CODES = {
    "RESTORE-MONTHLY-AK7": {
        "title": "💬  Monthly Message Leaderboard",
        "reset_at_ts": 1780332566,
        "reset_interval": 30 * 86400,
        "seed": {
            "Kongen": 117, "Mod | Bryce": 106, "Dev | Goose": 98,
            "! 𝐊𝐚𝐫𝐢": 67, "Chat Mod | Sully": 60, "Sr Helper | Jynxy": 47,
            "𝓼𝓻𝓲 |": 34, "chat mod | Optimal": 33, "Sr Mod | minnk.": 31,
            "Mod | AlexGrundi": 29,
        },
    },
    "RESTORE-WEEKLY-BN4": {
        "title": "💬  Weekly Message Leaderboard",
        "reset_at_ts": 1778345280,
        "reset_interval": 7 * 86400,
        "seed": {
            "Kongen": 118, "Mod | Bryce": 106, "Dev | Goose": 98,
            "! 𝐊𝐚𝐫𝐢": 67, "Chat Mod | Sully": 60, "Sr Helper | Jynxy": 47,
            "𝓼𝓻𝓲 |": 34, "chat mod | Optimal": 33, "Sr Mod | minnk.": 31,
            "Mod | AlexGrundi": 29,
        },
    },
}
GW_GRAY   = 0x95A5A6

COLORS = {
    "gold":   0xFFD700, "yellow": 0xFFFF00, "amber":  0xFFC200,
    "orange": 0xFF8C00, "white":  0xFFFFFF, "red":    0xFF0000,
    "blue":   0x0055FF, "green":  0x00CC44, "purple": 0x8B00FF,
    "pink":   0xFF69B4, "teal":   0x00CED1, "dark":   0x36393F,
}

# ─── Persistence ──────────────────────────────────────────────────────────────

def load_leaderboards() -> dict:
    if os.path.exists(LB_FILE):
        with open(LB_FILE) as f:
            return json.load(f)
    return {}

def get_guild_leaderboards(guild_id: int) -> dict:
    return load_leaderboards().get(str(guild_id), {})

def save_leaderboard(guild_id: int, name: str, data: dict):
    all_data = load_leaderboards()
    gkey = str(guild_id)
    if gkey not in all_data:
        all_data[gkey] = {}
    all_data[gkey][name] = {k: v for k, v in data.items() if k not in ("author_id", "lb_name", "guild_id")}
    with open(LB_FILE, "w") as f:
        json.dump(all_data, f, indent=2)

def parse_sort_value(v: str) -> float:
    v = v.strip().replace(",", "").replace("_", "")
    suffixes = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}
    if v and v[-1].lower() in suffixes:
        try:
            return float(v[:-1]) * suffixes[v[-1].lower()]
        except ValueError:
            pass
    try:
        return float(v)
    except ValueError:
        return 0.0

def normalize_value(v: str) -> str:
    """Convert any numeric input to clean shorthand (1000000000 → 1B, 1b → 1B, 1500 → 1.5K).
    Non-numeric strings are returned unchanged."""
    test = v.strip().replace(",", "").replace("_", "")
    is_numeric = False
    if test and test[-1].lower() in ("k", "m", "b", "t"):
        try:
            float(test[:-1])
            is_numeric = True
        except ValueError:
            pass
    if not is_numeric:
        try:
            float(test)
            is_numeric = True
        except ValueError:
            pass
    if not is_numeric:
        return v
    num = parse_sort_value(v)
    if abs(num) >= 1e12:
        return f"{num / 1e12:g}T"
    if abs(num) >= 1e9:
        return f"{num / 1e9:g}B"
    if abs(num) >= 1e6:
        return f"{num / 1e6:g}M"
    if abs(num) >= 1e3:
        return f"{num / 1e3:g}K"
    return f"{num:g}"

def parse_duration(text: str) -> int | None:
    text = text.strip().lower()
    units = [
        (r'(\d+(?:\.\d+)?)\s*y(?:ear)?s?',       365 * 86400),
        (r'(\d+(?:\.\d+)?)\s*mo(?:nth)?s?',        30 * 86400),
        (r'(\d+(?:\.\d+)?)\s*w(?:eek)?s?',          7 * 86400),
        (r'(\d+(?:\.\d+)?)\s*d(?:ay)?s?',               86400),
        (r'(\d+(?:\.\d+)?)\s*h(?:our|r)?s?',             3600),
        (r'(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?)?s?',          60),
        (r'(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?)?s?',           1),
    ]
    total = 0
    found = False
    for pattern, multiplier in units:
        for m in re.finditer(pattern, text):
            total += float(m.group(1)) * multiplier
            found = True
    return int(total) if found else None

def format_duration(seconds: int) -> str:
    if seconds >= 365 * 86400:
        return f"{seconds // (365 * 86400)} year(s)"
    if seconds >= 30 * 86400:
        return f"{seconds // (30 * 86400)} month(s)"
    if seconds >= 7 * 86400:
        return f"{seconds // (7 * 86400)} week(s)"
    if seconds >= 86400:
        return f"{seconds // 86400} day(s)"
    if seconds >= 3600:
        return f"{seconds // 3600} hour(s)"
    return f"{seconds // 60} minute(s)"

def save_chat_counters():
    with open(CC_FILE, "w") as f:
        json.dump(chat_counters, f, indent=2)

def load_guild_settings() -> dict:
    if os.path.exists(GS_FILE):
        with open(GS_FILE) as f:
            return json.load(f)
    return {}

def save_guild_settings(data: dict):
    with open(GS_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ─── In-memory state ──────────────────────────────────────────────────────────

lb_sessions:           dict[str, dict] = {}
emb_sessions:          dict[str, dict] = {}
cc_sessions:           dict[str, dict] = {}
chat_counters:         dict[str, dict] = {}
currently_counting:    set[str]        = set()
giveaways:             dict[str, dict] = {}
ticket_panel_builders: dict[str, dict] = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LEADERBOARD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_lb_embed(s: dict) -> discord.Embed:
    title       = s.get("title", "Custom Leaderboard")
    description = s.get("description", "")
    entries     = s.get("entries", [])
    sort        = s.get("sort", "none")
    show_medals = s.get("show_medals", True)
    footer_text = s.get("footer_text", "Kongen & Kari's Hangout")
    value_label = s.get("value_label", "")

    se = entries.copy()
    if sort == "desc":
        se.sort(key=lambda x: parse_sort_value(x["value"]), reverse=True)
    elif sort == "asc":
        se.sort(key=lambda x: parse_sort_value(x["value"]))
    elif sort == "name_asc":
        se.sort(key=lambda x: x["name"].lower())

    embed = discord.Embed(title=f"🏆  {title}", color=GOLD)
    embed.timestamp = discord.utils.utcnow()

    if description:
        embed.description = f"*{description}*\n​"

    if not se:
        body = "*No entries yet.*"
        if description:
            embed.add_field(name="​", value=body, inline=False)
        else:
            embed.description = body
    else:
        lines = []
        for i, entry in enumerate(se):
            rank   = MEDALS[i] if (show_medals and i < 3) else f"`#{i+1}`"
            suffix = f"  *({value_label})*" if (value_label and i == 0) else ""
            lines.append(f"{rank}  **{entry['name']}**  —  {entry['value']}{suffix}")
        body = "\n".join(lines)
        if description:
            embed.add_field(name="​", value=body, inline=False)
        else:
            embed.description = body

    embed.set_footer(text=f"{footer_text}  •  {len(entries)} {'entry' if len(entries) == 1 else 'entries'}")
    return embed


async def _refresh_lb(interaction: discord.Interaction, sid: str):
    s = lb_sessions.get(sid)
    if not s:
        return
    try:
        await interaction.edit_original_response(
            embed=build_lb_embed(s),
            view=LeaderboardPanel(sid, s["author_id"]),
        )
    except (discord.NotFound, discord.HTTPException):
        pass


# ── Leaderboard Modals ────────────────────────────────────────────────────────

class CreateLBModal(discord.ui.Modal, title="Name Your Leaderboard"):
    lb_name = discord.ui.TextInput(label="Leaderboard Name", placeholder="e.g. Weekly Kill Count", max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.lb_name.value.strip()
        sid  = f"{interaction.user.id}_{interaction.id}"
        guild_lbs = get_guild_leaderboards(interaction.guild.id)
        s = guild_lbs[name].copy() if name in guild_lbs else {
            "title": name, "description": "", "entries": [],
            "sort": "none", "show_medals": True,
            "footer_text": "Kongen & Kari's Hangout", "value_label": "",
        }
        s["lb_name"]  = name
        s["guild_id"] = interaction.guild.id
        s["author_id"] = interaction.user.id
        lb_sessions[sid] = s
        await interaction.response.send_message(
            embed=build_lb_embed(s), view=LeaderboardPanel(sid, interaction.user.id), ephemeral=True,
        )


class LBTitleModal(discord.ui.Modal, title="Set Title"):
    lb_title = discord.ui.TextInput(label="Title", max_length=100, required=False)

    def __init__(self, sid: str, current: str = ""):
        super().__init__()
        self.sid = sid
        self.lb_title.default = current

    async def on_submit(self, interaction: discord.Interaction):
        s = lb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["title"] = self.lb_title.value
        await interaction.response.defer()
        await _refresh_lb(interaction, self.sid)


class LBDescModal(discord.ui.Modal, title="Set Description"):
    desc = discord.ui.TextInput(label="Description / Subtitle", style=discord.TextStyle.paragraph,
                                required=False, max_length=300)

    def __init__(self, sid: str, current: str = ""):
        super().__init__()
        self.sid = sid
        self.desc.default = current

    async def on_submit(self, interaction: discord.Interaction):
        s = lb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["description"] = self.desc.value
        await interaction.response.defer()
        await _refresh_lb(interaction, self.sid)


class LBAddModal(discord.ui.Modal, title="Add Entry"):
    name  = discord.ui.TextInput(label="Name",          placeholder="Player / team...", max_length=100)
    value = discord.ui.TextInput(label="Score / Value", placeholder="e.g. 1500",        max_length=100)

    def __init__(self, sid: str):
        super().__init__()
        self.sid = sid

    async def on_submit(self, interaction: discord.Interaction):
        s = lb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["entries"].append({"name": self.name.value, "value": normalize_value(self.value.value)})
        await interaction.response.defer()
        await _refresh_lb(interaction, self.sid)


class LBEditModal(discord.ui.Modal, title="Edit Entry"):
    entry_name = discord.ui.TextInput(label="Name of entry to edit", placeholder="Exact name...", max_length=100)
    new_name   = discord.ui.TextInput(label="New name  (blank = keep)", required=False, max_length=100)
    new_value  = discord.ui.TextInput(label="New value (blank = keep)", required=False, max_length=100)

    def __init__(self, sid: str):
        super().__init__()
        self.sid = sid

    async def on_submit(self, interaction: discord.Interaction):
        s = lb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        target = self.entry_name.value.strip().lower()
        match = next((e for e in s["entries"] if e["name"].lower() == target), None)
        if match is None:
            return await interaction.response.send_message(
                f"No entry named **{self.entry_name.value}** found.", ephemeral=True
            )
        if self.new_name.value:
            match["name"] = self.new_name.value
        if self.new_value.value:
            match["value"] = normalize_value(self.new_value.value)
        await interaction.response.defer()
        await _refresh_lb(interaction, self.sid)


class LBRemoveModal(discord.ui.Modal, title="Remove Entry"):
    entry_name = discord.ui.TextInput(label="Name of entry to remove", placeholder="Exact name...", max_length=100)

    def __init__(self, sid: str):
        super().__init__()
        self.sid = sid

    async def on_submit(self, interaction: discord.Interaction):
        s = lb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        target = self.entry_name.value.strip().lower()
        before = len(s["entries"])
        s["entries"] = [e for e in s["entries"] if e["name"].lower() != target]
        if len(s["entries"]) == before:
            return await interaction.response.send_message(
                f"No entry named **{self.entry_name.value}** found.", ephemeral=True
            )
        await interaction.response.defer()
        await _refresh_lb(interaction, self.sid)


class LBSettingsModal(discord.ui.Modal, title="Settings"):
    footer      = discord.ui.TextInput(label="Footer text",             max_length=100, required=False)
    value_label = discord.ui.TextInput(label="Value label (e.g. pts)",  max_length=50,  required=False,
                                       placeholder="Shown after first entry's value")
    medals_on   = discord.ui.TextInput(label="Show medals? (yes / no)", max_length=3)

    def __init__(self, sid: str, s: dict):
        super().__init__()
        self.sid = sid
        self.footer.default      = s.get("footer_text", "Kongen & Kari's Hangout")
        self.value_label.default = s.get("value_label", "")
        self.medals_on.default   = "yes" if s.get("show_medals", True) else "no"

    async def on_submit(self, interaction: discord.Interaction):
        s = lb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["footer_text"] = self.footer.value
        s["value_label"] = self.value_label.value
        s["show_medals"] = self.medals_on.value.strip().lower() in ("yes", "y", "true", "1")
        await interaction.response.defer()
        await _refresh_lb(interaction, self.sid)


# ── Leaderboard Panel ─────────────────────────────────────────────────────────

class LBSortSelect(discord.ui.Select):
    def __init__(self, sid: str):
        self.sid = sid
        super().__init__(
            placeholder="Sort entries...",
            options=[
                discord.SelectOption(label="No sorting",    value="none",     emoji="📋"),
                discord.SelectOption(label="Highest first", value="desc",     emoji="🔽"),
                discord.SelectOption(label="Lowest first",  value="asc",      emoji="🔼"),
                discord.SelectOption(label="Name A to Z",   value="name_asc", emoji="🔤"),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        s = lb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["sort"] = self.values[0]
        await interaction.response.defer()
        await _refresh_lb(interaction, self.sid)


class LeaderboardPanel(discord.ui.View):
    def __init__(self, sid: str, author_id: int):
        super().__init__(timeout=600)
        self.sid       = sid
        self.author_id = author_id
        self.add_item(LBSortSelect(sid))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This panel belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        lb_sessions.pop(self.sid, None)

    @discord.ui.button(label="Title",        style=discord.ButtonStyle.secondary, row=1)
    async def btn_title(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = lb_sessions.get(self.sid, {})
        await interaction.response.send_modal(LBTitleModal(self.sid, s.get("title", "")))

    @discord.ui.button(label="Description",  style=discord.ButtonStyle.secondary, row=1)
    async def btn_desc(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = lb_sessions.get(self.sid, {})
        await interaction.response.send_modal(LBDescModal(self.sid, s.get("description", "")))

    @discord.ui.button(label="Settings",     style=discord.ButtonStyle.secondary, row=1)
    async def btn_settings(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = lb_sessions.get(self.sid, {})
        await interaction.response.send_modal(LBSettingsModal(self.sid, s))

    @discord.ui.button(label="Add Entry",    style=discord.ButtonStyle.success, row=2)
    async def btn_add(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(LBAddModal(self.sid))

    @discord.ui.button(label="Edit Entry",   style=discord.ButtonStyle.secondary, row=2)
    async def btn_edit(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(LBEditModal(self.sid))

    @discord.ui.button(label="Remove Entry", style=discord.ButtonStyle.danger, row=2)
    async def btn_remove(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(LBRemoveModal(self.sid))

    @discord.ui.button(label="Clear All",    style=discord.ButtonStyle.danger, row=3)
    async def btn_clear(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = lb_sessions.get(self.sid)
        if s:
            s["entries"] = []
        await interaction.response.defer()
        await _refresh_lb(interaction, self.sid)

    @discord.ui.button(label="Post & Save",  style=discord.ButtonStyle.primary, row=3)
    async def btn_post(self, interaction: discord.Interaction, _: discord.ui.Button):
        s        = lb_sessions.get(self.sid, {})
        name     = s.get("lb_name", "Untitled")
        guild_id = s.get("guild_id", interaction.guild.id)
        try:
            await interaction.channel.send(embed=build_lb_embed(s))
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"Failed to post: {e}", ephemeral=True)
        save_leaderboard(guild_id, name, s)
        await interaction.response.send_message(
            f"Leaderboard **{name}** posted and saved. Edit it later with `/editleaderboard`.",
            ephemeral=True,
        )
        lb_sessions.pop(self.sid, None)
        for item in self.children:
            item.disabled = True
        self.stop()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CHAT COUNTER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_cc_embed(counter: dict, guild: discord.Guild) -> discord.Embed:
    title       = counter.get("title", "Chat Leaderboard")
    footer_text = counter.get("footer_text", "Kongen & Kari's Hangout")
    show_medals = counter.get("show_medals", True)
    top_n       = counter.get("top_n", 10)
    counts      = counter.get("counts", {})
    counting    = counter.get("counting_history", False)

    embed = discord.Embed(title=f"\U0001f4ac  {title}", color=GOLD)
    embed.timestamp = discord.utils.utcnow()

    sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

    if not sorted_counts:
        embed.description = "*No messages counted yet.*"
    else:
        lines = []
        for i, (uid, count) in enumerate(sorted_counts):
            try:
                member = guild.get_member(int(uid))
                dname  = member.display_name if member else f"User {uid}"
            except (ValueError, TypeError):
                dname = uid  # seeded entry stored as display name string
            rank = MEDALS[i] if (show_medals and i < 3) else f"`#{i+1}`"
            lines.append(f"{rank}  **{dname}**  —  {count:,} messages")
        embed.description = "\n".join(lines)

    if counting:
        status = "  •  Counting history, please wait..."
    else:
        reset_at = counter.get("reset_at")
        if reset_at:
            dt = datetime.fromisoformat(reset_at).replace(tzinfo=timezone.utc)
            status = f"  •  Resets <t:{int(dt.timestamp())}:R>"
        else:
            status = "  •  Updates every 30 s"
    embed.set_footer(text=f"{footer_text}{status}")
    return embed


async def update_counter_embed(name: str):
    counter = chat_counters.get(name)
    if not counter:
        return
    guild   = bot.get_guild(counter["guild_id"])
    channel = guild.get_channel(counter.get("channel_id", 0)) if guild else None
    if not guild or not channel:
        return
    try:
        msg = await channel.fetch_message(counter["message_id"])
        await msg.edit(embed=build_cc_embed(counter, guild))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def count_history(name: str, after_dt: datetime | None = None):
    if name in currently_counting:
        return
    currently_counting.add(name)
    counter = chat_counters.get(name)
    if not counter:
        currently_counting.discard(name)
        return
    guild = bot.get_guild(counter["guild_id"])
    if not guild:
        currently_counting.discard(name)
        return

    counter["counting_history"] = True
    total = 0

    for channel in guild.text_channels:
        try:
            async for msg in channel.history(limit=None, after=after_dt, oldest_first=True):
                if msg.author.bot:
                    continue
                uid = str(msg.author.id)
                counter["counts"][uid] = counter["counts"].get(uid, 0) + 1
                total += 1
                if total % 2000 == 0:
                    await update_counter_embed(name)
        except (discord.Forbidden, discord.HTTPException):
            continue

    counter["counting_history"] = False
    counter["last_counted_at"]  = datetime.now(timezone.utc).isoformat()
    save_chat_counters()
    await update_counter_embed(name)
    currently_counting.discard(name)


@tasks.loop(seconds=30)
async def update_all_counters():
    now = datetime.now(timezone.utc)
    for name, counter in list(chat_counters.items()):
        reset_at = counter.get("reset_at")
        interval = counter.get("reset_interval")
        if reset_at and interval:
            dt = datetime.fromisoformat(reset_at).replace(tzinfo=timezone.utc)
            if now >= dt:
                counter["counts"]   = {}
                counter["reset_at"] = (now + timedelta(seconds=interval)).isoformat()
                print(f"[RESET] Counter '{name}' reset. Next reset: {counter['reset_at']}")
        await update_counter_embed(name)
    save_chat_counters()
    await _update_presence()


async def _update_presence():
    guild = bot.get_guild(1466873878973386931)
    count = guild.member_count if guild else 0
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name=f"{count:,} members",
    ))


# ── Chat Counter Setup ────────────────────────────────────────────────────────

class CCTitleModal(discord.ui.Modal, title="Set Title"):
    cc_title = discord.ui.TextInput(label="Title", max_length=100)

    def __init__(self, sid: str, current: str = ""):
        super().__init__()
        self.sid = sid
        self.cc_title.default = current

    async def on_submit(self, interaction: discord.Interaction):
        s = cc_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["title"] = self.cc_title.value
        await interaction.response.defer()
        await _refresh_cc(interaction, self.sid)


class CCFooterModal(discord.ui.Modal, title="Set Footer"):
    footer = discord.ui.TextInput(label="Footer text", max_length=100)

    def __init__(self, sid: str, current: str = ""):
        super().__init__()
        self.sid = sid
        self.footer.default = current

    async def on_submit(self, interaction: discord.Interaction):
        s = cc_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["footer_text"] = self.footer.value
        await interaction.response.defer()
        await _refresh_cc(interaction, self.sid)


class CCTopNModal(discord.ui.Modal, title="Top N"):
    top_n = discord.ui.TextInput(label="How many users to show? (1-25)", placeholder="10", max_length=2)

    def __init__(self, sid: str, current: int = 10):
        super().__init__()
        self.sid = sid
        self.top_n.default = str(current)

    async def on_submit(self, interaction: discord.Interaction):
        s = cc_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        try:
            s["top_n"] = max(1, min(25, int(self.top_n.value)))
        except ValueError:
            return await interaction.response.send_message("Enter a number 1-25.", ephemeral=True)
        await interaction.response.defer()
        await _refresh_cc(interaction, self.sid)


class CCResetModal(discord.ui.Modal, title="Set Reset Period"):
    period = discord.ui.TextInput(
        label="Reset Period",
        placeholder="e.g. 1hr  30 days  2 months  1 year  (blank = never reset)",
        required=False,
        max_length=20,
    )

    def __init__(self, sid: str, current: str = ""):
        super().__init__()
        self.sid = sid
        self.period.default = current

    async def on_submit(self, interaction: discord.Interaction):
        s = cc_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        text = self.period.value.strip()
        if not text:
            s["reset_interval"] = None
            s["reset_display"]  = ""
        else:
            seconds = parse_duration(text)
            if seconds is None:
                return await interaction.response.send_message(
                    "Invalid format. Examples: `1hr`, `30 days`, `2 months`, `1 year`", ephemeral=True
                )
            s["reset_interval"] = seconds
            s["reset_display"]  = text
        await interaction.response.defer()
        await _refresh_cc(interaction, self.sid)


def build_cc_setup_embed(s: dict) -> discord.Embed:
    embed = discord.Embed(title=f"\U0001f4ac  {s.get('title', 'Chat Leaderboard')}", color=GOLD)
    embed.description = "*No messages counted yet. Configure below then click Start Counting.*"
    interval = s.get("reset_interval")
    reset_display = format_duration(interval) if interval else "None (never resets)"
    embed.add_field(
        name="Settings",
        value=(
            f"**Top N:** {s.get('top_n', 10)} users\n"
            f"**Medals:** {'Yes' if s.get('show_medals', True) else 'No'}\n"
            f"**Reset Every:** {reset_display}\n"
            "**Footer:** " + s.get('footer_text', "Kongen & Kari's Hangout")
        ),
        inline=False,
    )
    mode_note = "Only counts NEW messages (reset period active)" if interval else "Counts ALL past messages on start"
    embed.set_footer(text=f"{mode_note}  •  Updates every 30 s")
    return embed


async def _refresh_cc(interaction: discord.Interaction, sid: str):
    s = cc_sessions.get(sid)
    if not s:
        return
    try:
        await interaction.edit_original_response(
            embed=build_cc_setup_embed(s),
            view=CCSetupPanel(sid, s["author_id"]),
        )
    except (discord.NotFound, discord.HTTPException):
        pass


class CCSetupPanel(discord.ui.View):
    def __init__(self, sid: str, author_id: int):
        super().__init__(timeout=300)
        self.sid       = sid
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This panel belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        cc_sessions.pop(self.sid, None)

    @discord.ui.button(label="Title",          style=discord.ButtonStyle.secondary, row=0)
    async def btn_title(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = cc_sessions.get(self.sid, {})
        await interaction.response.send_modal(CCTitleModal(self.sid, s.get("title", "")))

    @discord.ui.button(label="Footer",         style=discord.ButtonStyle.secondary, row=0)
    async def btn_footer(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = cc_sessions.get(self.sid, {})
        await interaction.response.send_modal(CCFooterModal(self.sid, s.get("footer_text", "")))

    @discord.ui.button(label="Top N",          style=discord.ButtonStyle.secondary, row=0)
    async def btn_topn(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = cc_sessions.get(self.sid, {})
        await interaction.response.send_modal(CCTopNModal(self.sid, s.get("top_n", 10)))

    @discord.ui.button(label="Toggle Medals",  style=discord.ButtonStyle.secondary, row=0)
    async def btn_medals(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = cc_sessions.get(self.sid)
        if s:
            s["show_medals"] = not s.get("show_medals", True)
        await interaction.response.defer()
        await _refresh_cc(interaction, self.sid)

    @discord.ui.button(label="Reset Period",   style=discord.ButtonStyle.secondary, row=1)
    async def btn_reset(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = cc_sessions.get(self.sid, {})
        await interaction.response.send_modal(CCResetModal(self.sid, s.get("reset_display", "")))

    @discord.ui.button(label="Start Counting", style=discord.ButtonStyle.primary, row=1)
    async def btn_start(self, interaction: discord.Interaction, _: discord.ui.Button):
        s     = cc_sessions.get(self.sid, {})
        name  = s.get("cc_name", "Chat Counter")
        guild = interaction.guild

        has_reset = bool(s.get("reset_interval"))
        after_dt  = datetime.now(timezone.utc) if has_reset else None

        live_msg = await interaction.channel.send(
            embed=build_cc_embed({**s, "counts": {}, "counting_history": not has_reset}, guild)
        )

        counter = {
            "guild_id":         guild.id,
            "channel_id":       interaction.channel_id,
            "message_id":       live_msg.id,
            "title":            s.get("title", name),
            "footer_text":      s.get("footer_text", "Kongen & Kari's Hangout"),
            "show_medals":      s.get("show_medals", True),
            "top_n":            s.get("top_n", 10),
            "counts":           {},
            "counting_history": not has_reset,
            "last_counted_at":  None,
            "reset_interval":   s.get("reset_interval"),
            "reset_at": (
                (datetime.now(timezone.utc) + timedelta(seconds=s["reset_interval"])).isoformat()
                if has_reset else None
            ),
        }
        chat_counters[name] = counter
        save_chat_counters()

        confirm = (
            f"**{name}** started! Only new messages will be counted (reset period is active)."
            if has_reset else
            f"**{name}** started! Counting all past messages now, this may take a few minutes."
        )
        await interaction.response.send_message(confirm, ephemeral=True)
        cc_sessions.pop(self.sid, None)
        for item in self.children:
            item.disabled = True
        self.stop()
        asyncio.create_task(count_history(name, after_dt=after_dt))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  EMBED BUILDER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _blank_emb(author_id: int) -> dict:
    return {
        "title": "", "title_url": "", "description": "", "color": GOLD,
        "author_name": "", "author_icon": "", "footer_text": "", "footer_icon": "",
        "thumbnail_url": "", "image_url": "", "fields": [], "timestamp": False,
        "author_id": author_id,
    }


def build_custom_embed(s: dict, placeholder: bool = False) -> discord.Embed:
    embed = discord.Embed(color=s.get("color", GOLD))
    title = s.get("title", "")
    if title:
        embed.title = title
        if s.get("title_url"):
            embed.url = s["title_url"]
    desc = s.get("description", "")
    if desc:
        embed.description = desc
    elif placeholder and not title and not s.get("fields") and not s.get("author_name"):
        embed.description = "*Your embed will appear here.*"
    if s.get("author_name"):
        kw = {"name": s["author_name"]}
        if s.get("author_icon"):
            kw["icon_url"] = s["author_icon"]
        embed.set_author(**kw)
    if s.get("footer_text"):
        kw = {"text": s["footer_text"]}
        if s.get("footer_icon"):
            kw["icon_url"] = s["footer_icon"]
        embed.set_footer(**kw)
    if s.get("thumbnail_url"):
        embed.set_thumbnail(url=s["thumbnail_url"])
    if s.get("image_url"):
        embed.set_image(url=s["image_url"])
    for f in s.get("fields", []):
        embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
    if s.get("timestamp"):
        embed.timestamp = discord.utils.utcnow()
    return embed


def build_emb_panel_embed(s: dict) -> discord.Embed:
    fields = s.get("fields", [])
    panel  = discord.Embed(title="Embed Builder", color=s.get("color", GOLD))
    panel.add_field(
        name="Fields",
        value=(
            "\n".join(
                f"`{i+1}.` **{f['name']}** — {f['value'][:40]}{'...' if len(f['value']) > 40 else ''}"
                for i, f in enumerate(fields)
            ) if fields else "*No fields yet.*"
        ),
        inline=False,
    )
    panel.add_field(
        name="Settings",
        value=(
            f"**Color:** `{hex(s.get('color', GOLD))}`\n"
            f"**Title:** {s.get('title') or '*(none)*'}\n"
            f"**Author:** {s.get('author_name') or '*(none)*'}\n"
            f"**Timestamp:** {'On' if s.get('timestamp') else 'Off'}"
        ),
        inline=False,
    )
    panel.set_footer(text="Only visible to you  •  Expires in 10 min  •  Kongen & Kari's Hangout")
    return panel


async def _refresh_emb(interaction: discord.Interaction, sid: str):
    s = emb_sessions.get(sid)
    if not s:
        return
    try:
        await interaction.edit_original_response(
            embed=build_emb_panel_embed(s),
            view=EmbedPanel(sid, s["author_id"]),
        )
    except discord.NotFound:
        pass


# ── Embed Modals ──────────────────────────────────────────────────────────────

class EmbTitleModal(discord.ui.Modal, title="Set Title"):
    emb_title = discord.ui.TextInput(label="Title",     placeholder="Your title...", max_length=256, required=False)
    title_url = discord.ui.TextInput(label="Title URL", placeholder="https://... (optional, makes title a link)", max_length=500, required=False)

    def __init__(self, sid: str, s: dict):
        super().__init__()
        self.sid = sid
        self.emb_title.default = s.get("title", "")
        self.title_url.default = s.get("title_url", "")

    async def on_submit(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["title"] = self.emb_title.value
        s["title_url"] = self.title_url.value
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)


class EmbDescModal(discord.ui.Modal, title="Set Description"):
    desc = discord.ui.TextInput(label="Description",
                                placeholder="Supports **bold**, *italic*, emojis, and Discord markdown...",
                                style=discord.TextStyle.paragraph, required=False, max_length=4000)

    def __init__(self, sid: str, s: dict):
        super().__init__()
        self.sid = sid
        self.desc.default = s.get("description", "")

    async def on_submit(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["description"] = self.desc.value
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)


class EmbAuthorModal(discord.ui.Modal, title="Set Author"):
    author_name = discord.ui.TextInput(label="Author Name",     required=False, max_length=256)
    author_icon = discord.ui.TextInput(label="Author Icon URL", required=False, max_length=500, placeholder="https://...")

    def __init__(self, sid: str, s: dict):
        super().__init__()
        self.sid = sid
        self.author_name.default = s.get("author_name", "")
        self.author_icon.default = s.get("author_icon", "")

    async def on_submit(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["author_name"] = self.author_name.value
        s["author_icon"] = self.author_icon.value
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)


class EmbFooterModal(discord.ui.Modal, title="Set Footer"):
    footer_text = discord.ui.TextInput(label="Footer Text",     required=False, max_length=2048)
    footer_icon = discord.ui.TextInput(label="Footer Icon URL", required=False, max_length=500, placeholder="https://...")

    def __init__(self, sid: str, s: dict):
        super().__init__()
        self.sid = sid
        self.footer_text.default = s.get("footer_text", "")
        self.footer_icon.default = s.get("footer_icon", "")

    async def on_submit(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["footer_text"] = self.footer_text.value
        s["footer_icon"] = self.footer_icon.value
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)


class EmbImagesModal(discord.ui.Modal, title="Set Images"):
    thumbnail = discord.ui.TextInput(label="Thumbnail URL (top-right)", required=False, max_length=500, placeholder="https://...")
    image     = discord.ui.TextInput(label="Large Image URL (bottom)",  required=False, max_length=500, placeholder="https://...")

    def __init__(self, sid: str, s: dict):
        super().__init__()
        self.sid = sid
        self.thumbnail.default = s.get("thumbnail_url", "")
        self.image.default     = s.get("image_url", "")

    async def on_submit(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        s["thumbnail_url"] = self.thumbnail.value
        s["image_url"]     = self.image.value
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)


class EmbCustomColorModal(discord.ui.Modal, title="Custom Hex Color"):
    hex_color = discord.ui.TextInput(label="Hex Color Code", placeholder="e.g. FFD700  or  #FFD700", max_length=7)

    def __init__(self, sid: str):
        super().__init__()
        self.sid = sid

    async def on_submit(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        try:
            s["color"] = int(self.hex_color.value.lstrip("#"), 16)
            await interaction.response.defer()
            await _refresh_emb(interaction, self.sid)
        except ValueError:
            await interaction.response.send_message("Invalid hex — example: `FFD700`", ephemeral=True)


class EmbAddFieldModal(discord.ui.Modal, title="Add Field"):
    field_name   = discord.ui.TextInput(label="Field Name",         max_length=256)
    field_value  = discord.ui.TextInput(label="Field Value",         max_length=1024, style=discord.TextStyle.paragraph,
                                        placeholder="Supports emojis and **markdown**...")
    field_inline = discord.ui.TextInput(label="Inline? (yes / no)", default="no", max_length=3)

    def __init__(self, sid: str):
        super().__init__()
        self.sid = sid

    async def on_submit(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        if len(s["fields"]) >= 25:
            return await interaction.response.send_message("Max 25 fields reached.", ephemeral=True)
        s["fields"].append({
            "name":   self.field_name.value,
            "value":  self.field_value.value,
            "inline": self.field_inline.value.strip().lower() in ("yes", "y", "true", "1"),
        })
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)


class EmbRemoveFieldModal(discord.ui.Modal, title="Remove Field"):
    index = discord.ui.TextInput(label="Field # to remove", placeholder="e.g. 2", max_length=3)

    def __init__(self, sid: str):
        super().__init__()
        self.sid = sid

    async def on_submit(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        try:
            i = int(self.index.value) - 1
            if not (0 <= i < len(s["fields"])):
                raise IndexError
        except (ValueError, IndexError):
            return await interaction.response.send_message("Invalid field number.", ephemeral=True)
        s["fields"].pop(i)
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)


class ColorSelect(discord.ui.Select):
    def __init__(self, sid: str):
        self.sid = sid
        super().__init__(
            placeholder="Pick a color...",
            options=[
                discord.SelectOption(label="Gold",        value="gold",   emoji="🟡", description="#FFD700 — Server theme"),
                discord.SelectOption(label="Yellow",      value="yellow", emoji="💛", description="#FFFF00"),
                discord.SelectOption(label="Amber",       value="amber",  emoji="🟠", description="#FFC200"),
                discord.SelectOption(label="Orange",      value="orange", emoji="🔶", description="#FF8C00"),
                discord.SelectOption(label="White",       value="white",  emoji="⬜", description="#FFFFFF"),
                discord.SelectOption(label="Red",         value="red",    emoji="🔴", description="#FF0000"),
                discord.SelectOption(label="Blue",        value="blue",   emoji="🔵", description="#0055FF"),
                discord.SelectOption(label="Green",       value="green",  emoji="🟢", description="#00CC44"),
                discord.SelectOption(label="Purple",      value="purple", emoji="🟣", description="#8B00FF"),
                discord.SelectOption(label="Pink",        value="pink",   emoji="🩷", description="#FF69B4"),
                discord.SelectOption(label="Teal",        value="teal",   emoji="🩵", description="#00CED1"),
                discord.SelectOption(label="Custom hex", value="custom", emoji="✏️", description="Enter any hex code"),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        s = emb_sessions.get(self.sid)
        if not s:
            return await interaction.response.send_message("Session expired.", ephemeral=True)
        if self.values[0] == "custom":
            return await interaction.response.send_modal(EmbCustomColorModal(self.sid))
        s["color"] = COLORS[self.values[0]]
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)


class EmbedPanel(discord.ui.View):
    def __init__(self, sid: str, author_id: int):
        super().__init__(timeout=600)
        self.sid       = sid
        self.author_id = author_id
        self.add_item(ColorSelect(sid))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This panel belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        emb_sessions.pop(self.sid, None)

    @discord.ui.button(label="Title",        style=discord.ButtonStyle.secondary, row=1)
    async def btn_title(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(EmbTitleModal(self.sid, emb_sessions.get(self.sid, {})))

    @discord.ui.button(label="Description",  style=discord.ButtonStyle.secondary, row=1)
    async def btn_desc(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(EmbDescModal(self.sid, emb_sessions.get(self.sid, {})))

    @discord.ui.button(label="Author",       style=discord.ButtonStyle.secondary, row=1)
    async def btn_author(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(EmbAuthorModal(self.sid, emb_sessions.get(self.sid, {})))

    @discord.ui.button(label="Footer",       style=discord.ButtonStyle.secondary, row=2)
    async def btn_footer(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(EmbFooterModal(self.sid, emb_sessions.get(self.sid, {})))

    @discord.ui.button(label="Images",       style=discord.ButtonStyle.secondary, row=2)
    async def btn_images(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(EmbImagesModal(self.sid, emb_sessions.get(self.sid, {})))

    @discord.ui.button(label="Timestamp",    style=discord.ButtonStyle.secondary, row=2)
    async def btn_timestamp(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = emb_sessions.get(self.sid)
        if s:
            s["timestamp"] = not s.get("timestamp", False)
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)

    @discord.ui.button(label="Add Field",    style=discord.ButtonStyle.success, row=3)
    async def btn_add_field(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(EmbAddFieldModal(self.sid))

    @discord.ui.button(label="Remove Field", style=discord.ButtonStyle.danger,  row=3)
    async def btn_rm_field(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(EmbRemoveFieldModal(self.sid))

    @discord.ui.button(label="Preview",      style=discord.ButtonStyle.primary, row=4)
    async def btn_preview(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            embed=build_custom_embed(emb_sessions.get(self.sid, {}), placeholder=True), ephemeral=True
        )

    @discord.ui.button(label="Clear All",    style=discord.ButtonStyle.danger,   row=4)
    async def btn_clear(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = emb_sessions.get(self.sid)
        if s:
            emb_sessions[self.sid] = _blank_emb(s["author_id"])
        await interaction.response.defer()
        await _refresh_emb(interaction, self.sid)

    @discord.ui.button(label="Post Embed",   style=discord.ButtonStyle.primary,  row=4)
    async def btn_post(self, interaction: discord.Interaction, _: discord.ui.Button):
        s     = emb_sessions.get(self.sid, {})
        embed = build_custom_embed(s)
        if not embed.title and not embed.description and not embed.fields and not embed.author.name:
            return await interaction.response.send_message(
                "Add at least a title or description before posting.", ephemeral=True
            )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Embed posted!", ephemeral=True)
        emb_sessions.pop(self.sid, None)
        for item in self.children:
            item.disabled = True
        self.stop()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GIVEAWAY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_giveaways() -> dict:
    if os.path.exists(GW_FILE):
        with open(GW_FILE) as f:
            return json.load(f)
    return {}

def save_giveaways():
    with open(GW_FILE, "w") as f:
        json.dump(giveaways, f, indent=2)


def build_giveaway_embed(g: dict, ended: bool = False, winners: list | None = None) -> discord.Embed:
    ts = int(g["end_time"])
    if ended:
        color = GW_YELLOW if winners else GW_GRAY
        title = "GIVEAWAY ENDED"
    else:
        color = GW_YELLOW
        title = "GIVEAWAY"

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Prize", value=f"```\n{g['prize']}\n```", inline=False)

    if g.get("description"):
        embed.add_field(name="Details", value=g["description"], inline=False)

    embed.add_field(name="​", value="​", inline=False)

    if ended:
        if winners:
            embed.add_field(
                name=f"Winner{'s' if len(winners) > 1 else ''}",
                value="\n".join(f"• <@{w}>" for w in winners),
                inline=True,
            )
        else:
            embed.add_field(name="No Winners", value="No entries were recorded.", inline=True)
        embed.add_field(name="Ended", value=f"<t:{ts}:R>", inline=True)
    else:
        embed.add_field(name="Time Left", value=f"<t:{ts}:R>", inline=True)
        embed.add_field(name="Ends At",   value=f"<t:{ts}:f>", inline=True)

    embed.add_field(name="​", value="​", inline=False)
    embed.add_field(name="Winners",   value=str(g["winners_count"]),   inline=True)
    embed.add_field(name="Entries",   value=f"**{len(g['entries'])}**", inline=True)
    embed.add_field(name="Hosted by", value=f"<@{g['host_id']}>",      inline=True)

    embed.set_footer(text="Click Enter below to join • Click again to leave" if not ended else "This giveaway has ended")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def _ended_view() -> discord.ui.View:
    v = discord.ui.View()
    v.add_item(discord.ui.Button(
        label="Giveaway Ended", style=discord.ButtonStyle.secondary,
        disabled=True, custom_id="gw_ended_placeholder",
    ))
    return v


class GiveawayView(discord.ui.View):
    def __init__(self, gw_id: str):
        super().__init__(timeout=None)
        self.gw_id = gw_id
        btn = discord.ui.Button(
            label="Enter Giveaway", style=discord.ButtonStyle.primary,
            custom_id=f"gw_enter_{gw_id}",
        )
        btn.callback = self._enter
        self.add_item(btn)

    async def _enter(self, interaction: discord.Interaction):
        g = giveaways.get(self.gw_id)
        if not g or g.get("ended"):
            return await interaction.response.send_message("This giveaway has already ended!", ephemeral=True)
        uid = interaction.user.id
        if uid in g["entries"]:
            g["entries"].remove(uid)
            reply = "You have **left** the giveaway."
        else:
            g["entries"].append(uid)
            reply = "You're **entered**! Good luck\n-# Click again to leave."
        save_giveaways()
        await interaction.response.send_message(reply, ephemeral=True)
        await _refresh_giveaway(interaction.client, self.gw_id)


async def _refresh_giveaway(client: discord.Client, gw_id: str):
    g = giveaways.get(gw_id)
    if not g:
        return
    channel = client.get_channel(g["channel_id"])
    if not channel:
        return
    try:
        msg = await channel.fetch_message(g["message_id"])
        view = GiveawayView(gw_id) if not g.get("ended") else _ended_view()
        await msg.edit(embed=build_giveaway_embed(g, ended=g.get("ended", False)), view=view)
    except Exception as exc:
        print(f"[giveaway] refresh error: {exc}")


async def _end_giveaway(client: discord.Client, gw_id: str):
    g = giveaways.get(gw_id)
    if not g or g.get("ended"):
        return
    entries = g["entries"]
    winners = random.sample(entries, min(g["winners_count"], len(entries))) if entries else []
    g["ended"] = True
    save_giveaways()

    channel = client.get_channel(g["channel_id"])
    if not channel:
        return

    try:
        msg = await channel.fetch_message(g["message_id"])
        await msg.edit(embed=build_giveaway_embed(g, ended=True, winners=winners), view=_ended_view())
    except Exception as exc:
        print(f"[giveaway] end-edit error: {exc}")

    if winners:
        mentions = " ".join(f"<@{w}>" for w in winners)
        announce = discord.Embed(
            title="We Have a Winner!",
            description=f"{mentions} {'has' if len(winners) == 1 else 'have'} won the giveaway!\n\n**Prize:** {g['prize']}",
            color=GW_YELLOW,
        )
        announce.add_field(name="Winner(s)", value="\n".join(f"• <@{w}>" for w in winners), inline=False)
        announce.add_field(name="Hosted by", value=f"<@{g['host_id']}>", inline=True)
        announce.set_footer(text="Congratulations!")
        announce.timestamp = datetime.now(timezone.utc)
        await channel.send(content=mentions, embed=announce)
    else:
        await channel.send(embed=discord.Embed(
            title="Giveaway Ended — No Winners",
            description=f"The **{g['prize']}** giveaway ended with no entries.",
            color=GW_GRAY,
        ))


@tasks.loop(seconds=5)
async def giveaway_checker():
    now = datetime.now(timezone.utc).timestamp()
    for gw_id, g in list(giveaways.items()):
        if not g.get("ended") and g["end_time"] <= now:
            await _end_giveaway(bot, gw_id)

@giveaway_checker.before_loop
async def before_giveaway_checker():
    await bot.wait_until_ready()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  COMMANDBLOCK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CommandBlockView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        self.guild = guild
        self.gid   = str(guild.id)
        self._populate()

    def _settings(self) -> dict:
        return load_guild_settings().get(self.gid, {"enabled": False, "command_channels": []})

    def build_embed(self) -> discord.Embed:
        s        = self._settings()
        enabled  = s.get("enabled", False)
        channels = s.get("command_channels", [])
        status   = "🟢 Active" if enabled else "🔴 Disabled"
        ch_list  = "\n".join(f"• <#{c}>" for c in channels) if channels else "*None — add channels below*"
        embed    = discord.Embed(title="🔒 Command Channel Restrictions", color=GOLD if enabled else GW_GRAY)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Allowed Channels", value=ch_list, inline=True)
        if enabled and not channels:
            embed.set_footer(text="⚠️ Active with no allowed channels — commands blocked everywhere!")
        elif enabled:
            embed.set_footer(text="Commands only work in allowed channels • Admins are always exempt")
        else:
            embed.set_footer(text="Restrictions off — commands work in all channels")
        return embed

    def _populate(self):
        s        = self._settings()
        enabled  = s.get("enabled", False)
        channels = s.get("command_channels", [])

        # ── Row 0: enable/disable toggle ─────────────────────
        btn = discord.ui.Button(
            label="Disable Restrictions" if enabled else "Enable Restrictions",
            style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
            row=0,
        )

        async def _toggle(interaction: discord.Interaction):
            data = load_guild_settings()
            gs   = data.setdefault(self.gid, {"enabled": False, "command_channels": []})
            gs["enabled"] = not gs.get("enabled", False)
            save_guild_settings(data)
            v = CommandBlockView(self.guild)
            await interaction.response.edit_message(embed=v.build_embed(), view=v)

        btn.callback = _toggle
        self.add_item(btn)

        # ── Row 1: add-channel select ─────────────────────────
        add_sel = discord.ui.ChannelSelect(
            placeholder="Add a channel to the allowlist…",
            channel_types=[discord.ChannelType.text],
            row=1,
        )

        async def _add(interaction: discord.Interaction):
            cid  = add_sel.values[0].id
            data = load_guild_settings()
            gs   = data.setdefault(self.gid, {"enabled": enabled, "command_channels": []})
            if cid not in gs["command_channels"]:
                gs["command_channels"].append(cid)
            save_guild_settings(data)
            v = CommandBlockView(self.guild)
            await interaction.response.edit_message(embed=v.build_embed(), view=v)

        add_sel.callback = _add
        self.add_item(add_sel)

        # ── Row 2: remove-channel select (only when list non-empty) ──
        if channels:
            opts = []
            for cid in channels:
                ch   = self.guild.get_channel(cid)
                name = f"#{ch.name}" if ch else f"Deleted channel ({cid})"
                opts.append(discord.SelectOption(label=name, value=str(cid)))
            rm_sel = discord.ui.Select(
                placeholder="Remove a channel from the allowlist…",
                options=opts,
                row=2,
            )

            async def _remove(interaction: discord.Interaction):
                cid  = int(rm_sel.values[0])
                data = load_guild_settings()
                gs   = data.get(self.gid, {})
                chs  = gs.get("command_channels", [])
                if cid in chs:
                    chs.remove(cid)
                    gs["command_channels"] = chs
                    data[self.gid] = gs
                    save_guild_settings(data)
                v = CommandBlockView(self.guild)
                await interaction.response.edit_message(embed=v.build_embed(), view=v)

            rm_sel.callback = _remove
            self.add_item(rm_sel)


@bot.tree.command(name="commandblock", description="Manage which channels commands are allowed in")
@app_commands.default_permissions(administrator=True)
async def commandblock_cmd(interaction: discord.Interaction):
    view = CommandBlockView(interaction.guild)
    await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)


@bot.tree.command(name="rolepermission", description="Grant or revoke a role's access to use bot commands (admin only)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(role="The role to grant or revoke bot command access")
async def rolepermission_cmd(interaction: discord.Interaction, role: discord.Role):
    data = load_guild_settings()
    gid  = str(interaction.guild_id)
    gs   = data.get(gid, {})
    permitted = gs.get("permitted_roles", [])
    if role.id in permitted:
        permitted.remove(role.id)
        desc   = f"{role.mention} has been **removed** from bot permissions."
        color  = 0xED4245
    else:
        permitted.append(role.id)
        desc   = f"{role.mention} has been **granted** bot command access."
        color  = 0x57F287
    gs["permitted_roles"] = permitted
    data[gid] = gs
    save_guild_settings(data)
    await interaction.response.send_message(
        embed=discord.Embed(color=color, description=desc), ephemeral=True
    )


@bot.check
async def _prefix_channel_check(ctx: commands.Context) -> bool:
    if not ctx.guild or _is_admin(ctx.author):
        return True
    gs = load_guild_settings().get(str(ctx.guild.id), {})
    if not gs.get("enabled") or not gs.get("command_channels"):
        return True
    return ctx.channel.id in gs["command_channels"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SLASH COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.tree.command(name="leaderboard", description="Create a leaderboard")
@app_commands.describe(
    type="Type of leaderboard",
    name="Name for the chat counter (required for chat_counter type)",
)
@app_commands.choices(type=[
    app_commands.Choice(name="Custom",       value="custom"),
    app_commands.Choice(name="Chat Counter", value="chat_counter"),
])
async def leaderboard_cmd(interaction: discord.Interaction, type: str, name: str | None = None):
    if type == "custom":
        await interaction.response.send_modal(CreateLBModal())
    elif type == "chat_counter":
        if not name:
            return await interaction.response.send_message("Provide a `name:` for the chat counter.", ephemeral=True)
        if name in chat_counters:
            return await interaction.response.send_message(f"A counter named **{name}** already exists.", ephemeral=True)

        if name in RESTORE_CODES:
            restore = RESTORE_CODES[name]
            counts  = {}
            for dname, count in restore["seed"].items():
                member = discord.utils.find(
                    lambda m, d=dname: m.display_name == d or m.name == d,
                    interaction.guild.members,
                )
                counts[str(member.id) if member else dname] = count
            reset_iso = datetime.fromtimestamp(restore["reset_at_ts"], tz=timezone.utc).isoformat()
            counter = {
                "guild_id":         interaction.guild.id,
                "channel_id":       interaction.channel_id,
                "message_id":       0,
                "title":            restore["title"],
                "footer_text":      "Kongen & Kari's Hangout",
                "show_medals":      True,
                "top_n":            10,
                "counts":           counts,
                "counting_history": False,
                "last_counted_at":  datetime.now(timezone.utc).isoformat(),
                "reset_interval":   restore["reset_interval"],
                "reset_at":         reset_iso,
            }
            live_msg = await interaction.channel.send(
                embed=build_cc_embed(counter, interaction.guild)
            )
            counter["message_id"] = live_msg.id
            chat_counters[name]   = counter
            save_chat_counters()
            return await interaction.response.send_message(
                f"Restored **{restore['title']}** with previous counts. Tracking new messages from now.",
                ephemeral=True,
            )

        sid = f"{interaction.user.id}_{interaction.id}"
        cc_sessions[sid] = {
            "cc_name": name, "title": name,
            "footer_text": "Kongen & Kari's Hangout",
            "show_medals": True, "top_n": 10,
            "author_id": interaction.user.id,
        }
        await interaction.response.send_message(
            embed=build_cc_setup_embed(cc_sessions[sid]),
            view=CCSetupPanel(sid, interaction.user.id),
            ephemeral=True,
        )


async def lb_autocomplete(interaction: discord.Interaction, current: str):
    guild_lbs = get_guild_leaderboards(interaction.guild.id)
    return [
        app_commands.Choice(name=n, value=n)
        for n in guild_lbs if current.lower() in n.lower()
    ][:25]


@bot.tree.command(name="editleaderboard", description="Edit a saved leaderboard")
@app_commands.describe(name="Name of the leaderboard to edit")
@app_commands.autocomplete(name=lb_autocomplete)
async def editleaderboard_cmd(interaction: discord.Interaction, name: str):
    guild_lbs = get_guild_leaderboards(interaction.guild.id)
    if name not in guild_lbs:
        return await interaction.response.send_message(f"No leaderboard named **{name}** found.", ephemeral=True)
    sid = f"{interaction.user.id}_{interaction.id}"
    s   = guild_lbs[name].copy()
    s["lb_name"]   = name
    s["guild_id"]  = interaction.guild.id
    s["author_id"] = interaction.user.id
    lb_sessions[sid] = s
    await interaction.response.send_message(
        embed=build_lb_embed(s), view=LeaderboardPanel(sid, interaction.user.id), ephemeral=True,
    )


@bot.tree.command(name="leaderboardlist", description="List all saved leaderboards on this server")
async def leaderboardlist_cmd(interaction: discord.Interaction):
    guild_lbs = get_guild_leaderboards(interaction.guild.id)
    if not guild_lbs:
        return await interaction.response.send_message("No leaderboards saved on this server.", ephemeral=True)
    names = "\n".join(f"• **{n}**" for n in guild_lbs)
    await interaction.response.send_message(
        f"**Leaderboards ({len(guild_lbs)}):**\n{names}", ephemeral=True
    )


@bot.tree.command(name="leaderboardname", description="Find the saved name of a leaderboard by its message ID")
@app_commands.describe(message_id="The message ID of the posted leaderboard")
async def leaderboardname_cmd(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True)
    try:
        mid = int(message_id)
    except ValueError:
        return await interaction.followup.send("Invalid message ID.", ephemeral=True)

    target_msg = None
    for channel in interaction.guild.text_channels:
        try:
            target_msg = await channel.fetch_message(mid)
            break
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            continue

    if not target_msg:
        return await interaction.followup.send("Message not found in any channel.", ephemeral=True)
    if not target_msg.embeds:
        return await interaction.followup.send("That message has no embed.", ephemeral=True)

    raw_title = target_msg.embeds[0].title or ""
    clean_title = raw_title.lstrip("🏆").strip()

    guild_lbs = get_guild_leaderboards(interaction.guild.id)
    matches = [name for name, data in guild_lbs.items() if data.get("title", "").strip() == clean_title]

    if not matches:
        return await interaction.followup.send(
            f"No saved leaderboard matched the title **{clean_title}**.", ephemeral=True
        )
    await interaction.followup.send(
        f"Leaderboard name: **{matches[0]}**", ephemeral=True
    )


@bot.tree.command(name="embed", description="Build and post a fully custom embed")
async def embed_cmd(interaction: discord.Interaction):
    sid = f"{interaction.user.id}_{interaction.id}"
    emb_sessions[sid] = _blank_emb(interaction.user.id)
    await interaction.response.send_message(
        embed=build_emb_panel_embed(emb_sessions[sid]),
        view=EmbedPanel(sid, interaction.user.id),
        ephemeral=True,
    )


@bot.tree.command(name="giveaway", description="Start a giveaway in this channel")
@app_commands.describe(
    prize="What are you giving away?",
    duration="How long to run — e.g. 30s  5m  2h  1d  1w  combine: 1d12h",
    winners="Number of winners (default: 1)",
    description="Optional extra details or requirements",
)
async def giveaway_cmd(
    interaction: discord.Interaction,
    prize: str,
    duration: str,
    winners: int = 1,
    description: str | None = None,
):
    seconds = parse_duration(duration)
    if seconds is None:
        return await interaction.response.send_message(
            "**Invalid duration.**\n"
            "Use: `s` second · `m` minute · `h` hour · `d` day · `w` week · `mo` month · `y` year\n"
            "Examples: `30s` · `5m` · `2h` · `1d` · `1w` · `1mo` · `1y` · `1d12h`",
            ephemeral=True,
        )
    if not (1 <= winners <= 20):
        return await interaction.response.send_message("Winner count must be between **1** and **20**.", ephemeral=True)

    end_time = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).timestamp()
    gw_id    = f"{interaction.guild_id}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"

    g = {
        "channel_id":    interaction.channel_id,
        "message_id":    0,
        "guild_id":      interaction.guild_id,
        "host_id":       interaction.user.id,
        "prize":         prize,
        "description":   description,
        "end_time":      end_time,
        "winners_count": winners,
        "ended":         False,
        "entries":       [],
    }
    giveaways[gw_id] = g

    view = GiveawayView(gw_id)
    bot.add_view(view)
    await interaction.response.send_message(embed=build_giveaway_embed(g), view=view)
    msg = await interaction.original_response()
    g["message_id"] = msg.id
    save_giveaways()


@bot.tree.command(name="giveaway-end", description="Immediately end an active giveaway by message ID")
@app_commands.describe(message_id="The message ID of the giveaway to end")
async def giveaway_end_cmd(interaction: discord.Interaction, message_id: str):
    try:
        mid = int(message_id)
    except ValueError:
        return await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
    gw_id = next((k for k, v in giveaways.items() if v["message_id"] == mid and v["guild_id"] == interaction.guild_id), None)
    if not gw_id:
        return await interaction.response.send_message("No active giveaway found with that message ID.", ephemeral=True)
    if giveaways[gw_id].get("ended"):
        return await interaction.response.send_message("That giveaway has already ended.", ephemeral=True)
    await interaction.response.send_message("Ending the giveaway now...", ephemeral=True)
    await _end_giveaway(bot, gw_id)


@bot.tree.command(name="giveaway-reroll", description="Reroll the winner(s) of an ended giveaway")
@app_commands.describe(message_id="The message ID of the ended giveaway")
async def giveaway_reroll_cmd(interaction: discord.Interaction, message_id: str):
    try:
        mid = int(message_id)
    except ValueError:
        return await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
    gw_id = next((k for k, v in giveaways.items() if v["message_id"] == mid and v["guild_id"] == interaction.guild_id and v.get("ended")), None)
    if not gw_id:
        return await interaction.response.send_message("No ended giveaway found with that message ID.", ephemeral=True)
    g = giveaways[gw_id]
    if not g["entries"]:
        return await interaction.response.send_message("No entries to reroll from.", ephemeral=True)
    new_winners = random.sample(g["entries"], min(g["winners_count"], len(g["entries"])))
    mentions    = " ".join(f"<@{w}>" for w in new_winners)
    reroll = discord.Embed(
        title="Giveaway Rerolled!",
        description=f"{mentions} {'is' if len(new_winners) == 1 else 'are'} the new winner(s) of **{g['prize']}**!",
        color=GW_YELLOW,
    )
    reroll.add_field(name="New Winner(s)", value="\n".join(f"• <@{w}>" for w in new_winners), inline=False)
    reroll.add_field(name="Hosted by", value=f"<@{g['host_id']}>", inline=True)
    reroll.set_footer(text="Rerolled")
    reroll.timestamp = datetime.now(timezone.utc)
    await interaction.response.send_message(content=mentions, embed=reroll)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TICKET SYSTEM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ─── DB helpers ───────────────────────────────────────────────────────────────

def _tdb() -> sqlite3.Connection:
    conn = sqlite3.connect(TICKET_DB)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # autocommit
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_ticket_db():
    c = _tdb()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS ticket_config (
            guild_id         TEXT PRIMARY KEY,
            category_id      TEXT,
            log_channel_id   TEXT,
            support_role_ids TEXT,
            panel_channel_id TEXT,
            panel_message_id TEXT,
            next_ticket_num  INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL UNIQUE,
            ticket_num  INTEGER NOT NULL,
            owner_id    TEXT NOT NULL,
            claimed_by  TEXT,
            status      TEXT DEFAULT 'open',
            reason      TEXT,
            created_at  INTEGER DEFAULT (strftime('%s','now')),
            closed_at   INTEGER
        );
        CREATE TABLE IF NOT EXISTS ticket_questions (
            guild_id         TEXT NOT NULL,
            prefix           TEXT NOT NULL,
            questions        TEXT NOT NULL DEFAULT '[]',
            category_id      TEXT,
            support_role_ids TEXT,
            PRIMARY KEY (guild_id, prefix)
        );
    """)
    c.close()


_init_ticket_db()


def _tc_get(gid: str) -> dict | None:
    c = _tdb()
    r = c.execute("SELECT * FROM ticket_config WHERE guild_id=?", (gid,)).fetchone()
    c.close()
    return dict(r) if r else None


def _tc_upsert(gid: str, **kw):
    ex = _tc_get(gid) or {}
    m  = {**ex, **kw, "guild_id": gid}
    c  = _tdb()
    c.execute("""
        INSERT INTO ticket_config
            (guild_id,category_id,log_channel_id,support_role_ids,panel_channel_id,panel_message_id,next_ticket_num)
        VALUES
            (:guild_id,:category_id,:log_channel_id,:support_role_ids,:panel_channel_id,:panel_message_id,:next_ticket_num)
        ON CONFLICT(guild_id) DO UPDATE SET
            category_id=excluded.category_id, log_channel_id=excluded.log_channel_id,
            support_role_ids=excluded.support_role_ids, panel_channel_id=excluded.panel_channel_id,
            panel_message_id=excluded.panel_message_id, next_ticket_num=excluded.next_ticket_num
    """, {"guild_id": gid, "category_id": m.get("category_id"), "log_channel_id": m.get("log_channel_id"),
          "support_role_ids": m.get("support_role_ids"), "panel_channel_id": m.get("panel_channel_id"),
          "panel_message_id": m.get("panel_message_id"), "next_ticket_num": m.get("next_ticket_num", 1)})
    c.close()


def _tc_bump(gid: str):
    c = _tdb()
    c.execute("UPDATE ticket_config SET next_ticket_num=next_ticket_num+1 WHERE guild_id=?", (gid,))
    c.close()


def _tq_get(gid: str, prefix: str) -> dict | None:
    c = _tdb()
    r = c.execute("SELECT * FROM ticket_questions WHERE guild_id=? AND prefix=?", (gid, prefix)).fetchone()
    c.close()
    return dict(r) if r else None


def _tq_all(gid: str) -> list[dict]:
    c = _tdb()
    rows = c.execute("SELECT * FROM ticket_questions WHERE guild_id=?", (gid,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def _tq_upsert(gid: str, prefix: str, questions: str, category_id: str | None, support_role_ids: str | None):
    c = _tdb()
    c.execute("""
        INSERT INTO ticket_questions (guild_id,prefix,questions,category_id,support_role_ids)
        VALUES (?,?,?,?,?)
        ON CONFLICT(guild_id,prefix) DO UPDATE SET
            questions=excluded.questions,category_id=excluded.category_id,support_role_ids=excluded.support_role_ids
    """, (gid, prefix, questions, category_id, support_role_ids))
    c.close()


def _tkt_create(gid: str, channel_id: str, num: int, owner_id: str, reason: str | None) -> int:
    c   = _tdb()
    cur = c.execute(
        "INSERT INTO tickets (guild_id,channel_id,ticket_num,owner_id,reason) VALUES (?,?,?,?,?)",
        (gid, channel_id, num, owner_id, reason),
    )
    row_id = cur.lastrowid
    c.close()
    return row_id


def _tkt_by_channel(channel_id: str) -> dict | None:
    c = _tdb()
    r = c.execute("SELECT * FROM tickets WHERE channel_id=?", (str(channel_id),)).fetchone()
    c.close()
    return dict(r) if r else None


def _tkt_by_id(ticket_id: int) -> dict | None:
    c = _tdb()
    r = c.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    c.close()
    return dict(r) if r else None


def _tkt_open(gid: str) -> list[dict]:
    c    = _tdb()
    rows = c.execute("SELECT * FROM tickets WHERE guild_id=? AND status='open'", (gid,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def _tkt_update(ticket_id: int, status: str, claimed_by: str | None, closed_at: int | None):
    c = _tdb()
    c.execute("UPDATE tickets SET status=?,claimed_by=?,closed_at=? WHERE id=?",
              (status, claimed_by, closed_at, ticket_id))
    c.close()


def _support_role_ids(cfg: dict | None) -> list[str]:
    if not cfg:
        return []
    if cfg.get("support_role_ids"):
        try:
            return json.loads(cfg["support_role_ids"])
        except Exception:
            pass
    return []


# ─── Transcript + close ───────────────────────────────────────────────────────

async def _gen_transcript(channel: discord.TextChannel, ticket: dict, cfg: dict | None, client: discord.Client):
    if not cfg or not cfg.get("log_channel_id"):
        return
    try:
        log_ch = client.get_channel(int(cfg["log_channel_id"])) or await client.fetch_channel(int(cfg["log_channel_id"]))
    except Exception:
        return
    try:
        msgs = [m async for m in channel.history(limit=100, oldest_first=True)]
    except Exception:
        return
    lines = []
    for m in msgs:
        ts     = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"[BOT] {m.author.name}" if m.author.bot else f"{m.author.name} ({m.author.id})"
        parts  = []
        if m.content:
            parts.append(m.content)
        for e in m.embeds:
            ep = []
            if e.title:       ep.append(f"[Embed: {e.title}]")
            if e.description: ep.append(e.description[:300])
            parts.append(" — ".join(ep) or "[Embed]")
        if m.attachments:
            parts.append(f"[{len(m.attachments)} attachment(s)]")
        lines.append(f"[{ts}] {author}: {' | '.join(parts) or '[no content]'}")
    header = (
        f"Ticket #{ticket['ticket_num']} — Transcript\n" + "=" * 60 + "\n"
        f"Opened by : {ticket['owner_id']}\n"
        f"Channel   : {channel.name}\n"
        f"Generated : {datetime.now(timezone.utc).isoformat()}\n" + "=" * 60 + "\n\n"
    )
    log_e = discord.Embed(color=0xED4245, title="🎫 Ticket Closed", timestamp=datetime.now(timezone.utc))
    log_e.add_field(name="Ticket #",   value=str(ticket["ticket_num"]), inline=True)
    log_e.add_field(name="Opened by", value=f"<@{ticket['owner_id']}>", inline=True)
    log_e.add_field(name="Messages",  value=str(len(lines)),             inline=True)
    log_e.set_footer(text="Transcript attached below")
    try:
        data = (header + "\n".join(lines)).encode("utf-8")
        await log_ch.send(
            embeds=[log_e],
            files=[discord.File(fp=io.BytesIO(data), filename=f"ticket-{ticket['ticket_num']}-transcript.txt")],
        )
    except Exception as err:
        print(f"[Transcript] {err}")


async def _close_ticket(interaction: discord.Interaction, ticket: dict, cfg: dict | None):
    _tkt_update(ticket["id"], "closed", ticket.get("claimed_by"), int(_time.time()))
    await _gen_transcript(interaction.channel, ticket, cfg, interaction.client)
    close_e = discord.Embed(color=0x5865F2, title="ℹ️ Ticket Closed",
                            description="This channel will be deleted in 5 seconds.")
    await interaction.followup.send(embed=close_e)
    await asyncio.sleep(5)
    try:
        await interaction.channel.delete()
    except Exception as err:
        print(f"[Ticket Delete] {err}")


# ─── Panel builder helpers ────────────────────────────────────────────────────

_PANEL_STYLES = {
    "blue":  discord.ButtonStyle.primary,
    "green": discord.ButtonStyle.success,
    "red":   discord.ButtonStyle.danger,
    "grey":  discord.ButtonStyle.secondary,
}
_PANEL_STYLE_LABEL = {"blue": "Blue", "green": "Green", "red": "Red", "grey": "Grey"}


def _panel_preview_embed(cfg: dict) -> discord.Embed:
    e = discord.Embed(color=cfg.get("color", 0xF4A460),
                      title=cfg.get("title", ""),
                      description=cfg.get("description", ""))
    e.set_footer(text="Panel Preview")
    return e


def _panel_config_embed(cfg: dict) -> discord.Embed:
    buttons = cfg.get("buttons", [])
    if buttons:
        lines = []
        for i, b in enumerate(buttons):
            sl    = _PANEL_STYLE_LABEL.get(b.get("style", ""), "Blue")
            em    = f"{b['emoji']} " if b.get("emoji") else ""
            qn    = f" · {len(b.get('questions', []))}Q" if b.get("questions") else ""
            cn    = " · 📁" if b.get("category_id") else ""
            rn    = f" · 🛡️({len(b['support_role_ids'])})" if b.get("support_role_ids") else ""
            lines.append(f"**{i+1}.** {em}**{b['label']}** ({sl}) → `{b.get('prefix','ticket')}-####`{qn}{cn}{rn}")
        btn_val = "\n".join(lines)
    else:
        btn_val = "*No buttons added yet — click ➕ Add Button*"
    role_ids   = cfg.get("support_role_ids", [])
    roles_val  = " ".join(f"<@&{r}>" for r in role_ids) if role_ids else "❌ Not set"
    e = discord.Embed(color=0x5865F2, title="⚙️ Panel Configuration")
    e.add_field(name="📁 Ticket Category", value=f"<#{cfg['category_id']}>" if cfg.get("category_id") else "❌ Not set", inline=True)
    e.add_field(name="📋 Log Channel",     value=f"<#{cfg['log_channel_id']}>" if cfg.get("log_channel_id") else "❌ Not set", inline=True)
    e.add_field(name=f"🛡️ Support Roles ({len(role_ids)})", value=roles_val, inline=False)
    e.add_field(name=f"🔘 Buttons ({len(buttons)}/5)", value=btn_val, inline=False)
    e.set_footer(text="Category, log channel, at least one support role, and one button must be set.")
    return e


def _btn_config_embed(cfg: dict, idx: int) -> discord.Embed:
    b       = cfg["buttons"][idx]
    cat_v   = f"<#{b['category_id']}>" if b.get("category_id") else "*(uses panel default)*"
    rids    = b.get("support_role_ids", [])
    role_v  = " ".join(f"<@&{r}>" for r in rids) if rids else "*(uses panel defaults)*"
    e = discord.Embed(color=0x5865F2, title=f"⚙️ Configure: {b['label']}")
    e.description = "Set a **specific category and support roles** for this ticket type.\nLeave unset to inherit the panel's global defaults."
    e.add_field(name="📁 Category",      value=cat_v,  inline=True)
    e.add_field(name="🛡️ Support Roles", value=role_v, inline=True)
    return e


# ─── Persistent ticket views ──────────────────────────────────────────────────

class TicketChannelView(discord.ui.View):
    def __init__(self, ticket_id: int):
        super().__init__(timeout=None)
        claim_btn = discord.ui.Button(
            label="Claim", emoji="🙋", style=discord.ButtonStyle.secondary,
            custom_id=f"ticket_claim:{ticket_id}",
        )
        close_btn = discord.ui.Button(
            label="Close", emoji="🔒", style=discord.ButtonStyle.danger,
            custom_id=f"ticket_close:{ticket_id}",
        )

        async def _claim(interaction: discord.Interaction):
            t   = _tkt_by_id(ticket_id)
            cfg = _tc_get(str(interaction.guild_id))
            if not t or t["status"] != "open":
                return await interaction.response.send_message(
                    embed=discord.Embed(color=0xED4245, title="❌ Ticket not found",
                                        description="This ticket could not be found or is already closed."),
                    ephemeral=True)
            roles = _support_role_ids(cfg)
            if not any(interaction.user.get_role(int(r)) for r in roles):  # type: ignore[union-attr]
                return await interaction.response.send_message(
                    embed=discord.Embed(color=0xED4245, title="❌ Permission denied",
                                        description="Only support staff can claim tickets."),
                    ephemeral=True)
            _tkt_update(ticket_id, "open", str(interaction.user.id), None)
            await interaction.response.send_message(
                embed=discord.Embed(color=0x5865F2, title="ℹ️ Ticket Claimed",
                                    description=f"{interaction.user.mention} has claimed this ticket."))

        async def _close(interaction: discord.Interaction):
            t   = _tkt_by_id(ticket_id)
            cfg = _tc_get(str(interaction.guild_id))
            if not t or t["status"] != "open":
                return await interaction.response.send_message(
                    embed=discord.Embed(color=0xED4245, title="❌ Ticket not found",
                                        description="This ticket could not be found or is already closed."),
                    ephemeral=True)
            roles    = _support_role_ids(cfg)
            can_close = (
                str(interaction.user.id) == t["owner_id"]
                or any(interaction.user.get_role(int(r)) for r in roles)  # type: ignore[union-attr]
                or interaction.user.guild_permissions.manage_channels       # type: ignore[union-attr]
            )
            if not can_close:
                return await interaction.response.send_message(
                    embed=discord.Embed(color=0xED4245, title="❌ Permission denied",
                                        description="Only the ticket owner or staff can close this."),
                    ephemeral=True)
            await interaction.response.defer()
            await _close_ticket(interaction, t, cfg)

        claim_btn.callback = _claim
        close_btn.callback = _close
        self.add_item(claim_btn)
        self.add_item(close_btn)


def _make_panel_listener(prefixes: list[str]) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for prefix in prefixes:
        btn = discord.ui.Button(label="Open Ticket", custom_id=f"ticket_create:{prefix}",
                                style=discord.ButtonStyle.primary)
        async def _cb(interaction: discord.Interaction, pfx: str = prefix):
            await _handle_ticket_create(interaction, pfx)
        btn.callback = _cb
        v.add_item(btn)
    return v


# ─── Panel builder views ──────────────────────────────────────────────────────

class BtnConfigView(discord.ui.View):
    def __init__(self, user_id: str, idx: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.idx     = idx
        self._populate()

    def _cfg(self):  return ticket_panel_builders.get(self.user_id, {})
    def _btn(self):  return self._cfg().get("buttons", [])[self.idx]

    def _populate(self):
        self.clear_items()
        cfg = self._cfg()
        btn = self._btn()

        cat = discord.ui.ChannelSelect(
            placeholder="✅ Category set — click to change" if btn.get("category_id") else "📁 Select category for this ticket type...",
            channel_types=[discord.ChannelType.category], row=0,
        )
        async def _on_cat(interaction: discord.Interaction):
            cfg2 = self._cfg()
            cfg2["buttons"][self.idx]["category_id"] = str(cat.values[0].id)
            ticket_panel_builders[self.user_id] = cfg2
            v = BtnConfigView(self.user_id, self.idx)
            await interaction.response.edit_message(embeds=[_btn_config_embed(cfg2, self.idx)], view=v)
        cat.callback = _on_cat
        self.add_item(cat)

        role = discord.ui.RoleSelect(
            placeholder="✅ Roles set — select to replace" if btn.get("support_role_ids") else "🛡️ Select support roles for this ticket type...",
            min_values=1, max_values=10, row=1,
        )
        async def _on_role(interaction: discord.Interaction):
            cfg2 = self._cfg()
            cfg2["buttons"][self.idx]["support_role_ids"] = [str(r.id) for r in role.values]
            ticket_panel_builders[self.user_id] = cfg2
            v = BtnConfigView(self.user_id, self.idx)
            await interaction.response.edit_message(embeds=[_btn_config_embed(cfg2, self.idx)], view=v)
        role.callback = _on_role
        self.add_item(role)

        done = discord.ui.Button(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=2)
        async def _on_done(interaction: discord.Interaction):
            cfg2 = self._cfg()
            v    = PanelBuilderView(self.user_id)
            await interaction.response.edit_message(
                embeds=[_panel_preview_embed(cfg2), _panel_config_embed(cfg2)], view=v)
        done.callback = _on_done
        self.add_item(done)

        if btn.get("category_id"):
            clr_cat = discord.ui.Button(label="Clear Category", style=discord.ButtonStyle.secondary, row=2)
            async def _on_clr_cat(interaction: discord.Interaction):
                cfg2 = self._cfg()
                cfg2["buttons"][self.idx]["category_id"] = None
                ticket_panel_builders[self.user_id] = cfg2
                v = BtnConfigView(self.user_id, self.idx)
                await interaction.response.edit_message(embeds=[_btn_config_embed(cfg2, self.idx)], view=v)
            clr_cat.callback = _on_clr_cat
            self.add_item(clr_cat)

        if btn.get("support_role_ids"):
            clr_roles = discord.ui.Button(label="Clear Roles", style=discord.ButtonStyle.secondary, row=2)
            async def _on_clr_roles(interaction: discord.Interaction):
                cfg2 = self._cfg()
                cfg2["buttons"][self.idx]["support_role_ids"] = []
                ticket_panel_builders[self.user_id] = cfg2
                v = BtnConfigView(self.user_id, self.idx)
                await interaction.response.edit_message(embeds=[_btn_config_embed(cfg2, self.idx)], view=v)
            clr_roles.callback = _on_clr_roles
            self.add_item(clr_roles)


class PanelBuilderView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=600)
        self.user_id = user_id
        self._populate()

    def _cfg(self): return ticket_panel_builders.get(self.user_id, {})

    def _populate(self):
        self.clear_items()
        cfg     = self._cfg()
        buttons = cfg.get("buttons", [])

        cat = discord.ui.ChannelSelect(
            placeholder="✅ Category set — click to change" if cfg.get("category_id") else "📁 Select ticket category...",
            channel_types=[discord.ChannelType.category], row=0,
        )
        async def _on_cat(interaction: discord.Interaction):
            cfg2 = self._cfg(); cfg2["category_id"] = str(cat.values[0].id)
            ticket_panel_builders[self.user_id] = cfg2
            v = PanelBuilderView(self.user_id)
            await interaction.response.edit_message(embeds=[_panel_preview_embed(cfg2), _panel_config_embed(cfg2)], view=v)
        cat.callback = _on_cat
        self.add_item(cat)

        log = discord.ui.ChannelSelect(
            placeholder="✅ Log channel set — click to change" if cfg.get("log_channel_id") else "📋 Select log channel...",
            channel_types=[discord.ChannelType.text], row=1,
        )
        async def _on_log(interaction: discord.Interaction):
            cfg2 = self._cfg(); cfg2["log_channel_id"] = str(log.values[0].id)
            ticket_panel_builders[self.user_id] = cfg2
            v = PanelBuilderView(self.user_id)
            await interaction.response.edit_message(embeds=[_panel_preview_embed(cfg2), _panel_config_embed(cfg2)], view=v)
        log.callback = _on_log
        self.add_item(log)

        role = discord.ui.RoleSelect(
            placeholder="✅ Roles set — select again to replace" if cfg.get("support_role_ids") else "🛡️ Select support roles...",
            min_values=1, max_values=10, row=2,
        )
        async def _on_role(interaction: discord.Interaction):
            cfg2 = self._cfg(); cfg2["support_role_ids"] = [str(r.id) for r in role.values]
            ticket_panel_builders[self.user_id] = cfg2
            v = PanelBuilderView(self.user_id)
            await interaction.response.edit_message(embeds=[_panel_preview_embed(cfg2), _panel_config_embed(cfg2)], view=v)
        role.callback = _on_role
        self.add_item(role)

        if buttons:
            edit_sel = discord.ui.Select(
                placeholder="✏️ Select a button to edit...",
                options=[
                    discord.SelectOption(
                        label=(b["label"] or f"Button {i+1}")[:100],
                        value=str(i),
                        description=f"prefix: {b.get('prefix','ticket')} · {len(b.get('questions',[]))} question(s)",
                    ) for i, b in enumerate(buttons)
                ], row=3,
            )
            async def _on_edit_sel(interaction: discord.Interaction):
                await interaction.response.send_modal(EditButtonModal(self.user_id, int(edit_sel.values[0])))
            edit_sel.callback = _on_edit_sel
            self.add_item(edit_sel)

        ready = bool(cfg.get("category_id") and cfg.get("log_channel_id") and cfg.get("support_role_ids") and buttons)

        add_btn = discord.ui.Button(label="Add Button", emoji="➕", style=discord.ButtonStyle.primary, row=4, disabled=len(buttons) >= 5)
        async def _on_add(interaction: discord.Interaction):
            await interaction.response.send_modal(AddButtonModal(self.user_id))
        add_btn.callback = _on_add
        self.add_item(add_btn)

        rm_btn = discord.ui.Button(label="Remove Last", emoji="↩️", style=discord.ButtonStyle.secondary, row=4, disabled=not buttons)
        async def _on_rm(interaction: discord.Interaction):
            cfg2 = self._cfg(); cfg2["buttons"].pop()
            ticket_panel_builders[self.user_id] = cfg2
            v = PanelBuilderView(self.user_id)
            await interaction.response.edit_message(embeds=[_panel_preview_embed(cfg2), _panel_config_embed(cfg2)], view=v)
        rm_btn.callback = _on_rm
        self.add_item(rm_btn)

        edit_text = discord.ui.Button(label="Edit Text", emoji="✏️", style=discord.ButtonStyle.secondary, row=4)
        async def _on_edit_text(interaction: discord.Interaction):
            await interaction.response.send_modal(EditPanelTextModal(self.user_id))
        edit_text.callback = _on_edit_text
        self.add_item(edit_text)

        post_btn = discord.ui.Button(label="Post Panel", emoji="✅", style=discord.ButtonStyle.success, row=4, disabled=not ready)
        async def _on_post(interaction: discord.Interaction):
            await _post_ticket_panel(interaction, self.user_id)
        post_btn.callback = _on_post
        self.add_item(post_btn)

        cancel_btn = discord.ui.Button(label="Cancel", emoji="🗑️", style=discord.ButtonStyle.danger, row=4)
        async def _on_cancel(interaction: discord.Interaction):
            ticket_panel_builders.pop(self.user_id, None)
            await interaction.response.edit_message(
                embeds=[discord.Embed(color=0x5865F2, title="Cancelled", description="Panel builder closed.")], view=None)
        cancel_btn.callback = _on_cancel
        self.add_item(cancel_btn)


# ─── Ticket + panel modals ────────────────────────────────────────────────────

def _parse_hex_color(raw: str) -> int:
    try:
        return int(raw.strip().replace("#", ""), 16)
    except Exception:
        return 0xF4A460


def _parse_questions(raw: str) -> list[dict]:
    qs = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        qs.append({"label": line[1:].strip() if line.startswith("*") else line, "required": not line.startswith("*")})
    return qs[:5]


class TicketPanelInitModal(discord.ui.Modal, title="Ticket Panel Builder"):
    panel_title = discord.ui.TextInput(label="Panel Title", max_length=100, required=True)
    description = discord.ui.TextInput(label="Panel Description", style=discord.TextStyle.paragraph, max_length=2000, required=True)
    color       = discord.ui.TextInput(label="Embed Color (hex, e.g. F4A460)", max_length=7, required=False, default="F4A460")

    def __init__(self, channel_id: str):
        super().__init__()
        self._channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        uid    = str(interaction.user.id)
        ex     = ticket_panel_builders.get(uid, {})
        config = {
            "channel_id": self._channel_id, "edit_message_id": None,
            "title": self.panel_title.value.strip(), "description": self.description.value.strip(),
            "color": _parse_hex_color(self.color.value),
            "category_id": ex.get("category_id"), "log_channel_id": ex.get("log_channel_id"),
            "support_role_ids": ex.get("support_role_ids", []), "buttons": ex.get("buttons", []),
        }
        ticket_panel_builders[uid] = config
        v = PanelBuilderView(uid)
        await interaction.response.send_message(
            embeds=[_panel_preview_embed(config), _panel_config_embed(config)], view=v, ephemeral=True)


class EditPanelTextModal(discord.ui.Modal, title="Edit Panel Text"):
    panel_title = discord.ui.TextInput(label="Panel Title", max_length=100, required=True)
    description = discord.ui.TextInput(label="Panel Description", style=discord.TextStyle.paragraph, max_length=2000, required=True)
    color       = discord.ui.TextInput(label="Embed Color (hex, e.g. F4A460)", max_length=7, required=False)

    def __init__(self, user_id: str):
        super().__init__()
        self._uid = user_id
        cfg = ticket_panel_builders.get(user_id, {})
        self.panel_title.default = cfg.get("title", "")
        self.description.default = cfg.get("description", "")
        self.color.default       = f"{cfg.get('color', 0xF4A460):06X}"

    async def on_submit(self, interaction: discord.Interaction):
        cfg = ticket_panel_builders.get(self._uid, {})
        cfg["title"]       = self.panel_title.value.strip()
        cfg["description"] = self.description.value.strip()
        if self.color.value.strip():
            cfg["color"] = _parse_hex_color(self.color.value)
        ticket_panel_builders[self._uid] = cfg
        v = PanelBuilderView(self._uid)
        await interaction.response.edit_message(
            embeds=[_panel_preview_embed(cfg), _panel_config_embed(cfg)], view=v)


class AddButtonModal(discord.ui.Modal, title="Add Ticket Button"):
    btn_label     = discord.ui.TextInput(label="Button Label", max_length=80, required=True)
    btn_emoji     = discord.ui.TextInput(label="Emoji (optional)", max_length=10, required=False)
    btn_prefix    = discord.ui.TextInput(label="Ticket Name Prefix", max_length=20, required=False, placeholder="e.g. support → channel: support-0001")
    btn_style     = discord.ui.TextInput(label="Button Color (blue / green / red / grey)", max_length=10, required=False, placeholder="blue")
    btn_questions = discord.ui.TextInput(label="Questions (one per line, * = optional)", style=discord.TextStyle.paragraph, max_length=500, required=False, placeholder="Are you buying or selling?\nHow many units?\n*Any notes?")

    def __init__(self, user_id: str):
        super().__init__()
        self._uid = user_id

    async def on_submit(self, interaction: discord.Interaction):
        cfg = ticket_panel_builders.get(self._uid)
        if not cfg:
            return await interaction.response.send_message("Session expired. Run `/ticket panel` again.", ephemeral=True)
        prefix = re.sub(r"\s+", "-", self.btn_prefix.value.strip().lower()) or "ticket"
        cfg["buttons"].append({
            "label": self.btn_label.value.strip(), "emoji": self.btn_emoji.value.strip(),
            "style": self.btn_style.value.strip().lower(), "prefix": prefix,
            "questions": _parse_questions(self.btn_questions.value),
            "category_id": None, "support_role_ids": [],
        })
        ticket_panel_builders[self._uid] = cfg
        idx = len(cfg["buttons"]) - 1
        await interaction.response.edit_message(embeds=[_btn_config_embed(cfg, idx)], view=BtnConfigView(self._uid, idx))


class EditButtonModal(discord.ui.Modal, title="Edit Ticket Button"):
    btn_label     = discord.ui.TextInput(label="Button Label", max_length=80, required=True)
    btn_emoji     = discord.ui.TextInput(label="Emoji (optional)", max_length=10, required=False)
    btn_prefix    = discord.ui.TextInput(label="Ticket Name Prefix", max_length=20, required=False)
    btn_style     = discord.ui.TextInput(label="Button Color (blue / green / red / grey)", max_length=10, required=False)
    btn_questions = discord.ui.TextInput(label="Questions (one per line, * = optional)", style=discord.TextStyle.paragraph, max_length=500, required=False)

    def __init__(self, user_id: str, idx: int):
        super().__init__()
        self._uid = user_id
        self._idx = idx
        cfg = ticket_panel_builders.get(user_id, {})
        if cfg.get("buttons") and idx < len(cfg["buttons"]):
            b = cfg["buttons"][idx]
            self.btn_label.default     = b.get("label", "")
            self.btn_emoji.default     = b.get("emoji", "")
            self.btn_prefix.default    = b.get("prefix", "ticket")
            self.btn_style.default     = b.get("style", "blue")
            self.btn_questions.default = "\n".join(
                ("*" if not q["required"] else "") + q["label"]
                for q in b.get("questions", [])
            )

    async def on_submit(self, interaction: discord.Interaction):
        cfg = ticket_panel_builders.get(self._uid)
        if not cfg or self._idx >= len(cfg.get("buttons", [])):
            return await interaction.response.send_message("Session expired. Run `/ticket panel` again.", ephemeral=True)
        prefix = re.sub(r"\s+", "-", self.btn_prefix.value.strip().lower()) or "ticket"
        b      = cfg["buttons"][self._idx]
        cfg["buttons"][self._idx] = {
            "label": self.btn_label.value.strip(), "emoji": self.btn_emoji.value.strip(),
            "style": self.btn_style.value.strip().lower(), "prefix": prefix,
            "questions": _parse_questions(self.btn_questions.value),
            "category_id": b.get("category_id"), "support_role_ids": b.get("support_role_ids", []),
        }
        ticket_panel_builders[self._uid] = cfg
        await interaction.response.edit_message(embeds=[_btn_config_embed(cfg, self._idx)], view=BtnConfigView(self._uid, self._idx))


class TicketQsFormModal(discord.ui.Modal):
    def __init__(self, prefix: str, questions: list[dict]):
        type_name = prefix.replace("-", " ").title()
        super().__init__(title=f"{type_name} — Open a Ticket")
        self._prefix    = prefix
        self._questions = questions
        for i, q in enumerate(questions[:5]):
            is_para = bool(re.search(r"explain|describe|proof|detail|reason|issue|problem", q["label"], re.I))
            self.add_item(discord.ui.TextInput(
                custom_id=f"q{i}", label=q["label"][:45],
                style=discord.TextStyle.paragraph if is_para else discord.TextStyle.short,
                required=q.get("required", True), max_length=500,
            ))

    async def on_submit(self, interaction: discord.Interaction):
        answers = [
            {"question": q["label"], "answer": self.children[i].value}
            for i, q in enumerate(self._questions[:5])
        ]
        await _create_ticket_channel(interaction, self._prefix, answers)


class EditMessageModal(discord.ui.Modal, title="Edit Message"):
    msg_title = discord.ui.TextInput(label="Embed Title", max_length=256, required=False)
    msg_body  = discord.ui.TextInput(label="Embed Description / Message Content", style=discord.TextStyle.paragraph, max_length=4000, required=False)

    def __init__(self, message: discord.Message):
        super().__init__()
        self._msg = message
        if message.embeds:
            self.msg_title.default = message.embeds[0].title or ""
            self.msg_body.default  = (message.embeds[0].description or "")[:4000]
        else:
            self.msg_body.default  = message.content[:4000] if message.content else ""

    async def on_submit(self, interaction: discord.Interaction):
        new_title = self.msg_title.value.strip()
        new_body  = self.msg_body.value.strip()
        if self._msg.embeds:
            updated = discord.Embed.from_dict(self._msg.embeds[0].to_dict())
            updated.title       = new_title or None
            updated.description = new_body  or None
            await self._msg.edit(embeds=[updated])
        else:
            await self._msg.edit(content=new_body or None)
        await interaction.response.send_message(
            embed=discord.Embed(color=0x57F287, title="✅ Message Updated", description="The message has been edited."),
            ephemeral=True)


# ─── Ticket creation ──────────────────────────────────────────────────────────

async def _handle_ticket_create(interaction: discord.Interaction, prefix: str):
    gid    = str(interaction.guild_id)
    cfg    = _tc_get(gid)
    btn_cfg = _tq_get(gid, prefix)
    if not btn_cfg and not cfg:
        return await interaction.response.send_message(
            embed=discord.Embed(color=0xED4245, title="❌ Not configured",
                                description="No ticket panel has been set up yet."), ephemeral=True)
    existing = next((t for t in _tkt_open(gid) if t["owner_id"] == str(interaction.user.id)), None)
    if existing:
        return await interaction.response.send_message(
            embed=discord.Embed(color=0xFEE75C, title="⚠️ Already open",
                                description=f"You already have a ticket open: <#{existing['channel_id']}>"), ephemeral=True)
    q_list = json.loads(btn_cfg["questions"]) if btn_cfg and btn_cfg.get("questions") else []
    if q_list:
        return await interaction.response.send_modal(TicketQsFormModal(prefix, q_list))
    await _create_ticket_channel(interaction, prefix, [])


async def _create_ticket_channel(interaction: discord.Interaction, prefix: str, answers: list[dict]):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    gid      = str(interaction.guild_id)
    cfg      = _tc_get(gid)
    btn_cfg  = _tq_get(gid, prefix)
    cat_id   = (btn_cfg.get("category_id") if btn_cfg else None) or (cfg.get("category_id") if cfg else None)
    if not cat_id:
        return await interaction.followup.send(
            embed=discord.Embed(color=0xED4245, title="❌ Not configured",
                                description="No ticket panel has been set up yet."), ephemeral=True)
    if btn_cfg and btn_cfg.get("support_role_ids"):
        try:
            role_ids = json.loads(btn_cfg["support_role_ids"])
        except Exception:
            role_ids = []
    else:
        role_ids = _support_role_ids(cfg)
    existing = next((t for t in _tkt_open(gid) if t["owner_id"] == str(interaction.user.id)), None)
    if existing:
        return await interaction.followup.send(
            embed=discord.Embed(color=0xFEE75C, title="⚠️ Already open",
                                description=f"You already have a ticket open: <#{existing['channel_id']}>"), ephemeral=True)
    type_name = prefix.replace("-", " ").title()
    num       = (cfg or {}).get("next_ticket_num", 1)
    guild     = interaction.guild
    safe_name = re.sub(r"[^a-z0-9_-]", "-", interaction.user.name.lower())
    safe_name = re.sub(r"-{2,}", "-", safe_name).strip("-")[:25] or "user"
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    for rid in role_ids:
        role_obj = guild.get_role(int(rid))
        if role_obj:
            overwrites[role_obj] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    channel = await guild.create_text_channel(
        name=f"{prefix}-{safe_name}",
        category=guild.get_channel(int(cat_id)),
        overwrites=overwrites,
    )
    ticket_id = _tkt_create(gid, str(channel.id), num, str(interaction.user.id),
                            "\n".join(f"{a['question']}: {a['answer']}" for a in answers) if answers else None)
    _tc_bump(gid)
    display = interaction.user.display_name
    greet   = discord.Embed(color=0xF4A460, title=f"🎫 {type_name} — {display}",
                            description=f"Hello {interaction.user.mention}! Support staff will be with you shortly.",
                            timestamp=datetime.now(timezone.utc))
    greet.set_footer(text="Use /ticket close to close this ticket.")
    if answers:
        for a in answers:
            greet.add_field(name=a["question"], value=a["answer"] or "—", inline=False)
    view = TicketChannelView(ticket_id)
    bot.add_view(view)
    ping = f"{interaction.user.mention} " + " ".join(f"<@&{r}>" for r in role_ids) if role_ids else interaction.user.mention
    await channel.send(content=ping, embeds=[greet], view=view)
    if cfg and cfg.get("log_channel_id"):
        try:
            log_ch = interaction.client.get_channel(int(cfg["log_channel_id"])) or \
                     await interaction.client.fetch_channel(int(cfg["log_channel_id"]))
            log_e  = discord.Embed(color=0x57F287, title=f"{type_name} Ticket Opened",
                                   timestamp=datetime.now(timezone.utc))
            log_e.add_field(name="User",    value=f"{interaction.user.mention} ({interaction.user.id})", inline=True)
            log_e.add_field(name="Channel", value=f"<#{channel.id}>",                                   inline=True)
            log_e.add_field(name="Type",    value=type_name,                                            inline=True)
            if answers:
                for a in answers:
                    log_e.add_field(name=a["question"], value=a["answer"] or "—", inline=False)
            await log_ch.send(embeds=[log_e])
        except Exception:
            pass
    await interaction.followup.send(
        embed=discord.Embed(color=0x57F287, title="✅ Ticket Created",
                            description=f"Your ticket is open in {channel.mention}."), ephemeral=True)


# ─── Post panel ───────────────────────────────────────────────────────────────

async def _post_ticket_panel(interaction: discord.Interaction, user_id: str):
    cfg = ticket_panel_builders.get(user_id)
    if not cfg:
        return await interaction.response.send_message("Session expired. Run `/ticket panel` again.", ephemeral=True)
    if not cfg.get("buttons"):
        return await interaction.response.send_message("Add at least one button before posting.", ephemeral=True)
    if not cfg.get("edit_message_id") and not (cfg.get("category_id") and cfg.get("log_channel_id") and cfg.get("support_role_ids")):
        return await interaction.response.send_message("Fill in category, log channel, and at least one support role.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=False)
    try:
        channel = interaction.client.get_channel(int(cfg["channel_id"])) or \
                  await interaction.client.fetch_channel(int(cfg["channel_id"]))
    except Exception:
        return await interaction.followup.send("Target channel not found.", ephemeral=True)
    panel_embed = discord.Embed(color=cfg.get("color", 0xF4A460), title=cfg["title"], description=cfg["description"])
    panel_embed.set_footer(text="Kongen & Kari's Helper • Ticket System")
    btn_components = []
    for b in cfg["buttons"]:
        style = _PANEL_STYLES.get(b.get("style", ""), discord.ButtonStyle.primary)
        btn   = discord.ui.Button(label=b["label"], style=style, custom_id=f"ticket_create:{b['prefix']}")
        if b.get("emoji"):
            try:
                btn.emoji = b["emoji"]
            except Exception:
                pass
        btn_components.append(btn)
    panel_view = discord.ui.View(timeout=None)
    for bc in btn_components:
        panel_view.add_item(bc)
    payload = {"embeds": [panel_embed], "view": panel_view}
    if cfg.get("edit_message_id"):
        try:
            existing_msg = await channel.fetch_message(int(cfg["edit_message_id"]))
            await existing_msg.edit(**payload)
            panel_msg_id = cfg["edit_message_id"]
        except Exception:
            return await interaction.followup.send("Original message not found — it may have been deleted.", ephemeral=True)
    else:
        sent         = await channel.send(**payload)
        panel_msg_id = str(sent.id)
    gid = str(interaction.guild_id)
    _tc_upsert(gid, category_id=cfg.get("category_id"), log_channel_id=cfg.get("log_channel_id"),
               support_role_ids=json.dumps(cfg.get("support_role_ids", [])),
               panel_channel_id=cfg["channel_id"], panel_message_id=panel_msg_id)
    for b in cfg["buttons"]:
        _tq_upsert(gid, b["prefix"], json.dumps(b.get("questions", [])),
                   b.get("category_id"), json.dumps(b["support_role_ids"]) if b.get("support_role_ids") else None)
    listener = _make_panel_listener([b["prefix"] for b in cfg["buttons"]])
    bot.add_view(listener, message_id=int(panel_msg_id))
    ticket_panel_builders.pop(user_id, None)
    verb = "Updated" if cfg.get("edit_message_id") else "Posted"
    await interaction.followup.send(
        embed=discord.Embed(color=0x57F287, title=f"✅ Panel {verb}!",
                            description=f"Your ticket panel in {channel.mention} has been {verb.lower()}."),
        ephemeral=True)


# ─── /ticket subcommand group ─────────────────────────────────────────────────

ticket_group = app_commands.Group(name="ticket", description="Ticket management commands")


@ticket_group.command(name="panel", description="Build and post a customizable ticket panel")
@app_commands.describe(channel="Channel to post the panel in")
async def ticket_panel_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.send_modal(TicketPanelInitModal(str(channel.id)))


@ticket_group.command(name="close", description="Close the current ticket")
@app_commands.describe(reason="Optional reason for closing")
async def ticket_close_cmd(interaction: discord.Interaction, reason: str | None = None):
    ticket = _tkt_by_channel(str(interaction.channel_id))
    cfg    = _tc_get(str(interaction.guild_id))
    if not ticket or ticket["status"] != "open":
        return await interaction.response.send_message(
            embed=discord.Embed(color=0xED4245, title="❌ Not a ticket",
                                description="This command can only be used inside an open ticket channel."),
            ephemeral=True)
    roles     = _support_role_ids(cfg)
    can_close = (
        str(interaction.user.id) == ticket["owner_id"]
        or any(interaction.user.get_role(int(r)) for r in roles)  # type: ignore[union-attr]
        or interaction.user.guild_permissions.manage_channels       # type: ignore[union-attr]
    )
    if not can_close:
        return await interaction.response.send_message(
            embed=discord.Embed(color=0xED4245, title="❌ Permission denied",
                                description="Only the ticket owner or staff can close this ticket."),
            ephemeral=True)
    await interaction.response.defer()
    await _close_ticket(interaction, ticket, cfg)


@ticket_group.command(name="rename", description="Rename this ticket channel")
@app_commands.describe(name="New name for the channel")
async def ticket_rename_cmd(interaction: discord.Interaction, name: str):
    ticket = _tkt_by_channel(str(interaction.channel_id))
    if not ticket:
        return await interaction.response.send_message(
            embed=discord.Embed(color=0xED4245, title="❌ Not a ticket",
                                description="This command can only be used inside a ticket channel."),
            ephemeral=True)
    new_name = re.sub(r"[^a-z0-9_-]", "-", name.lower())
    new_name = re.sub(r"-{2,}", "-", new_name).strip("-")[:100]
    if not new_name:
        return await interaction.response.send_message(
            embed=discord.Embed(color=0xED4245, title="❌ Invalid name",
                                description="Channel name must contain at least one valid character."),
            ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.edit(name=new_name)
    await interaction.followup.send(
        embed=discord.Embed(color=0x57F287, title="✅ Renamed",
                            description=f"Channel renamed to **{new_name}**."), ephemeral=True)


@ticket_group.command(name="edit", description="Edit an existing bot message or ticket panel")
@app_commands.describe(message_id="The ID of the message to edit")
async def ticket_edit_cmd(interaction: discord.Interaction, message_id: str):
    try:
        msg = await interaction.channel.fetch_message(int(message_id.strip()))
    except Exception:
        return await interaction.response.send_message(
            embed=discord.Embed(color=0xED4245, title="❌ Not found",
                                description="Could not find that message in this channel."), ephemeral=True)
    if msg.author.id != interaction.client.user.id:
        return await interaction.response.send_message(
            embed=discord.Embed(color=0xED4245, title="❌ Not my message",
                                description="I can only edit messages sent by me."), ephemeral=True)
    is_panel = any(
        c.custom_id and c.custom_id.startswith("ticket_create:")
        for row in msg.components for c in row.children
    )
    if is_panel:
        gid     = str(interaction.guild_id)
        cfg     = _tc_get(gid)
        embed_d = msg.embeds[0] if msg.embeds else None
        buttons = []
        for row in msg.components:
            for comp in row.children:
                if not (comp.custom_id and comp.custom_id.startswith("ticket_create:")):
                    continue
                pfx       = comp.custom_id.split(":", 1)[1]
                q_row     = _tq_get(gid, pfx)
                qs        = json.loads(q_row["questions"]) if q_row and q_row.get("questions") else []
                cat       = q_row.get("category_id") if q_row else None
                rids      = json.loads(q_row["support_role_ids"]) if q_row and q_row.get("support_role_ids") else []
                style_map = {1: "blue", 3: "green", 4: "red", 2: "grey"}
                em_str    = str(comp.emoji) if comp.emoji else ""
                buttons.append({
                    "label": comp.label or "", "emoji": em_str,
                    "style": style_map.get(comp.style.value, "blue"),
                    "prefix": pfx, "questions": qs, "category_id": cat, "support_role_ids": rids,
                })
        uid    = str(interaction.user.id)
        config = {
            "channel_id": str(msg.channel.id), "edit_message_id": str(msg.id),
            "title": embed_d.title if embed_d else "", "description": embed_d.description if embed_d else "",
            "color": embed_d.color.value if embed_d and embed_d.color else 0xF4A460,
            "category_id": cfg.get("category_id") if cfg else None,
            "log_channel_id": cfg.get("log_channel_id") if cfg else None,
            "support_role_ids": _support_role_ids(cfg), "buttons": buttons,
        }
        ticket_panel_builders[uid] = config
        v = PanelBuilderView(uid)
        return await interaction.response.send_message(
            embeds=[_panel_preview_embed(config), _panel_config_embed(config)], view=v, ephemeral=True)
    await interaction.response.send_modal(EditMessageModal(msg))


bot.tree.add_command(ticket_group)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PAID AD REMINDERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PA_FILE = os.path.join(DATA_DIR, "paid_ads.json")

# In-memory: { ad_id: { reminder_channel_id, role_ids, ad_channel_id, bought, paid,
#               starts_ts, ends_ts, server, reminders_secs:[...], fired:[], guild_id } }
paid_ads: dict[str, dict] = {}

# Per-user wizard state while setting up an ad
paid_ad_sessions: dict[str, dict] = {}

# Combined schedule options: (label, [seconds_before_start, ...])
PA_SCHEDULES = [
    ("1 reminder — 15 min before",           [900]),
    ("1 reminder — 30 min before",           [1800]),
    ("1 reminder — 1 hour before",           [3600]),
    ("1 reminder — 2 hours before",          [7200]),
    ("2 reminders — 1h & 15min before",      [3600, 900]),
    ("2 reminders — 2h & 1h before",         [7200, 3600]),
    ("2 reminders — 3h & 1h before",         [10800, 3600]),
    ("3 reminders — 3h, 1h & 15min before",  [10800, 3600, 900]),
    ("3 reminders — 6h, 2h & 30min before",  [21600, 7200, 1800]),
    ("4 reminders — 1d, 6h, 2h & 30min",     [86400, 21600, 7200, 1800]),
]

_PA_DT_FMTS = [
    "%B %d, %Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M",
    "%b %d, %Y %H:%M",
]


def _pa_load():
    global paid_ads
    if os.path.exists(PA_FILE):
        with open(PA_FILE) as f:
            paid_ads = json.load(f)


def _pa_save():
    with open(PA_FILE, "w") as f:
        json.dump(paid_ads, f, indent=2)


def _parse_ad_dt(text: str) -> datetime | None:
    text = text.strip()
    for fmt in _PA_DT_FMTS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _pa_embed(ad: dict) -> discord.Embed:
    starts = datetime.fromtimestamp(ad["starts_ts"], tz=timezone.utc)
    ends   = datetime.fromtimestamp(ad["ends_ts"],   tz=timezone.utc)
    roles  = " ".join(f"<@&{r}>" for r in ad.get("role_ids", []))
    e = discord.Embed(title="📢 Paid Ad Details", color=0x5865F2)
    e.add_field(name="Bought",       value=ad["bought"],                              inline=True)
    e.add_field(name="Paid",         value=ad["paid"],                                inline=True)
    e.add_field(name="​",       value="​",                                 inline=True)
    e.add_field(name="Starts",       value=discord.utils.format_dt(starts, "F"),     inline=True)
    e.add_field(name="Ends",         value=discord.utils.format_dt(ends,   "F"),     inline=True)
    e.add_field(name="​",       value="​",                                 inline=True)
    e.add_field(name="Ad Channel",   value=f"<#{ad['ad_channel_id']}>",              inline=True)
    e.add_field(name="Server",       value=ad["server"],                             inline=True)
    e.add_field(name="​",       value="​",                                 inline=True)
    if roles:
        e.add_field(name="Ping Roles", value=roles,                                  inline=False)
    sched_labels = [
        discord.utils.format_dt(datetime.fromtimestamp(ad["starts_ts"] - s, tz=timezone.utc), "R")
        for s in sorted(ad["reminders_secs"], reverse=True)
    ]
    e.add_field(name="Reminders", value="\n".join(sched_labels) or "none", inline=False)
    e.set_footer(text="Kongen & Kari's Helper • Paid Ad")
    return e


async def _post_paid_ad(interaction: discord.Interaction, user_id: str):
    s = paid_ad_sessions.get(user_id)
    if not s:
        return await interaction.response.send_message("Session expired.", ephemeral=True)

    missing = []
    if not s.get("reminder_channel_id"): missing.append("reminder channel")
    if not s.get("role_ids"):            missing.append("roles to ping")
    if not s.get("ad_channel_id"):       missing.append("ad channel")
    if not s.get("reminders_secs"):      missing.append("reminder schedule")
    if not s.get("bought"):              missing.append("ad details (click Fill Ad Details)")
    if missing:
        return await interaction.response.send_message(
            "Missing: " + ", ".join(missing), ephemeral=True)

    ad_id = str(int(_time.time() * 1000))
    ad = {
        "guild_id":           str(interaction.guild_id),
        "reminder_channel_id": s["reminder_channel_id"],
        "role_ids":            s["role_ids"],
        "ad_channel_id":       s["ad_channel_id"],
        "bought":              s["bought"],
        "paid":                s["paid"],
        "starts_ts":           s["starts_ts"],
        "ends_ts":             s["ends_ts"],
        "server":              s["server"],
        "reminders_secs":      s["reminders_secs"],
        "fired":               [],
    }
    paid_ads[ad_id] = ad
    _pa_save()
    paid_ad_sessions.pop(user_id, None)

    ch = interaction.guild.get_channel(int(s["reminder_channel_id"]))
    if ch:
        await ch.send(embed=_pa_embed(ad))

    await interaction.response.edit_message(
        content="Ad posted and reminders scheduled!", embed=None, view=None)


class PaidAdModal(discord.ui.Modal, title="Ad Details"):
    bought  = discord.ui.TextInput(label="Bought",  placeholder="e.g. 3 Days Nitro Ad",          max_length=100)
    paid    = discord.ui.TextInput(label="Paid",    placeholder="e.g. 35€",                       max_length=50)
    starts  = discord.ui.TextInput(label="Starts",  placeholder="e.g. June 8, 2026 18:00",        max_length=60)
    ends    = discord.ui.TextInput(label="Ends",    placeholder="e.g. June 11, 2026 18:00",       max_length=60)
    server  = discord.ui.TextInput(label="Server",  placeholder="e.g. https://discord.gg/…",      max_length=200)

    def __init__(self, user_id: str):
        super().__init__()
        self._uid = user_id
        s = paid_ad_sessions.get(user_id, {})
        if s.get("bought"):  self.bought.default = s["bought"]
        if s.get("paid"):    self.paid.default   = s["paid"]
        if s.get("server"):  self.server.default = s["server"]

    async def on_submit(self, interaction: discord.Interaction):
        s = paid_ad_sessions.setdefault(self._uid, {})

        starts_dt = _parse_ad_dt(self.starts.value)
        ends_dt   = _parse_ad_dt(self.ends.value)
        if not starts_dt or not ends_dt:
            return await interaction.response.send_message(
                "Could not parse dates. Use format: `June 8, 2026 18:00`", ephemeral=True)
        if ends_dt <= starts_dt:
            return await interaction.response.send_message(
                "End date must be after start date.", ephemeral=True)

        s["bought"]    = self.bought.value.strip()
        s["paid"]      = self.paid.value.strip()
        s["starts_ts"] = starts_dt.timestamp()
        s["ends_ts"]   = ends_dt.timestamp()
        s["server"]    = self.server.value.strip()

        view = PaidAdView(self._uid)
        await interaction.response.edit_message(
            content=_pa_wizard_text(s), view=view)


def _pa_wizard_text(s: dict) -> str:
    lines = ["**Set up Paid Ad Reminder**\n"]
    lines.append("**Reminder Channel:** " + (f"<#{s['reminder_channel_id']}>" if s.get("reminder_channel_id") else "not set"))
    lines.append("**Roles:** " + (" ".join(f"<@&{r}>" for r in s.get("role_ids", [])) or "not set"))
    lines.append("**Ad Channel:** " + (f"<#{s['ad_channel_id']}>" if s.get("ad_channel_id") else "not set"))
    lines.append("**Schedule:** " + (s.get("schedule_label", "not set")))
    if s.get("bought"):
        lines.append(f"\n**Bought:** {s['bought']}  |  **Paid:** {s.get('paid','')}")
        if s.get("starts_ts"):
            starts = datetime.fromtimestamp(s["starts_ts"], tz=timezone.utc)
            ends   = datetime.fromtimestamp(s["ends_ts"],   tz=timezone.utc)
            lines.append(f"**Starts:** {discord.utils.format_dt(starts, 'F')}  |  **Ends:** {discord.utils.format_dt(ends, 'F')}")
        lines.append(f"**Server:** {s.get('server','')}")
    return "\n".join(lines)


class PaidAdView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=600)
        self._uid = user_id
        self._add_selects()

    def _add_selects(self):
        self.clear_items()

        reminder_ch = discord.ui.ChannelSelect(
            placeholder="Select reminder channel (where embed & pings are posted)",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1, row=0,
        )
        reminder_ch.callback = self._reminder_channel_cb
        self.add_item(reminder_ch)

        role_sel = discord.ui.RoleSelect(
            placeholder="Select roles to ping",
            min_values=1, max_values=10, row=1,
        )
        role_sel.callback = self._role_cb
        self.add_item(role_sel)

        ad_ch = discord.ui.ChannelSelect(
            placeholder="Select ad channel (where you'll post the ad)",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1, row=2,
        )
        ad_ch.callback = self._ad_channel_cb
        self.add_item(ad_ch)

        sched_opts = [
            discord.SelectOption(label=label, value=str(i))
            for i, (label, _) in enumerate(PA_SCHEDULES)
        ]
        sched_sel = discord.ui.Select(
            placeholder="Select reminder schedule",
            options=sched_opts, min_values=1, max_values=1, row=3,
        )
        sched_sel.callback = self._schedule_cb
        self.add_item(sched_sel)

        fill_btn = discord.ui.Button(label="Fill Ad Details", style=discord.ButtonStyle.primary,
                                     emoji="📝", row=4)
        fill_btn.callback = self._fill_details_cb
        self.add_item(fill_btn)

        s = paid_ad_sessions.get(self._uid, {})
        ready = all([s.get("reminder_channel_id"), s.get("role_ids"),
                     s.get("ad_channel_id"), s.get("reminders_secs"), s.get("bought")])
        post_btn = discord.ui.Button(label="Post Ad & Set Reminders",
                                     style=discord.ButtonStyle.success, emoji="✅",
                                     disabled=not ready, row=4)
        post_btn.callback = self._post_cb
        self.add_item(post_btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger,
                                       emoji="✖", row=4)
        cancel_btn.callback = self._cancel_cb
        self.add_item(cancel_btn)

    async def _reminder_channel_cb(self, interaction: discord.Interaction):
        s = paid_ad_sessions.setdefault(self._uid, {})
        s["reminder_channel_id"] = str(interaction.data["values"][0])
        self._add_selects()
        await interaction.response.edit_message(content=_pa_wizard_text(s), view=self)

    async def _role_cb(self, interaction: discord.Interaction):
        s = paid_ad_sessions.setdefault(self._uid, {})
        s["role_ids"] = interaction.data["values"]
        self._add_selects()
        await interaction.response.edit_message(content=_pa_wizard_text(s), view=self)

    async def _ad_channel_cb(self, interaction: discord.Interaction):
        s = paid_ad_sessions.setdefault(self._uid, {})
        s["ad_channel_id"] = str(interaction.data["values"][0])
        self._add_selects()
        await interaction.response.edit_message(content=_pa_wizard_text(s), view=self)

    async def _schedule_cb(self, interaction: discord.Interaction):
        s = paid_ad_sessions.setdefault(self._uid, {})
        idx = int(interaction.data["values"][0])
        label, secs = PA_SCHEDULES[idx]
        s["reminders_secs"]  = secs
        s["schedule_label"]  = label
        self._add_selects()
        await interaction.response.edit_message(content=_pa_wizard_text(s), view=self)

    async def _fill_details_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PaidAdModal(self._uid))

    async def _post_cb(self, interaction: discord.Interaction):
        await _post_paid_ad(interaction, self._uid)

    async def _cancel_cb(self, interaction: discord.Interaction):
        paid_ad_sessions.pop(self._uid, None)
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


@tasks.loop(minutes=1)
async def paid_ad_checker():
    now = _time.time()
    changed = False
    for ad_id, ad in list(paid_ads.items()):
        starts = ad["starts_ts"]
        for secs in ad["reminders_secs"]:
            fire_at = starts - secs
            key = str(secs)
            if key not in ad["fired"] and now >= fire_at:
                await _fire_paid_ad_reminder(ad_id, ad, secs)
                ad["fired"].append(key)
                changed = True
    if changed:
        _pa_save()


@paid_ad_checker.before_loop
async def before_paid_ad_checker():
    await bot.wait_until_ready()


async def _fire_paid_ad_reminder(ad_id: str, ad: dict, secs_before: int):
    ch = bot.get_channel(int(ad["reminder_channel_id"]))
    if not ch:
        return
    starts = datetime.fromtimestamp(ad["starts_ts"], tz=timezone.utc)
    roles_mention = " ".join(f"<@&{r}>" for r in ad.get("role_ids", []))
    mins = secs_before // 60
    time_str = f"{mins} minute{'s' if mins != 1 else ''}" if mins < 60 else f"{secs_before // 3600} hour{'s' if secs_before // 3600 != 1 else ''}"
    content = f"{roles_mention}\n⏰ **Reminder!** Ad starts in **{time_str}** ({discord.utils.format_dt(starts, 'R')})"
    e = discord.Embed(title="📢 Time to Post Your Ad!", color=0xFF4500)
    e.add_field(name="Bought",      value=ad["bought"],                          inline=True)
    e.add_field(name="Paid",        value=ad["paid"],                            inline=True)
    e.add_field(name="​",      value="​",                              inline=True)
    e.add_field(name="Starts",      value=discord.utils.format_dt(starts, "F"), inline=True)
    e.add_field(name="Ad Channel",  value=f"<#{ad['ad_channel_id']}>",          inline=True)
    e.add_field(name="Server",      value=ad["server"],                          inline=True)
    e.set_footer(text="Kongen & Kari's Helper • Paid Ad Reminder")
    try:
        await ch.send(content=content, embed=e)
    except discord.HTTPException:
        pass


@app_commands.command(name="paidad", description="Set up a paid ad reminder (admin only)")
async def paidad_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    paid_ad_sessions[uid] = {}
    view = PaidAdView(uid)
    await interaction.response.send_message(
        content=_pa_wizard_text({}), view=view, ephemeral=True)

bot.tree.add_command(paidad_cmd)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BOT EVENTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return await bot.process_commands(message)
    uid      = str(message.author.id)
    guild_id = message.guild.id
    for counter in chat_counters.values():
        if counter.get("guild_id") == guild_id and not counter.get("counting_history", False):
            counter["counts"][uid] = counter["counts"].get(uid, 0) + 1
    gs = load_guild_settings().get(str(guild_id), {})
    if gs.get("enabled") and gs.get("command_channels"):
        if message.channel.id not in gs["command_channels"] and not _is_admin(message.author):
            if COMMAND_RE.search(message.content):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
            return
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
        return


@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"[JOIN] Joined guild: {guild.name} (ID: {guild.id}) | Members: {guild.member_count}")
    print(f"[JOIN] Owner: {guild.owner} | Region: {guild.preferred_locale}")
    print(f"[JOIN] Bot roles: {[r.name for r in guild.me.roles]}")
    print(f"[JOIN] Bot permissions: {guild.me.guild_permissions.value}")


@bot.event
async def on_guild_remove(guild: discord.Guild):
    print(f"[LEAVE] Removed from guild: {guild.name} (ID: {guild.id})")
    print(f"[LEAVE] This could mean: kicked, banned, or guild deleted")


@bot.event
async def on_ready():
    global chat_counters, giveaways
    if os.path.exists(CC_FILE):
        with open(CC_FILE) as f:
            chat_counters = json.load(f)
    giveaways = load_giveaways()
    print(f"Logged in as {bot.user}  (ID: {bot.user.id})")
    print(f"Loaded {len(chat_counters)} chat counter(s)")
    print(f"Loaded {len(giveaways)} giveaway(s)")

    for name, counter in chat_counters.items():
        last = counter.get("last_counted_at")
        after_dt = datetime.fromisoformat(last).replace(tzinfo=timezone.utc) if last else None
        asyncio.create_task(count_history(name, after_dt=after_dt))

    for gw_id, g in giveaways.items():
        if not g.get("ended"):
            bot.add_view(GiveawayView(gw_id))

    # Re-register persistent ticket views
    try:
        c = _tdb()
        all_cfgs = [dict(r) for r in c.execute("SELECT * FROM ticket_config").fetchall()]
        open_tix = [dict(r) for r in c.execute("SELECT id FROM tickets WHERE status='open'").fetchall()]
        c.close()
        for cfg_row in all_cfgs:
            pmid = cfg_row.get("panel_message_id")
            if not pmid:
                continue
            qs_rows  = _tq_all(cfg_row["guild_id"])
            prefixes = [r["prefix"] for r in qs_rows]
            if prefixes:
                bot.add_view(_make_panel_listener(prefixes), message_id=int(pmid))
        for t in open_tix:
            bot.add_view(TicketChannelView(t["id"]))
        print(f"Registered {len(open_tix)} ticket channel view(s) and {len(all_cfgs)} panel view(s)")
    except Exception as err:
        print(f"[Ticket startup] {err}")

    _pa_load()
    print(f"Loaded {len(paid_ads)} paid ad(s)")

    update_all_counters.start()
    giveaway_checker.start()
    paid_ad_checker.start()
    await _update_presence()

    for guild in GUILD_IDS:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash command(s) to guild {guild.id}")
        except discord.Forbidden:
            print(f"Bot not in guild {guild.id} — skipping")

    # Clear stale global commands after guild sync (removes /chatleaderboard and duplicates)
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("ERROR: DISCORD_TOKEN not set in .env")
    bot.run(token)
