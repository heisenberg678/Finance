"""
NEWSPACE Telegram Bot
@Sarthnews_Bot
"""

import os
import json
import re
import tempfile
import requests
import fitz
import base64
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.error import Conflict, NetworkError
from datetime import datetime, timezone, timedelta

BOT_TOKEN    = "8974294866:AAHDCzLcm9jqZ56j6MFGsvmcmplSbPZMFkU"
GROQ_API_KEY = "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"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "heisenberg678/Finance"
GITHUB_FILE  = "index.html"

IST = timezone(timedelta(hours=5, minutes=30))
todays_summaries = []


def extract_text_from_pdf(pdf_bytes):
    text = ""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        total_pages = len(doc)
        print(f"[DEBUG] PDF has {total_pages} pages")
        for page in doc:
            text += page.get_text()
    print(f"[DEBUG] Extracted {len(text):,} characters")
    return text.strip()


def chunk_text(text, chunk_size=4000):
    """Split text into chunks for processing."""
    chunks = []
    words = text.split()
    current = []
    current_len = 0
    for word in words:
        current.append(word)
        current_len += len(word) + 1
        if current_len >= chunk_size:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
    if current:
        chunks.append(" ".join(current))
    return chunks


def summarize_chunk(chunk, newspaper_name, chunk_num):
    """Summarize one chunk of newspaper text."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = f"""You are a newspaper editor. Read this section of {newspaper_name} and extract ALL news stories you can find.
For each story return a JSON object with:
- title (clear headline, max 12 words)
- category (one of: Indian Finance, Stock Market, Global Markets, World News, Banking, Economy, Politics, Business)
- summary (2-3 clear sentences explaining what happened)
- takeaway (1 sentence key insight for a finance student)
- source (use "{newspaper_name}")

Return ONLY a valid JSON array. No extra text. Include as many stories as you find (aim for 8-12 per section).

NEWSPAPER TEXT SECTION {chunk_num}:
{chunk}"""

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 3000,
        "temperature": 0.2
    }

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code != 200:
        print(f"[WARN] Chunk {chunk_num} failed: {r.status_code}")
        return []

    raw = r.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        raw = match.group()
    try:
        return json.loads(raw)
    except:
        return []


def summarize_newspaper(text, newspaper_name):
    """Process entire newspaper and return ALL articles."""
    # Split into chunks of 4000 chars each
    chunks = chunk_text(text, 4000)
    print(f"[DEBUG] Processing {len(chunks)} chunks from newspaper")

    all_articles = []
    # Process up to 8 chunks (covers most newspapers)
    for i, chunk in enumerate(chunks[:8]):
        print(f"[DEBUG] Processing chunk {i+1}/{min(len(chunks), 8)}...")
        articles = summarize_chunk(chunk, newspaper_name, i+1)
        all_articles.extend(articles)
        print(f"[DEBUG] Found {len(articles)} articles in chunk {i+1}")

    # Remove duplicates by title
    seen_titles = set()
    unique_articles = []
    for art in all_articles:
        title = art.get("title", "").lower().strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            unique_articles.append(art)

    print(f"[DEBUG] Total unique articles: {len(unique_articles)}")
    return unique_articles


def update_github_site(articles):
    if not GITHUB_TOKEN:
        print("[ERROR] No GITHUB_TOKEN")
        return False

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    r = requests.get(url, headers=headers)
    print(f"[DEBUG] GET status: {r.status_code}")
    if r.status_code != 200:
        print(f"[ERROR] {r.text[:200]}")
        return False

    data = r.json()
    sha  = data["sha"]
    html = base64.b64decode(data["content"]).decode("utf-8")
    now  = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

    print(f"[DEBUG] HTML length: {len(html)}")
    print(f"[DEBUG] Markers found: {'AUTO:BOT_ARTICLES_START' in html}")

    tag_colors = {
        "Indian Finance":  ("#dbeafe", "#1d4ed8"),
        "Stock Market":    ("#dcfce7", "#15803d"),
        "Global Markets":  ("#ede9fe", "#6d28d9"),
        "World News":      ("#fef9c3", "#a16207"),
        "Banking":         ("#dbeafe", "#1d4ed8"),
        "Economy":         ("#dcfce7", "#15803d"),
    }

    cards = f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px;font-size:12px;color:#166534;margin-bottom:12px;">✅ Live from newspaper · {now}</div>\n'

    for i, art in enumerate(articles):
        cat = art.get("category", "Indian Finance")
        bg, color = tag_colors.get(cat, ("#dbeafe", "#1d4ed8"))
        featured = "featured" if i == 0 else ""
        cards += f'<div class="ncard {featured}">\n'
        cards += f'<div class="nc-meta"><span class="tag" style="background:{bg};color:{color}">{cat}</span><span class="nc-source">{art.get("source","Newspaper")}</span><span class="nc-sep">·</span><span class="nc-time">Today</span></div>\n'
        cards += f'<div class="nc-title">{art.get("title","")}</div>\n'
        cards += f'<div class="nc-desc">{art.get("summary","")}</div>\n'
        cards += f'<div style="margin-top:8px;padding:8px 10px;background:#eff6ff;border-radius:6px;font-size:12px;color:#1d4ed8;">📌 {art.get("takeaway","")}</div>\n'
        cards += '</div>\n'

    new_section = f'<!-- AUTO:BOT_ARTICLES_START -->\n{cards}<!-- AUTO:BOT_ARTICLES_END -->'

    if 'AUTO:BOT_ARTICLES_START' in html:
        html_new = re.sub(
            r'<!-- AUTO:BOT_ARTICLES_START -->.*?<!-- AUTO:BOT_ARTICLES_END -->',
            new_section,
            html,
            flags=re.DOTALL
        )
        print(f"[DEBUG] New HTML length: {len(html_new)}")
        if html_new == html:
            print("[ERROR] Regex did not change anything!")
            return False
    else:
        print("[ERROR] Markers not found in HTML!")
        return False

    encoded = base64.b64encode(html_new.encode("utf-8")).decode("utf-8")
    payload = {
        "message": f"NEWSPACE Bot: {now}",
        "content": encoded,
        "sha": sha
    }
    r2 = requests.put(url, headers=headers, json=payload)
    print(f"[DEBUG] PUT status: {r2.status_code}")
    if r2.status_code not in (200, 201):
        print(f"[ERROR] Push failed: {r2.text[:300]}")
        return False

    return True


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *NEWSPACE Bot is ready!*\n\n"
        "Forward any newspaper PDF and I will:\n"
        "1️⃣ Extract all text\n"
        "2️⃣ Find top 5 finance stories\n"
        "3️⃣ Summarize with AI\n"
        "4️⃣ Update your NEWSPACE website\n\n"
        "Send a PDF now to try!",
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

    try:
        file = await ctx.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            with open(tmp.name, "rb") as f:
                pdf_bytes = f.read()
    except Exception as e:
        await msg.edit_text(f"❌ Download failed: {e}")
        return

    try:
        text = extract_text_from_pdf(pdf_bytes)
        if len(text) < 100:
            await msg.edit_text("⚠️ Could not extract text. Use a text-based PDF.")
            return
        await msg.edit_text(f"✅ Extracted {len(text):,} chars\n🤖 Summarizing...")
    except Exception as e:
        await msg.edit_text(f"❌ Extraction failed: {e}")
        return

    try:
        articles = summarize_newspaper(text, newspaper_name)
        todays_summaries.extend(articles)
    except Exception as e:
        await msg.edit_text(f"❌ AI failed: {e}")
        return

    total = len(articles)
    # Send summary count first
    await msg.edit_text(
        f"✅ *Found {total} articles from {newspaper_name}*\n\n"
        f"Updating NEWSPACE website with all articles...",
        parse_mode="Markdown"
    )

    # Send first 5 as preview in Telegram (Telegram has message limits)
    preview = f"📰 *Preview — First 5 of {total} articles:*\n\n"
    for i, art in enumerate(articles[:5], 1):
        preview += f"*{i}. {art.get('title', '')}*\n"
        preview += f"_{art.get('summary', '')}_\n"
        preview += f"📌 _{art.get('takeaway', '')}_\n\n"
    if len(preview) > 4000:
        preview = preview[:3990] + "..."
    await update.message.reply_text(preview, parse_mode="Markdown")

    m2 = await update.message.reply_text("🌐 Updating NEWSPACE website...")
    success = update_github_site(todays_summaries)
    if success:
        await m2.edit_text(
            "✅ *NEWSPACE updated!*\nVisit site and press Ctrl+Shift+R",
            parse_mode="Markdown"
        )
    else:
        await m2.edit_text("⚠️ Site update failed. Check Railway logs.")


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    await update.message.reply_text(
        f"📊 *NEWSPACE Bot Status*\n\n"
        f"🕐 {now}\n"
        f"📰 Articles today: {len(todays_summaries)}\n"
        f"🤖 Groq AI: ✅ Free\n"
        f"🌐 GitHub: {'✅ Ready' if GITHUB_TOKEN else '⚠️ Token missing'}",
        parse_mode="Markdown"
    )


async def clear_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global todays_summaries
    todays_summaries = []
    await update.message.reply_text("🗑️ Cleared.")


async def error_handler(update, ctx: ContextTypes.DEFAULT_TYPE):
    error = ctx.error
    if isinstance(error, Conflict):
        print("[ERROR] Conflict — another instance running. Waiting 5 seconds...")
        await asyncio.sleep(5)
    elif isinstance(error, NetworkError):
        print(f"[ERROR] Network error: {error}")
        await asyncio.sleep(3)
    else:
        print(f"[ERROR] {error}")


def main():
    print("[NEWSPACE Bot] Starting @Sarthnews_Bot...")

    # Delete webhook first to clear any conflicts
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
        )
        print(f"[DEBUG] Webhook deleted: {r.json()}")
    except Exception as e:
        print(f"[WARN] Could not delete webhook: {e}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("clear",  clear_cmd))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_error_handler(error_handler)

    print("[NEWSPACE Bot] Running!")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        close_loop=False
    )


if __name__ == "__main__":
    main()
