import asyncio
import datetime
import json
import os
import re
import time
import aiohttp
from aiohttp import web
import discord
from discord.ext import commands

# =====================================================================
# TIER 1 CONFIGURATION (MODERATION + TICKETS + AI BAN APPEAL)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

STAFF_ROLE_IDS = [1567026563369664590]
TICKET_CATEGORY_ID = 1567026565483859991
APPEAL_CATEGORY_ID = 1567026565483859991
MOD_LOG_CHANNEL_ID = 1567026568423931975
APPEAL_BANNER_URL = "https://images.unsplash.com/photo-1589829545856-d10d557cf95f?auto=format&fit=crop&w=1200&q=80"

START_TIME = time.time()
BOT_STATUS = "Starting..."
BAD_WORDS = ["discord.gg/", "discord.com/invite/", "free nitro", "steamcommunnity", "dox", "ddos", "token grab", "nuke"]

TICKET_TYPES = {
    "support": ("General Support", "support", discord.Color.blurple(), "Please describe what you need help with, and staff will assist you shortly."),
    "report": ("Report a Member", "report", discord.Color.red(), "Please provide the username/ID of the member you are reporting and screenshots/evidence."),
    "appeal": ("Ban or Mute Appeal", "appeal", discord.Color.gold(), "Please share why you were muted/banned and why the punishment should be lifted."),
    "partnership": ("Partnership Request", "partner", discord.Color.green(), "Please share your server details, member count, and partnership request."),
}


def is_staff(m: discord.Member) -> bool:
    if not isinstance(m, discord.Member):
        return False
    return (
        m.guild_permissions.administrator
        or m.guild_permissions.moderate_members
        or m.guild_permissions.ban_members
        or any(r.id in STAFF_ROLE_IDS for r in m.roles)
    )


async def send_mod_log(guild: discord.Guild, embed: discord.Embed, view: discord.ui.View = None):
    if not guild:
        return
    ch = guild.get_channel(MOD_LOG_CHANNEL_ID)
    if ch:
        try:
            await ch.send(embed=embed, view=view)
        except Exception:
            pass


# =====================================================================
# 1. 24/7 KEEP-ALIVE WEB SERVER & AUTO-PINGER
# =====================================================================
async def handle_ping(req):
    up = int((time.time() - START_TIME) // 60)
    return web.Response(text=f"Tier 1 Security Bot 24/7 | Uptime: {up}m | Status: {BOT_STATUS}")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    try:
        await web.TCPSite(runner, "0.0.0.0", port).start()
    except Exception:
        pass


async def self_ping_loop():
    await asyncio.sleep(20)
    url = os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/"
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(url, timeout=10):
                    pass
            except Exception:
                pass
            await asyncio.sleep(240)


# =====================================================================
# 2. TICKET SYSTEM VIEWS (/panel)
# =====================================================================
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, emoji="🙋‍♂️", custom_id="t1_tk:claim")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Only staff can claim tickets.", ephemeral=True)
        emb = interaction.message.embeds[0]
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        button.style = discord.ButtonStyle.secondary
        emb.set_field_at(1, name="Claimed By", value=interaction.user.mention, inline=True)
        await interaction.response.edit_message(embed=emb, view=self)
        await interaction.channel.send(f"✅ {interaction.user.mention} has claimed this ticket!")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="t1_tk:close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"🔒 Ticket closed by {interaction.user.mention}. Deleting in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def open_ticket(self, interaction: discord.Interaction, key: str):
        await interaction.response.defer(ephemeral=True)
        guild, user = interaction.guild, interaction.user
        title, prefix, color, welcome = TICKET_TYPES[key]

        for ch in guild.text_channels:
            if ch.topic and f"UserID:{user.id}" in ch.topic and f"Type:{key}" in ch.topic:
                return await interaction.followup.send(f"⚠️ You already have an open **{title}** ticket: {ch.mention}", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, embed_links=True),
        }
        pings = []
        for rid in STAFF_ROLE_IDS:
            r = guild.get_role(rid)
            if r:
                overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                pings.append(r.mention)

        cat = guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(cat, discord.CategoryChannel):
            cat = None

        ch = await guild.create_text_channel(
            name=f"{prefix}-{user.name.lower()[:18]}",
            category=cat,
            overwrites=overwrites,
            topic=f"Ticket | Type:{key} | UserID:{user.id}",
        )
        emb = discord.Embed(title=f"🎫 {title}", description=f"Welcome {user.mention}!\n\n{welcome}", color=color)
        emb.add_field(name="Opened By", value=user.mention, inline=True)
        emb.add_field(name="Claimed By", value="Unclaimed", inline=True)
        await ch.send(content=f"{user.mention} {' '.join(pings)}".strip(), embed=emb, view=TicketControlView())
        await interaction.followup.send(f"✅ Ticket created: {ch.mention}", ephemeral=True)

    @discord.ui.button(label="General Support", style=discord.ButtonStyle.primary, emoji="💬", custom_id="t1_p:sup", row=0)
    async def sup_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.open_ticket(interaction, "support")

    @discord.ui.button(label="Report a Member", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="t1_p:rep", row=0)
    async def rep_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.open_ticket(interaction, "report")

    @discord.ui.button(label="Ban or Mute Appeal", style=discord.ButtonStyle.secondary, emoji="⚖️", custom_id="t1_p:app", row=1)
    async def app_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.open_ticket(interaction, "appeal")

    @discord.ui.button(label="Partnership Request", style=discord.ButtonStyle.success, emoji="🤝", custom_id="t1_p:par", row=1)
    async def par_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.open_ticket(interaction, "partnership")


# =====================================================================
# 3. STRICT AI BAN APPEAL SYSTEM (/appealpanel)
# =====================================================================
def strict_heuristic_judge(reason: str, expl: str, promise: str) -> dict:
    text = f"{reason} {expl} {promise}".lower()
    words = len(f"{expl} {promise}".split())
    for w in ["dox", "nsfw", "gore", "raid", "scam", "nuke", "token", "ddos", "slur"]:
        if w in text:
            return {"verdict": "DENIED", "score": 10, "summary": f"Auto-Denial: Severe violation ('{w}').", "analysis": "Zero-tolerance violations are strictly denied unless a Moderator overrides.", "action": "Keep Banned"}
    if words < 35:
        return {"verdict": "DENIED", "score": 20, "summary": "Auto-Denial: Low-effort appeal.", "analysis": f"Only {words} words provided. Strict appeals require detailed accountability.", "action": "Reject (Low Effort)"}
    for ex in ["my brother", "my friend", "my dog", "wasn't me", "corrupt", "joke", "prank", "idk"]:
        if ex in text:
            return {"verdict": "DENIED", "score": 30, "summary": "Denied: Making excuses / shifting blame.", "analysis": f"Detected excuse ('{ex}'). You are responsible for your account.", "action": "Keep Punished"}
    hits = sum(1 for w in ["apologize", "sorry", "responsibility", "my fault", "mistake", "understand", "rules", "never again", "promise"] if w in text)
    if hits >= 3 and words >= 50:
        return {"verdict": "APPROVED", "score": min(92, 65 + hits * 5), "summary": "Conditionally Approved: High effort & remorse.", "analysis": "Thorough explanation and clear accountability shown.", "action": "Approve on Probation"}
    return {"verdict": "DENIED", "score": 45, "summary": "Denied: Insufficient remorse shown.", "analysis": "Did not show enough accountability or guarantee of future rule compliance.", "action": "Deny Appeal"}


async def evaluate_appeal(acc: str, reason: str, expl: str, promise: str) -> dict:
    if not GEMINI_API_KEY:
        return strict_heuristic_judge(reason, expl, promise)
    prompt = f"Account: {acc}\nReason: {reason}\nExplanation: {expl}\nPromise: {promise}"
    sys_inst = "You are a strict Ban Appeal Judge. Default to DENIED unless strong remorse and detail are shown. Return JSON with keys: verdict ('DENIED' or 'APPROVED'), score (1-100), summary, analysis, action."
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}], "systemInstruction": {"parts": [{"text": sys_inst}]}, "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, timeout=15) as r:
                if r.status == 200:
                    d = json.loads((await r.json())["candidates"][0]["content"]["parts"][0]["text"])
                    return {"verdict": str(d.get("verdict", "DENIED")).upper(), "score": int(d.get("score", 40)), "summary": d.get("summary", "Evaluated."), "analysis": d.get("analysis", "Strict review complete."), "action": d.get("action", "Mod Review")}
    except Exception:
        pass
    return strict_heuristic_judge(reason, expl, promise)


class AppealOverrideModal(discord.ui.Modal):
    def __init__(self, verdict: str, view_obj: "AppealModView"):
        super().__init__(title=f"Moderator Override: {verdict}")
        self.verdict = verdict
        self.view_obj = view_obj
        self.note = discord.ui.TextInput(label="Moderator Reason / Note", style=discord.TextStyle.paragraph, required=True, max_length=400)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        await self.view_obj.apply_decision(interaction, self.verdict, self.note.value)


class AppealModView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def apply_decision(self, interaction: discord.Interaction, verdict: str, note: str):
        await interaction.response.defer()
        msg = interaction.message
        if not msg or not msg.embeds:
            return
        emb = msg.embeds[0]
        ok = verdict == "APPROVED"
        emb.color = discord.Color.green() if ok else discord.Color.dark_red()
        status = f"✅ OVERRIDDEN & APPROVED BY {interaction.user.display_name}" if ok else f"🚫 OVERRIDDEN & DENIED BY {interaction.user.display_name}"
        emb.set_field_at(len(emb.fields) - 1, name="⚖️ Final Decision (Mod Override)", value=f"**{status}**\n**Note:** {note}", inline=False)

        topic = interaction.channel.topic or ""
        app_m = re.search(r"UserID:(\d+)", topic)
        tgt_m = re.search(r"TargetID:(\d+)", topic)
        app_id = int(app_m.group(1)) if app_m else None
        tgt_id = int(tgt_m.group(1)) if tgt_m else app_id

        unban_msg = ""
        if ok and tgt_id:
            try:
                await interaction.guild.unban(discord.Object(id=tgt_id), reason=f"Appeal Approved by {interaction.user}")
                unban_msg = f"\n🔓 **Auto-Unbanned ID:** `{tgt_id}`"
            except Exception:
                unban_msg = f"\nℹ️ *User ID `{tgt_id}` was not on the server ban list.*"

        await msg.edit(embed=emb, view=self)
        ping = f"<@{app_id}>" if app_id else "Applicant"
        await interaction.channel.send(f"{'🎉 **APPEAL APPROVED**' if ok else '🛑 **APPEAL DENIED**'} by {interaction.user.mention} for {ping}!\n> **Mod Note:** {note}{unban_msg}")
        await send_mod_log(interaction.guild, emb)

    @discord.ui.button(label="Override: Approve & Unban", style=discord.ButtonStyle.success, emoji="✅", custom_id="t1_app:approve", row=0)
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        await interaction.response.send_modal(AppealOverrideModal("APPROVED", self))

    @discord.ui.button(label="Override: Deny Appeal", style=discord.ButtonStyle.danger, emoji="🚫", custom_id="t1_app:deny", row=0)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        await interaction.response.send_modal(AppealOverrideModal("DENIED", self))

    @discord.ui.button(label="Re-Run Strict AI Judge", style=discord.ButtonStyle.secondary, emoji="🤖", custom_id="t1_app:rejudge", row=1)
    async def rejudge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        emb = interaction.message.embeds[0]
        f = {field.name: field.value for field in emb.fields}
        res = await evaluate_appeal(f.get("👤 Account / ID", ""), f.get("📌 Ban Reason", ""), f.get("📝 Explanation", ""), f.get("🤝 Promise", ""))
        ok = res["verdict"] == "APPROVED"
        emb.color = discord.Color.gold() if ok else discord.Color.red()
        hdr = "🟢 AI: APPROVE" if ok else "🔴 AI: STRICTLY DENIED"
        emb.set_field_at(4, name="🤖 Strict AI Judge Verdict (Re-Run)", value=f"**{hdr}** (`{res['score']}/100`)\n**Summary:** {res['summary']}\n**Analysis:** {res['analysis']}\n**Action:** `{res['action']}`", inline=False)
        await interaction.message.edit(embed=emb, view=self)
        await interaction.followup.send("✅ Re-evaluated by Strict AI.", ephemeral=True)

    @discord.ui.button(label="Close Appeal Ticket", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="t1_app:close", row=1)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        await interaction.response.send_message("🔒 Closing appeal ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()


class AppealModal(discord.ui.Modal, title="⚖️ Official Ban / Punishment Appeal"):
    def __init__(self):
        super().__init__()
        self.acc = discord.ui.TextInput(label="Your Username & Discord/Game ID", required=True, max_length=100)
        self.reason = discord.ui.TextInput(label="What were you banned/punished for?", required=True, max_length=200)
        self.expl = discord.ui.TextInput(label="Detailed Explanation & Apology", style=discord.TextStyle.paragraph, required=True, min_length=20, max_length=900)
        self.prom = discord.ui.TextInput(label="Why unban you & how will you follow rules?", style=discord.TextStyle.paragraph, required=True, min_length=20, max_length=700)
        for item in (self.acc, self.reason, self.expl, self.prom):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild, user = interaction.guild, interaction.user
        for ch in guild.text_channels:
            if ch.topic and f"AppealTicket | UserID:{user.id}" in ch.topic:
                return await interaction.followup.send(f"⚠️ You already have an open appeal: {ch.mention}", ephemeral=True)

        m = re.search(r"\b(\d{17,20})\b", self.acc.value)
        tgt_id = m.group(1) if m else str(user.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, embed_links=True),
        }
        pings = []
        for rid in STAFF_ROLE_IDS:
            r = guild.get_role(rid)
            if r:
                overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                pings.append(r.mention)

        cat = guild.get_channel(APPEAL_CATEGORY_ID)
        if not isinstance(cat, discord.CategoryChannel):
            cat = None
        ch = await guild.create_text_channel(
            name=f"appeal-{user.name.lower()[:18]}",
            category=cat,
            overwrites=overwrites,
            topic=f"AppealTicket | UserID:{user.id} | TargetID:{tgt_id}",
        )

        res = await evaluate_appeal(self.acc.value, self.reason.value, self.expl.value, self.prom.value)
        ok = res["verdict"] == "APPROVED"
        hdr = "🟢 AI RECOMMENDED: APPROVE" if ok else "🔴 AI VERDICT: STRICTLY DENIED"

        emb = discord.Embed(
            title="⚖️ Ban Appeal Submitted",
            description=f"Applicant: {user.mention} (`{user.id}`)\nEvaluated by **Strict AI Judge**. Moderators can override below.",
            color=discord.Color.gold() if ok else discord.Color.red(),
        )
        emb.add_field(name="👤 Account / ID", value=self.acc.value, inline=True)
        emb.add_field(name="📌 Ban Reason", value=self.reason.value, inline=True)
        emb.add_field(name="📝 Explanation", value=self.expl.value, inline=False)
        emb.add_field(name="🤝 Promise", value=self.prom.value, inline=False)
        emb.add_field(name="🤖 Strict AI Judge Verdict", value=f"**{hdr}** (`{res['score']}/100`)\n**Summary:** {res['summary']}\n**Analysis:** {res['analysis']}\n**Action:** `{res['action']}`", inline=False)
        emb.add_field(name="⚖️ Final Decision", value="⏳ Pending Moderator Review / Override", inline=False)

        await ch.send(content=f"{user.mention} {' '.join(pings)}".strip(), embed=emb, view=AppealModView())
        await interaction.followup.send(f"✅ Appeal submitted in {ch.mention}!", ephemeral=True)


class AppealPanelView(discord.ui.Vi
