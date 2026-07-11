"""
NEWSPACE Telegram Bot
@Sarthnews_Bot
Processes every page of large newspapers (30-40MB, 20-30 pages)
Extracts and summarizes every single article
"""

import os
import json
import re
import tempfile
import requests
import fitz
import base64
import time
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


# ────────────────────────────────────────────
# 1. PDF — EXTRACT PAGE BY PAGE
# ────────────────────────────────────────────

def extract_pages(pdf_bytes):
    """Return list of (page_num, text) for every page."""
    pages = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        total = len(doc)
        print(f"[PDF] {total} pages found")
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if len(text) > 100:   # skip blank/image pages
                pages.append((i + 1, text))
    print(f"[PDF] {len(pages)} pages with extractable text")
    return pages, total


# ────────────────────────────────────────────
# 2. GROQ — SUMMARIZE ONE PAGE
# ────────────────────────────────────────────

def summarize_page(page_num, page_text, newspaper_name):
    """Send one page to Groq and get all articles from it."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    # Truncate page text to 5000 chars to stay within token limits
    text = page_text[:5000]

    prompt = f"""You are a financial newspaper editor. Read page {page_num} of {newspaper_name} below.

Extract EVERY distinct news story or article on this page. For each one return a JSON object.

Required fields for each article:
- "title": clear headline in plain English (max 15 words)
- "category": one of [Indian Finance, Stock Market, Global Markets, World News, Banking, Economy, Business, Corporate, Politics]
- "summary": plain English summary of the full story in 3-4 sentences covering what happened, who is involved, and the impact
- "takeaway": one sentence key insight for a finance student learning from this story
- "source": "{newspaper_name}"
- "page": {page_num}

IMPORTANT RULES:
- Return ONLY a valid JSON array starting with [ and ending with ]
- No markdown, no backticks, no explanation text before or after
- Include ALL articles on the page, even short ones
- Skip advertisements, weather reports, sports scores, crosswords
- If the page has no news articles return exactly: []
- Write summaries in simple plain English, not newspaper jargon

PAGE {page_num} TEXT:
{text}"""

    try:
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
            "temperature": 0.1
        }
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        if r.status_code == 429:
            print(f"[WARN] Rate limited on page {page_num}, waiting 5s...")
            time.sleep(5)
            r = requests.post(url, headers=headers, json=payload, timeout=45)
        if r.status_code != 200:
            print(f"[WARN] Page {page_num} Groq error: {r.status_code}")
            return []

        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r'```json|```', '', raw).strip()

        # Find JSON array
        start = raw.find('[')
        end   = raw.rfind(']') + 1
        if start == -1 or end == 0:
            return []
        raw = raw[start:end]

        articles = json.loads(raw)
        # Validate each article has required fields
        valid = []
        for a in articles:
            if a.get('title') and a.get('summary') and len(a.get('title','')) > 3:
                valid.append(a)
        print(f"[PAGE {page_num}] {len(valid)} articles found")
        return valid

    except json.JSONDecodeError as e:
        print(f"[WARN] Page {page_num} JSON error: {e}")
        return []
    except Exception as e:
        print(f"[WARN] Page {page_num} failed: {e}")
        return []


# ────────────────────────────────────────────
# 3. PROCESS FULL NEWSPAPER
# ────────────────────────────────────────────

async def process_newspaper(pdf_bytes, newspaper_name, progress_msg):
    """Process every page and collect all articles."""
    pages, total_pages = extract_pages(pdf_bytes)

    all_articles = []
    processed = 0

    for page_num, page_text in pages:
        # Update progress every 3 pages
        if processed % 3 == 0:
            try:
                await progress_msg.edit_text(
                    f"📰 *{newspaper_name}*\n"
                    f"Processing page {page_num}/{total_pages}...\n"
                    f"Articles found so far: {len(all_articles)}",
                    parse_mode="Markdown"
                )
            except:
                pass

        articles = summarize_page(page_num, page_text, newspaper_name)
        all_articles.extend(articles)
        processed += 1

        # Small delay to avoid rate limiting
        time.sleep(0.5)

    # Remove duplicates by title similarity
    seen = set()
    unique = []
    for art in all_articles:
        title_key = art.get('title','').lower().strip()[:40]
        if title_key and title_key not in seen:
            seen.add(title_key)
            unique.append(art)

    print(f"[DONE] Total unique articles: {len(unique)} from {total_pages} pages")
    return unique, total_pages


# ────────────────────────────────────────────
# 4. GITHUB SITE UPDATE
# ────────────────────────────────────────────

def update_github_site(articles):
    if not GITHUB_TOKEN:
        return False, "No GITHUB_TOKEN set"

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return False, f"GET failed: {r.status_code}"

    data = r.json()
    sha  = data["sha"]
    html = base64.b64decode(data["content"]).decode("utf-8")
    now  = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

    if 'AUTO:BOT_ARTICLES_START' not in html:
        return False, "Markers not found in index.html"

    tag_colors = {
        "Indian Finance":  ("#dbeafe", "#1d4ed8"),
        "Stock Market":    ("#dcfce7", "#15803d"),
        "Global Markets":  ("#ede9fe", "#6d28d9"),
        "World News":      ("#fef9c3", "#a16207"),
        "Banking":         ("#dbeafe", "#1d4ed8"),
        "Economy":         ("#dcfce7", "#15803d"),
        "Business":        ("#f3e8ff", "#7e22ce"),
        "Corporate":       ("#fff7ed", "#c2410c"),
        "Politics":        ("#fee2e2", "#b91c1c"),
    }

    # Group articles by category for better display
    cards  = f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 16px;'
    cards += f'font-size:12px;color:#166534;margin-bottom:14px;display:flex;align-items:center;'
    cards += f'justify-content:space-between;"><span>📰 <strong>{len(articles)} articles</strong> '
    cards += f'from {articles[0].get("source","Newspaper") if articles else "Newspaper"} · {now}</span></div>\n'

    for i, art in enumerate(articles):
        cat = art.get("category", "Indian Finance")
        bg, color = tag_colors.get(cat, ("#dbeafe", "#1d4ed8"))
        featured = 'featured' if i == 0 else ''
        page_badge = f'<span style="font-size:10px;color:var(--ink4);margin-left:4px;">Pg {art.get("page","")}</span>' if art.get("page") else ''

        cards += f'<div class="ncard {featured}">\n'
        cards += f'<div class="nc-meta">'
        cards += f'<span class="tag" style="background:{bg};color:{color}">{cat}</span>'
        cards += f'<span class="nc-source">{art.get("source","Newspaper")}</span>'
        cards += f'{page_badge}'
        cards += f'<span class="nc-sep">·</span><span class="nc-time">Today</span>'
        cards += f'</div>\n'
        cards += f'<div class="nc-title">{art.get("title","")}</div>\n'
        cards += f'<div class="nc-desc" style="-webkit-line-clamp:3">{art.get("summary","")}</div>\n'
        cards += f'<div style="margin-top:8px;padding:8px 10px;background:#eff6ff;border-radius:6px;font-size:12px;color:#1d4ed8;">📌 {art.get("takeaway","")}</div>\n'
        cards += f'</div>\n'

    new_section = f'<!-- AUTO:BOT_ARTICLES_START -->\n{cards}<!-- AUTO:BOT_ARTICLES_END -->'

    html_new = re.sub(
        r'<!-- AUTO:BOT_ARTICLES_START -->.*?<!-- AUTO:BOT_ARTICLES_END -->',
        new_section,
        html,
        flags=re.DOTALL
    )

    encoded = base64.b64encode(html_new.encode("utf-8")).decode("utf-8")
    payload  = {"message": f"NEWSPACE Bot: {len(articles)} articles · {now}", "content": encoded, "sha": sha}
    r2 = requests.put(url, headers=headers, json=payload)

    if r2.status_code in (200, 201):
        return True, f"{len(articles)} articles published"
    return False, f"PUT failed: {r2.status_code}"


# ────────────────────────────────────────────
# 5. TELEGRAM HANDLERS
# ────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *NEWSPACE Bot ready!*\n\n"
        "Send any newspaper PDF (up to 50MB) and I will:\n"
        "📄 Process every single page\n"
        "🤖 Extract and summarize every article\n"
        "🌐 Update your NEWSPACE website\n\n"
        "Commands: /status · /clear",
        parse_mode="Markdown"
    )


async def handle_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global todays_summaries

    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Please send a PDF file.")
        return

    # Check file size
    file_size_mb = doc.file_size / (1024 * 1024)
    if file_size_mb > 50:
        await update.message.reply_text(
            f"⚠️ File is {file_size_mb:.1f}MB. Max supported is 50MB.\n"
            "Try splitting the newspaper into smaller parts."
        )
        return

    newspaper_name = doc.file_name.replace(".pdf","").replace("_"," ").replace("-"," ").title()
    msg = await update.message.reply_text(
        f"📰 *{newspaper_name}* ({file_size_mb:.1f}MB)\nDownloading...",
        parse_mode="Markdown"
    )

    # Download
    try:
        file = await ctx.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            with open(tmp.name, "rb") as f:
                pdf_bytes = f.read()
        await msg.edit_text(
            f"📰 *{newspaper_name}*\nDownloaded {file_size_mb:.1f}MB\nStarting page-by-page extraction...",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Download failed: {e}")
        return

    # Process all pages
    try:
        articles, total_pages = await process_newspaper(pdf_bytes, newspaper_name, msg)
    except Exception as e:
        await msg.edit_text(f"❌ Processing failed: {e}")
        return

    if len(articles) == 0:
        await msg.edit_text(
            f"⚠️ No articles found in {newspaper_name}.\n\n"
            "This usually means the PDF is a scanned image (not text-based).\n"
            "Please use a digital PDF, not a scanned copy."
        )
        return

    todays_summaries = articles  # replace with today's fresh articles

    await msg.edit_text(
        f"✅ *{newspaper_name} processed!*\n\n"
        f"📄 Pages: {total_pages}\n"
        f"📰 Articles found: {len(articles)}\n\n"
        f"Updating NEWSPACE website...",
        parse_mode="Markdown"
    )

    # Preview first 3 articles
    preview = f"📰 *Preview — {len(articles)} articles extracted:*\n\n"
    for i, art in enumerate(articles[:3], 1):
        preview += f"*{i}. {art.get('title','')}*\n"
        preview += f"_{art.get('summary','')[:150]}..._\n\n"
    if len(articles) > 3:
        preview += f"_...and {len(articles)-3} more articles on NEWSPACE_"
    if len(preview) > 4000:
        preview = preview[:3990] + "..."
    await update.message.reply_text(preview, parse_mode="Markdown")

    # Push to GitHub
    m2 = await update.message.reply_text("🌐 Publishing to NEWSPACE...")
    success, message = update_github_site(articles)
    if success:
        await m2.edit_text(
            f"✅ *NEWSPACE updated!*\n"
            f"📰 {message}\n"
            f"🕐 Site refreshes in ~5 minutes\n\n"
            f"Visit: heisenberg678.github.io/Finance",
            parse_mode="Markdown"
        )
    else:
        await m2.edit_text(f"⚠️ Site update failed: {message}")


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    await update.message.reply_text(
        f"📊 *NEWSPACE Bot Status*\n\n"
        f"🕐 {now}\n"
        f"📰 Articles today: {len(todays_summaries)}\n"
        f"🤖 Groq AI: ✅ Free\n"
        f"🌐 GitHub: {'✅ Ready' if GITHUB_TOKEN else '⚠️ Token missing'}\n"
        f"📄 Max PDF size: 50MB",
        parse_mode="Markdown"
    )


async def clear_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global todays_summaries
    todays_summaries = []
    await update.message.reply_text("🗑️ Cleared. Send a new PDF to start fresh.")


async def error_handler(update, ctx: ContextTypes.DEFAULT_TYPE):
    print(f"[ERROR] {ctx.error}")


def main():
    print("[NEWSPACE Bot] Starting @Sarthnews_Bot...")
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
        print(f"[DEBUG] Webhook cleared: {r.json().get('result')}")
    except Exception as e:
        print(f"[WARN] {e}")

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
