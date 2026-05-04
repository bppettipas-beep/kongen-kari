import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import json
import random
import re
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
        if not settings.get("enabled") or not settings.get("command_channels"):
            return True
        if interaction.channel_id in settings["command_channels"]:
            return True
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
DATA_DIR = os.getenv("DATA_DIR", ".")
LB_FILE  = os.path.join(DATA_DIR, "leaderboards.json")
CC_FILE  = os.path.join(DATA_DIR, "chat_counters.json")
GW_FILE  = os.path.join(DATA_DIR, "giveaways.json")
GS_FILE  = os.path.join(DATA_DIR, "guild_settings.json")

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

lb_sessions:        dict[str, dict] = {}
emb_sessions:       dict[str, dict] = {}
cc_sessions:        dict[str, dict] = {}
chat_counters:      dict[str, dict] = {}
currently_counting: set[str]        = set()
giveaways:          dict[str, dict] = {}


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
            f"**Footer:** {s.get('footer_text', 'Kongen & Kari\'s Hangout')}"
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

commandblock = app_commands.Group(
    name="commandblock",
    description="Restrict which channels commands can be used in",
    default_permissions=discord.Permissions(administrator=True),
)

@commandblock.command(name="add", description="Allow commands in a channel")
@app_commands.describe(channel="Channel to allow commands in")
async def cb_add(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = str(interaction.guild_id)
    data = load_guild_settings()
    gs = data.setdefault(gid, {"enabled": True, "command_channels": []})
    if channel.id not in gs["command_channels"]:
        gs["command_channels"].append(channel.id)
        gs["enabled"] = True
    save_guild_settings(data)
    channels = gs["command_channels"]
    mentions = " ".join(f"<#{c}>" for c in channels)
    await interaction.response.send_message(
        f"Commands are now restricted to: {mentions}", ephemeral=True
    )

@commandblock.command(name="remove", description="Remove a channel from the allowed list")
@app_commands.describe(channel="Channel to remove from the allowed list")
async def cb_remove(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = str(interaction.guild_id)
    data = load_guild_settings()
    gs = data.get(gid, {})
    channels: list = gs.get("command_channels", [])
    if channel.id in channels:
        channels.remove(channel.id)
        gs["command_channels"] = channels
        data[gid] = gs
        save_guild_settings(data)
    if channels:
        mentions = " ".join(f"<#{c}>" for c in channels)
        await interaction.response.send_message(
            f"Removed. Commands are now restricted to: {mentions}", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "No allowed channels left — restrictions are now effectively off. Use `/commandblock clear` to disable.", ephemeral=True
        )

@commandblock.command(name="list", description="Show the current channel allowlist")
async def cb_list(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    gs = load_guild_settings().get(gid, {})
    channels: list = gs.get("command_channels", [])
    if not gs.get("enabled") or not channels:
        await interaction.response.send_message("No channel restrictions are active.", ephemeral=True)
        return
    mentions = "\n".join(f"• <#{c}>" for c in channels)
    embed = discord.Embed(
        title="Command Channel Allowlist",
        description=f"Commands are only usable in:\n{mentions}",
        color=GOLD,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@commandblock.command(name="clear", description="Remove all channel restrictions — allow commands everywhere")
async def cb_clear(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    data = load_guild_settings()
    data.pop(gid, None)
    save_guild_settings(data)
    await interaction.response.send_message(
        "Channel restrictions cleared. Commands are now allowed everywhere.", ephemeral=True
    )

bot.tree.add_command(commandblock)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SLASH COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.tree.command(name="leaderboard", description="Create a leaderboard")
@app_commands.default_permissions(administrator=True)
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
@app_commands.default_permissions(administrator=True)
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


@bot.tree.command(name="embed", description="Build and post a fully custom embed")
@app_commands.default_permissions(administrator=True)
async def embed_cmd(interaction: discord.Interaction):
    sid = f"{interaction.user.id}_{interaction.id}"
    emb_sessions[sid] = _blank_emb(interaction.user.id)
    await interaction.response.send_message(
        embed=build_emb_panel_embed(emb_sessions[sid]),
        view=EmbedPanel(sid, interaction.user.id),
        ephemeral=True,
    )


@bot.tree.command(name="giveaway", description="Start a giveaway in this channel")
@app_commands.default_permissions(administrator=True)
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
@app_commands.default_permissions(administrator=True)
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
@app_commands.default_permissions(administrator=True)
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
    if not _is_admin(message.author):
        gs = load_guild_settings().get(str(guild_id), {})
        if gs.get("enabled") and gs.get("command_channels"):
            if message.channel.id not in gs["command_channels"]:
                return
    await bot.process_commands(message)


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

    update_all_counters.start()
    giveaway_checker.start()
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
