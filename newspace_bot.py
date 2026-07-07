"""
NEWSPACE Telegram Bot — Powered by Groq (Free)
================================================
Forward newspaper PDFs to @Sarthnews_Bot
Bot extracts text, Groq AI summarizes top stories,
and updates your NEWSPACE website automatically.

Install:
  pip install python-telegram-bot PyMuPDF requests groq
  python newspace_bot.py
"""

import os
import json
import re
import tempfile
import requests
import fitz  # PyMuPDF
from groq import Groq
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from datetime import datetime, timezone, timedelta

# ── CONFIG ──────────────────────────────────────────────────────────────────
BOT_TOKEN    = "8974294866:AAHDCzLcm9jqZ56j6MFGsvmcmplSbPZMFkU"
GROQ_API_KEY = "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"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "heisenberg678/Finance"
GITHUB_FILE  = "index.html"

IST = timezone(timedelta(hours=5, minutes=30))
todays_summaries = []

# ────────────────────────────────────────────────────────────────────────────
# 1. PDF TEXT EXTRACTION
# ────────────────────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    text = ""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            text += page.get_text()
    return text.strip()

# ────────────────────────────────────────────────────────────────────────────
# 2. GROQ SUMMARIZATION (Free)
# ────────────────────────────────────────────────────────────────────────────

def summarize_newspaper(text: str, newspaper_name: str) -> list:
    client = Groq(api_key=GROQ_API_KEY)

    prompt = f"""You are NEWSPACE, a finance news summarizer for Indian finance students.

Below is text from today's {newspaper_name} newspaper.

Find the TOP 5 most important finance/economy/market stories and return ONLY a valid JSON array like this:
[
  {{
    "title": "Short headline max 12 words",
    "category": "Indian Finance",
    "summary": "2-3 sentences: what happened and why it matters for India.",
    "takeaway": "One key finance concept this story illustrates.",
    "source": "{newspaper_name}"
  }}
]

Categories must be one of: Indian Finance, Stock Market, Global Markets, World News, Banking, Economy

Return ONLY the JSON array. No extra text. No markdown. No backticks.

NEWSPAPER TEXT:
{text[:8000]}"""

    response = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.3,
    )

    raw = response.choices[0].message.content.strip()

    # Clean markdown if present
    raw = re.sub(r"```json|```", "", raw).strip()

    articles = json.loads(raw)
    return articles[:5]

# ────────────────────────────────────────────────────────────────────────────
# 3. GITHUB SITE UPDATER
# ────────────────────────────────────────────────────────────────────────────

def update_github_site(articles: list) -> bool:
    if not GITHUB_TOKEN:
        print("[WARN] No GITHUB_TOKEN — skipping site update")
        return False

    import base64

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print(f"[ERROR] Could not fetch file: {r.status_code}")
        return False

    data = r.json()
    sha  = data["sha"]
    html = base64.b64decode(data["content"]).decode("utf-8")

    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

    tag_colors = {
        "Indian Finance":  ("#dbeafe", "#1d4ed8"),
        "Stock Market":    ("#dcfce7", "#15803d"),
        "Global Markets":  ("#ede9fe", "#6d28d9"),
        "World News":      ("#fef9c3", "#a16207"),
        "Banking":         ("#dbeafe", "#1d4ed8"),
        "Economy":         ("#dcfce7", "#15803d"),
    }

    cards_html = f'<!-- AUTO:BOT_ARTICLES_START -->\n'
    cards_html += f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px;font-size:12px;color:#166534;margin-bottom:12px;">✅ Updated from newspaper · {now}</div>\n'

    for i, art in enumerate(articles):
        cat = art.get("category", "Indian Finance")
        bg, color = tag_colors.get(cat, ("#dbeafe", "#1d4ed8"))
        featured = "featured" if i == 0 else ""
        cards_html += f"""<div class="ncard {featured}">
  <div class="nc-meta">
    <span class="tag" style="background:{bg};color:{color}">{cat}</span>
    <span class="nc-source">{art.get('source','Newspaper')}</span>
    <span class="nc-sep">·</span>
    <span class="nc-time">Today</span>
  </div>
  <div class="nc-title">{art.get('title','')}</div>
  <div class="nc-desc">{art.get('summary','')}</div>
  <div style="margin-top:8px;padding:8px 10px;background:#eff6ff;border-radius:6px;font-size:12px;color:#1d4ed8;">
    📌 {art.get('takeaway','')}
  </div>
</div>\n"""

    cards_html += '<!-- AUTO:BOT_ARTICLES_END -->'

    if "AUTO:BOT_ARTICLES_START" in html:
        html = re.sub(
            r'<!-- AUTO:BOT_ARTICLES_START -->.*?<!-- AUTO:BOT_ARTICLES_END -->',
            cards_html, html, flags=re.DOTALL
        )
    else:
        html = html.replace('<div id="news-feed">', f'<div id="news-feed">\n{cards_html}\n')

    encoded = base64.b64encode(html.encode()).decode()
    payload = {
        "message": f"NEWSPACE Bot update: {now}",
        "content": encoded,
        "sha": sha
    }
    r2 = requests.put(url, headers=headers, json=payload)
    return r2.status_code in (200, 201)

# ────────────────────────────────────────────────────────────────────────────
# 4. TELEGRAM HANDLERS
# ────────────────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *NEWSPACE Bot is ready!*\n\n"
        "Send or forward any newspaper PDF and I will:\n"
        "1️⃣ Extract all the text\n"
        "2️⃣ Find top 5 finance stories\n"
        "3️⃣ Summarize them with AI\n"
        "4️⃣ Update your NEWSPACE website\n\n"
        "Just send a PDF now to try it!",
        parse_mode="Markdown"
    )

async def handle_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global todays_summaries

    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Please send a PDF file.")
        return

    newspaper_name = doc.file_name.replace(".pdf", "").replace("_", " ").replace("-", " ").title()
    msg = await update.message.reply_text(
        f"📰 Received *{newspaper_name}*\nExtracting text...",
        parse_mode="Markdown"
    )

    # Download PDF
    try:
        file = await ctx.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            with open(tmp.name, "rb") as f:
                pdf_bytes = f.read()
    except Exception as e:
        await msg.edit_text(f"❌ Could not download PDF: {e}")
        return

    # Extract text
    try:
        text = extract_text_from_pdf(pdf_bytes)
        if len(text) < 200:
            await msg.edit_text(
                "⚠️ Could not extract text from this PDF.\n"
                "Make sure it is a text-based PDF (not a scanned image)."
            )
            return
        await msg.edit_text(
            f"✅ Extracted {len(text):,} characters\n"
            f"🤖 Summarizing with Groq AI..."
        )
    except Exception as e:
        await msg.edit_text(f"❌ PDF extraction failed: {e}")
        return

    # Summarize
    try:
        articles = summarize_newspaper(text, newspaper_name)
        todays_summaries.extend(articles)
    except Exception as e:
        await msg.edit_text(f"❌ AI summarization failed: {e}\n\nTry again in a moment.")
        return

    # Send summaries back to user
    reply = f"✅ *Top {len(articles)} stories from {newspaper_name}:*\n\n"
    for i, art in enumerate(articles, 1):
        reply += f"*{i}. {art.get('title','')}*\n"
        reply += f"_{art.get('summary','')}_\n"
        reply += f"📌 _{art.get('takeaway','')}_\n\n"

    # Telegram has 4096 char limit — split if needed
    if len(reply) > 4000:
        reply = reply[:3990] + "..."

    await msg.edit_text(reply, parse_mode="Markdown")

    # Update GitHub site
    if GITHUB_TOKEN:
        updating_msg = await update.message.reply_text("🌐 Updating NEWSPACE website...")
        success = update_github_site(todays_summaries)
        if success:
            await updating_msg.edit_text(
                "✅ *NEWSPACE website updated!*\n"
                "Your site will refresh in ~60 seconds via Netlify.",
                parse_mode="Markdown"
            )
        else:
            await updating_msg.edit_text(
                "⚠️ Summaries done but site update failed.\n"
                "Check your GITHUB_TOKEN."
            )
    else:
        await update.message.reply_text(
            "📋 Done! Set GITHUB_TOKEN to also auto-update your website."
        )

async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    await update.message.reply_text(
        f"📊 *NEWSPACE Bot Status*\n\n"
        f"🕐 Time: {now}\n"
        f"📰 Articles today: {len(todays_summaries)}\n"
        f"🤖 Groq AI: ✅ Ready (Free)\n"
        f"🌐 GitHub: {'✅ Ready' if GITHUB_TOKEN else '⚠️ Token missing'}",
        parse_mode="Markdown"
    )

async def clear_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global todays_summaries
    todays_summaries = []
    await update.message.reply_text("🗑️ Cleared. Send new PDFs to start fresh.")

# ────────────────────────────────────────────────────────────────────────────
# 5. MAIN
# ────────────────────────────────────────────────────────────────────────────

def main():
    print("[NEWSPACE Bot] Starting @Sarthnews_Bot...")
    print(f"  Groq AI:  ✅ Free & Ready")
    print(f"  GitHub:   {'✅ Ready' if GITHUB_TOKEN else '⚠️ Set GITHUB_TOKEN to enable site updates'}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("clear",  clear_cmd))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    print("[NEWSPACE Bot] Running! Send a PDF to @Sarthnews_Bot on Telegram")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
