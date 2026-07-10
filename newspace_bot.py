"""
NEWSPACE Telegram Bot
@Sarthnews_Bot - Full newspaper extraction
"""

import os
import json
import re
import tempfile
import requests
import fitz
import base64
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from datetime import datetime, timezone, timedelta

BOT_TOKEN    = "8974294866:AAHDCzLcm9jqZ56j6MFGsvmcmplSbPZMFkU"
GROQ_API_KEY = "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"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "heisenberg678/Finance"
GITHUB_FILE  = "index.html"

IST = timezone(timedelta(hours=5, minutes=30))
todays_summaries = []


# ── PDF EXTRACTION ──
def extract_text_from_pdf(pdf_bytes):
    text = ""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        print(f"[DEBUG] PDF pages: {len(doc)}")
        for page in doc:
            text += page.get_text() + "\n"
    print(f"[DEBUG] Total chars: {len(text):,}")
    return text.strip()


# ── GROQ API CALL ──
def call_groq(prompt, max_tokens=2000):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.1
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code != 200:
        raise Exception(f"Groq error {r.status_code}: {r.text[:100]}")
    return r.json()["choices"][0]["message"]["content"].strip()


# ── EXTRACT ARTICLES FROM ONE CHUNK ──
def extract_articles_from_chunk(chunk, newspaper_name, chunk_num):
    prompt = f"""Extract news stories from this newspaper text. Return a JSON array.

Each story needs these exact fields:
{{"title": "headline here", "category": "Indian Finance", "summary": "what happened in 2 sentences", "takeaway": "key lesson for finance students", "source": "{newspaper_name}"}}

Category must be one of: Indian Finance, Stock Market, Global Markets, World News, Banking, Economy, Business, Politics

Rules:
- Return ONLY the JSON array starting with [ and ending with ]
- No markdown, no backticks, no explanation
- Include every distinct news story you find
- Skip advertisements and repeated content
- If no stories found return empty array []

TEXT:
{chunk[:3500]}"""

    try:
        raw = call_groq(prompt, max_tokens=2500)
        # Clean up response
        raw = re.sub(r'```json|```', '', raw).strip()
        # Find JSON array
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start == -1 or end == 0:
            print(f"[WARN] No JSON array in chunk {chunk_num}")
            return []
        raw = raw[start:end]
        articles = json.loads(raw)
        print(f"[DEBUG] Chunk {chunk_num}: {len(articles)} articles")
        return articles
    except Exception as e:
        print(f"[WARN] Chunk {chunk_num} failed: {e}")
        return []


# ── PROCESS FULL NEWSPAPER ──
def summarize_newspaper(text, newspaper_name):
    # Split into overlapping chunks
    chunk_size = 3500
    overlap = 200
    chunks = []
    i = 0
    while i < len(text):
        chunk = text[i:i + chunk_size]
        if len(chunk) > 200:  # skip tiny chunks
            chunks.append(chunk)
        i += chunk_size - overlap

    print(f"[DEBUG] Total chunks: {len(chunks)}")

    all_articles = []
    # Process up to 10 chunks
    for idx, chunk in enumerate(chunks[:10]):
        print(f"[DEBUG] Processing chunk {idx+1}/{min(len(chunks), 10)}")
        articles = extract_articles_from_chunk(chunk, newspaper_name, idx+1)
        all_articles.extend(articles)

    # Remove duplicates by similar title
    seen = set()
    unique = []
    for art in all_articles:
        title = art.get("title", "").lower().strip()[:50]
        if title and title not in seen and len(title) > 5:
            seen.add(title)
            # Validate required fields
            if art.get("summary") and art.get("title"):
                unique.append(art)

    print(f"[DEBUG] Total unique articles: {len(unique)}")
    return unique


# ── GITHUB SITE UPDATE ──
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
    if r.status_code != 200:
        print(f"[ERROR] GET failed: {r.status_code}")
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
        "Business":        ("#f3e8ff", "#7e22ce"),
        "Politics":        ("#fee2e2", "#b91c1c"),
    }

    # Build article cards
    cards = f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px;font-size:12px;color:#166534;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;"><span>✅ {len(articles)} articles from newspaper · {now}</span></div>\n'

    for i, art in enumerate(articles):
        cat = art.get("category", "Indian Finance")
        bg, color = tag_colors.get(cat, ("#dbeafe", "#1d4ed8"))
        featured = "featured" if i == 0 else ""
        cards += f'<div class="ncard {featured}">\n'
        cards += f'<div class="nc-meta"><span class="tag" style="background:{bg};color:{color}">{cat}</span>'
        cards += f'<span class="nc-source">{art.get("source","Newspaper")}</span>'
        cards += f'<span class="nc-sep">·</span><span class="nc-time">Today</span></div>\n'
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
    else:
        print("[ERROR] Markers not found!")
        return False

    encoded = base64.b64encode(html_new.encode("utf-8")).decode("utf-8")
    payload = {"message": f"NEWSPACE Bot: {now}", "content": encoded, "sha": sha}
    r2 = requests.put(url, headers=headers, json=payload)
    print(f"[DEBUG] PUT status: {r2.status_code}")
    return r2.status_code in (200, 201)


# ── TELEGRAM HANDLERS ──
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *NEWSPACE Bot ready!*\n\n"
        "Send any newspaper PDF and I will extract ALL news stories and update your website.\n\n"
        "Commands:\n"
        "/status — check bot status\n"
        "/clear — clear today's articles",
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
        f"📰 *{newspaper_name}*\nDownloading...",
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
        await msg.edit_text(f"❌ Download failed: {e}")
        return

    # Extract text
    try:
        await msg.edit_text(f"📰 *{newspaper_name}*\nExtracting text from all pages...")
        text = extract_text_from_pdf(pdf_bytes)
        if len(text) < 100:
            await msg.edit_text("⚠️ Could not extract text. Use a text-based PDF not a scanned image.")
            return
        await msg.edit_text(f"📰 *{newspaper_name}*\n✅ Extracted {len(text):,} characters\n🤖 Finding all articles...", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Extraction failed: {e}")
        return

    # Extract all articles
    try:
        articles = summarize_newspaper(text, newspaper_name)
        if len(articles) == 0:
            await msg.edit_text(
                "⚠️ Found 0 articles. The PDF may be a scanned image or have no extractable text.\n\n"
                "Try a different PDF file."
            )
            return
        todays_summaries.extend(articles)
        await msg.edit_text(
            f"✅ *Found {len(articles)} articles from {newspaper_name}*\n"
            f"Updating NEWSPACE website...",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Article extraction failed: {e}")
        return

    # Send preview of first 3 articles
    preview = f"📰 *Preview — {len(articles)} total articles:*\n\n"
    for i, art in enumerate(articles[:3], 1):
        preview += f"*{i}. {art.get('title', '')}*\n"
        preview += f"_{art.get('summary', '')}_\n\n"
    preview += f"_...and {max(0, len(articles)-3)} more on NEWSPACE_"
    if len(preview) > 4000:
        preview = preview[:3990] + "..."
    await update.message.reply_text(preview, parse_mode="Markdown")

    # Update site
    m2 = await update.message.reply_text("🌐 Updating NEWSPACE website...")
    success = update_github_site(todays_summaries)
    if success:
        await m2.edit_text(
            f"✅ *NEWSPACE updated with {len(todays_summaries)} articles!*\n"
            "Site refreshes in ~5 minutes via GitHub Pages.",
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
    await update.message.reply_text("🗑️ Cleared. Send new PDFs to start fresh.")


async def error_handler(update, ctx: ContextTypes.DEFAULT_TYPE):
    print(f"[ERROR] {ctx.error}")


def main():
    print("[NEWSPACE Bot] Starting @Sarthnews_Bot...")

    # Clear any old webhook
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
        print(f"[DEBUG] Webhook cleared: {r.json().get('result')}")
    except Exception as e:
        print(f"[WARN] Webhook clear failed: {e}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("clear",  clear_cmd))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_error_handler(error_handler)

    print("[NEWSPACE Bot] Running!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
