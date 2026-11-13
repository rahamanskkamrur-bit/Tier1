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


class AppealPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit a Ban / Punishment Appeal", style=discord.ButtonStyle.danger, emoji="⚖️", custom_id="t1_app:open")
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AppealModal())


# =====================================================================
# 4. MODERATION LOG ACTION VIEW (UNMUTE & BAN BUTTONS)
# =====================================================================
class ModLogActionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def get_target_id(self, emb: discord.Embed):
        m = re.search(r"ID:\s*`(\d+)`", emb.description or "")
        return int(m.group(1)) if m else None

    @discord.ui.button(label="Remove Timeout", style=discord.ButtonStyle.success, emoji="🔓", custom_id="t1_mod:unmute")
    async def unmute_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        tid = self.get_target_id(interaction.message.embeds[0])
        member = interaction.guild.get_member(tid) if tid else None
        if member:
            await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
            await interaction.response.send_message(f"🔓 {interaction.user.mention} removed the timeout for {member.mention}.")
        else:
            await interaction.response.send_message("⚠️ Member not found in server.", ephemeral=True)

    @discord.ui.button(label="Ban Offender", style=discord.ButtonStyle.danger, emoji="🔨", custom_id="t1_mod:ban")
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        tid = self.get_target_id(interaction.message.embeds[0])
        if tid:
            await interaction.guild.ban(discord.Object(id=tid), reason=f"Banned via Mod-Log by {interaction.user}")
            await interaction.response.send_message(f"🔨 {interaction.user.mention} banned User ID `{tid}`.")


# =====================================================================
# 5. BOT CLASS, AUTO-MOD & SLASH COMMANDS
# =====================================================================
class Tier1SecurityBot(commands.Bot):
    def __init__(self, msg_intent: bool = True):
        intents = discord.Intents.default()
        intents.message_content = msg_intent
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())
        self.add_view(AppealPanelView())
        self.add_view(AppealModView())
        self.add_view(ModLogActionView())
        await self.tree.sync()

    async def on_ready(self):
        global BOT_STATUS
        BOT_STATUS = f"ONLINE as {self.user}"
        print(f"✅ [TIER 1] Security, Tickets & Appeal Bot ONLINE as {self.user}")

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await self.process_commands(message)

        if is_staff(message.author):
            return

        low = message.content.lower()
        triggered = next((w for w in BAD_WORDS if w in low), None)
        if not triggered and len(message.mentions) >= 5:
            triggered = "Mass Mention Spam (5+ pings)"

        if triggered:
            try:
                await message.delete()
            except Exception:
                pass
            timed_out = False
            try:
                until = discord.utils.utcnow() + datetime.timedelta(minutes=10)
                await message.author.timeout(until, reason=f"Auto-Mod: {triggered}")
                timed_out = True
            except Exception:
                pass

            warn_emb = discord.Embed(
                title="🛡️ Auto-Moderator Action",
                description=f"{message.author.mention}, your message was removed for triggering **`{triggered}`**." + ("\n⏱️ **10-minute timeout applied.**" if timed_out else ""),
                color=discord.Color.red(),
            )
            await message.channel.send(embed=warn_emb, delete_after=10)

            log_emb = discord.Embed(
                title="🚨 Auto-Mod Violation Logged",
                description=f"👤 **Offender:** {message.author.mention} (ID: `{message.author.id}`)\n📍 **Channel:** {message.channel.mention}\n⚠️ **Trigger:** `{triggered}`\n💬 **Message:** `{message.content[:300]}`",
                color=discord.Color.dark_red(),
            )
            await send_mod_log(message.guild, log_emb, ModLogActionView())


def register_commands(b: Tier1SecurityBot):
    # --- TICKETS (/panel, !panel) ---
    @b.tree.command(name="panel", description="Post the 4-button Server Support & Tickets panel")
    async def s_panel(interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        emb = discord.Embed(
            title="📬 Server Support & Tickets",
            description="Select a button below to open a private ticket:\n\n💬 **General Support**\n🚨 **Report a Member**\n⚖️ **Ban or Mute Appeal**\n🤝 **Partnership Request**",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=emb, view=TicketPanelView())

    @b.command(name="panel")
    async def p_panel(ctx: commands.Context):
        if not is_staff(ctx.author):
            return
        emb = discord.Embed(
            title="📬 Server Support & Tickets",
            description="Select a button below to open a private ticket:\n\n💬 **General Support**\n🚨 **Report a Member**\n⚖️ **Ban or Mute Appeal**\n🤝 **Partnership Request**",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=emb, view=TicketPanelView())

    # --- BAN APPEAL PANEL (/appealpanel, !appealpanel) ---
    @b.tree.command(name="appealpanel", description="Post the Strict AI Ban Appeal Center panel")
    async def s_appealpanel(interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        emb = discord.Embed(
            title=f"⚖️ {interaction.guild.name} — Strict AI Ban Appeal Center",
            description="Click below to submit a **Ban or Punishment Appeal**.\n\n1️⃣ **Be Honest & Detailed:** Short appeals or excuses are strictly denied by AI.\n2️⃣ **Strict AI Judge:** Instantly scores and judges your appeal.\n3️⃣ **Moderator Override:** Moderators review every appeal and can Approve/Unban or Deny.",
            color=discord.Color.dark_red(),
        )
        emb.set_image(url=APPEAL_BANNER_URL)
        await interaction.response.send_message(embed=emb, view=AppealPanelView())

    @b.command(name="appealpanel")
    async def p_appealpanel(ctx: commands.Context):
        if not is_staff(ctx.author):
            return
        emb = discord.Embed(
            title=f"⚖️ {ctx.guild.name} — Strict AI Ban Appeal Center",
            description="Click below to submit a **Ban or Punishment Appeal**.",
            color=discord.Color.dark_red(),
        )
        emb.set_image(url=APPEAL_BANNER_URL)
        await ctx.send(embed=emb, view=AppealPanelView())

    # --- MODERATION COMMANDS (/warn, /timeout, /untimeout, /purge, /kick, /ban) ---
    @b.tree.command(name="warn", description="Warn a member and log the warning")
    async def s_warn(interaction: discord.Interaction, member: discord.Member, reason: str):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        emb = discord.Embed(
            title="⚠️ Official Server Warning",
            description=f"👤 **Member:** {member.mention} (ID: `{member.id}`)\n🛡️ **Moderator:** {interaction.user.mention}\n📌 **Reason:** {reason}",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(content=member.mention, embed=emb)
        await send_mod_log(interaction.guild, emb)

    @b.tree.command(name="timeout", description="Timeout a member for a set number of minutes")
    async def s_timeout(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "Rule violation"):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        until = discord.utils.utcnow() + datetime.timedelta(minutes=max(1, min(minutes, 40320)))
        await member.timeout(until, reason=f"{reason} (by {interaction.user})")
        emb = discord.Embed(
            title="🔇 Member Timed Out",
            description=f"👤 **Member:** {member.mention} (ID: `{member.id}`)\n⏱️ **Duration:** `{minutes} minutes`\n🛡️ **Moderator:** {interaction.user.mention}\n📌 **Reason:** {reason}",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=emb)
        await send_mod_log(interaction.guild, emb, ModLogActionView())

    @b.tree.command(name="untimeout", description="Remove an active timeout from a member")
    async def s_untimeout(interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        await member.timeout(None, reason=f"Removed by {interaction.user}")
        emb = discord.Embed(
            title="🔓 Timeout Removed",
            description=f"👤 **Member:** {member.mention} (ID: `{member.id}`)\n🛡️ **Moderator:** {interaction.user.mention}",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=emb)
        await send_mod_log(interaction.guild, emb)

    @b.tree.command(name="purge", description="Delete a number of recent messages in this channel")
    async def s_purge(interaction: discord.Interaction, amount: int):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=max(1, min(amount, 100)))
        emb = discord.Embed(
            title="🧹 Channel Purged",
            description=f"📍 **Channel:** {interaction.channel.mention}\n🗑️ **Messages Deleted:** `{len(deleted)}`\n🛡️ **Moderator:** {interaction.user.mention}",
            color=discord.Color.blurple(),
        )
        await send_mod_log(interaction.guild, emb, ModLogActionView())
        await interaction.followup.send(f"🧹 Deleted **{len(deleted)}** messages!", ephemeral=True)

    @b.tree.command(name="kick", description="Kick a member from the server")
    async def s_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Rule violation"):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Moderators only.", ephemeral=True)
        await member.kick(reason=f"{reason} (by {interaction.user})")
        emb = discord.Embed(
            title="👢 Member Kicked",
            description=f"👤 **Member:** {member} (ID: `{member.id}`)\n🛡️ **Moderator:** {interaction.user.mention}\n📌 **Reason:** {reason}",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=emb)
        await send_mod_log(interaction.guild, emb)


async def main():
    await run_web_server()
    asyncio.create_task(self_ping_loop())
    if not BOT_TOKEN:
        print("❌ ERROR: Set BOT_TOKEN in Environment Variables!")
        while True:
            await asyncio.sleep(3600)

    bot = Tier1SecurityBot(True)
    register_commands(bot)
    try:
        await bot.start(BOT_TOKEN)
    except discord.PrivilegedIntentsRequired:
        await bot.close()
        bot = Tier1SecurityBot(False)
        register_commands(bot)
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
    
