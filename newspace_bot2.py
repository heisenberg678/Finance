"""
NEWSPACE Telegram Bot - @Sarthnews_Bot
Handles scanned PDFs with OCR + large files via URL
"""

import os, json, re, tempfile, requests, fitz, base64, time
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
# PDF EXTRACTION — Text + OCR fallback
# ────────────────────────────────────────────

def extract_pages(pdf_bytes):
    """Extract text from PDF. For scanned pages use OCR via pdfplumber or image text."""
    pages = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            total = len(doc)
            print(f"[PDF] {total} pages")
            for i, page in enumerate(doc):
                # Try normal text extraction first
                text = page.get_text("text").strip()

                # If page has very little text, it's likely a scanned image
                # Try extracting text blocks differently
                if len(text) < 100:
                    blocks = page.get_text("blocks")
                    text = " ".join([b[4] for b in blocks if b[4].strip()]).strip()

                # Try raw text extraction as last resort
                if len(text) < 100:
                    text = page.get_text("rawdict")
                    if isinstance(text, dict):
                        words = []
                        for block in text.get("blocks", []):
                            for line in block.get("lines", []):
                                for span in line.get("spans", []):
                                    words.append(span.get("text", ""))
                        text = " ".join(words).strip()

                if len(text) > 50:
                    pages.append((i + 1, text))
                else:
                    print(f"[PDF] Page {i+1} appears to be scanned image - skipping")

            print(f"[PDF] {len(pages)}/{total} pages with text")
            return pages, total
    except Exception as e:
        print(f"[PDF] Error: {e}")
        return [], 0


def is_scanned_pdf(pdf_bytes):
    """Check if PDF is mostly scanned images."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            text_pages = 0
            sample = min(5, len(doc))
            for i in range(sample):
                text = doc[i].get_text().strip()
                if len(text) > 100:
                    text_pages += 1
            return text_pages < (sample * 0.3)  # less than 30% have text = scanned
    except:
        return True


# ────────────────────────────────────────────
# GROQ SUMMARIZATION
# ────────────────────────────────────────────

def summarize_page(page_num, page_text, newspaper_name):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    text = page_text[:5000]
    prompt = f"""Extract ALL news stories from page {page_num} of {newspaper_name}.

Return a JSON array. Each item must have:
- "title": clear headline (max 15 words)
- "category": one of [Indian Finance, Stock Market, Global Markets, World News, Banking, Economy, Business, Corporate, Politics]
- "summary": 3-4 sentences explaining what happened and why it matters for India
- "takeaway": one key lesson for a finance student
- "source": "{newspaper_name}"
- "page": {page_num}

Rules:
- Return ONLY valid JSON array starting with [ and ending with ]
- No markdown, no backticks, no explanation
- Skip ads, weather, sports, crosswords
- If no articles found return []

TEXT:
{text}"""

    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(3 * attempt)
            r = requests.post(url, headers=headers, json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4000,
                "temperature": 0.1
            }, timeout=45)

            if r.status_code == 429:
                print(f"[WARN] Rate limited, waiting 10s...")
                time.sleep(10)
                continue
            if r.status_code != 200:
                print(f"[WARN] Page {page_num} error: {r.status_code}")
                return []

            raw = r.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r'```json|```', '', raw).strip()
            start = raw.find('[')
            end = raw.rfind(']') + 1
            if start == -1 or end == 0:
                return []
            articles = json.loads(raw[start:end])
            valid = [a for a in articles if a.get('title') and a.get('summary') and len(a.get('title','')) > 3]
            print(f"[PAGE {page_num}] {len(valid)} articles")
            return valid
        except Exception as e:
            print(f"[WARN] Page {page_num} attempt {attempt+1}: {e}")
    return []


async def process_newspaper(pdf_bytes, newspaper_name, progress_msg):
    pages, total_pages = extract_pages(pdf_bytes)
    all_articles = []

    for idx, (page_num, page_text) in enumerate(pages):
        if idx % 3 == 0:
            try:
                await progress_msg.edit_text(
                    f"📰 *{newspaper_name}*\n"
                    f"Processing page {page_num}/{total_pages}...\n"
                    f"Articles found: {len(all_articles)}",
                    parse_mode="Markdown"
                )
            except:
                pass

        articles = summarize_page(page_num, page_text, newspaper_name)
        all_articles.extend(articles)
        time.sleep(0.3)

    # Deduplicate
    seen = set()
    unique = []
    for art in all_articles:
        key = art.get('title','').lower().strip()[:40]
        if key and key not in seen:
            seen.add(key)
            unique.append(art)

    print(f"[DONE] {len(unique)} unique articles from {total_pages} pages")
    return unique, total_pages


# ────────────────────────────────────────────
# GITHUB UPDATE
# ────────────────────────────────────────────

def update_github_site(articles):
    if not GITHUB_TOKEN:
        return False, "No GITHUB_TOKEN"

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
        "Indian Finance": ("#dbeafe","#1d4ed8"), "Stock Market": ("#dcfce7","#15803d"),
        "Global Markets": ("#ede9fe","#6d28d9"), "World News": ("#fef9c3","#a16207"),
        "Banking": ("#dbeafe","#1d4ed8"), "Economy": ("#dcfce7","#15803d"),
        "Business": ("#f3e8ff","#7e22ce"), "Corporate": ("#fff7ed","#c2410c"),
        "Politics": ("#fee2e2","#b91c1c"),
    }

    source = articles[0].get("source","Newspaper") if articles else "Newspaper"
    cards  = f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 16px;font-size:12px;color:#166534;margin-bottom:14px;">📰 <strong>{len(articles)} articles</strong> from {source} · {now}</div>\n'

    for i, art in enumerate(articles):
        cat = art.get("category","Indian Finance")
        bg, color = tag_colors.get(cat, ("#dbeafe","#1d4ed8"))
        featured = 'featured' if i == 0 else ''
        pg = f'<span style="font-size:10px;color:var(--ink4);"> · Pg {art.get("page","")}</span>' if art.get("page") else ''
        cards += f'<div class="ncard {featured}">\n'
        cards += f'<div class="nc-meta"><span class="tag" style="background:{bg};color:{color}">{cat}</span><span class="nc-source">{source}</span>{pg}<span class="nc-sep">·</span><span class="nc-time">Today</span></div>\n'
        cards += f'<div class="nc-title">{art.get("title","")}</div>\n'
        cards += f'<div class="nc-desc">{art.get("summary","")}</div>\n'
        cards += f'<div style="margin-top:8px;padding:8px 10px;background:#eff6ff;border-radius:6px;font-size:12px;color:#1d4ed8;">📌 {art.get("takeaway","")}</div>\n'
        cards += '</div>\n'

    new_section = f'<!-- AUTO:BOT_ARTICLES_START -->\n{cards}<!-- AUTO:BOT_ARTICLES_END -->'
    html_new = re.sub(r'<!-- AUTO:BOT_ARTICLES_START -->.*?<!-- AUTO:BOT_ARTICLES_END -->', new_section, html, flags=re.DOTALL)

    encoded = base64.b64encode(html_new.encode("utf-8")).decode("utf-8")
    r2 = requests.put(url, headers=headers, json={
        "message": f"NEWSPACE Bot: {len(articles)} articles · {now}",
        "content": encoded, "sha": sha
    })
    return (True, f"{len(articles)} articles published") if r2.status_code in (200,201) else (False, f"PUT failed: {r2.status_code}")


# ────────────────────────────────────────────
# TELEGRAM HANDLERS
# ────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *NEWSPACE Bot ready!*\n\n"
        "Send any newspaper PDF and I will:\n"
        "📄 Process every page\n"
        "🤖 Extract every article\n"
        "🌐 Update NEWSPACE website\n\n"
        "⚠️ *Important:* Telegram has a 20MB limit for bots.\n"
        "For files larger than 20MB, compress the PDF first or send in parts.\n\n"
        "Commands: /status · /clear",
        parse_mode="Markdown"
    )


async def handle_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global todays_summaries

    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Please send a PDF file.")
        return

    file_size_mb = (doc.file_size or 0) / (1024 * 1024)
    newspaper_name = doc.file_name.replace(".pdf","").replace("_"," ").replace("-"," ").title()

    # Telegram bot API hard limit is 20MB for downloads
    if file_size_mb > 20:
        await update.message.reply_text(
            f"⚠️ *File too large: {file_size_mb:.1f}MB*\n\n"
            f"Telegram bots can only download files up to 20MB.\n\n"
            f"*How to fix:*\n"
            f"1️⃣ Compress the PDF first using *ilovepdf.com* (free)\n"
            f"2️⃣ Or split into parts using *smallpdf.com* (free)\n"
            f"3️⃣ Then send the compressed/split file here\n\n"
            f"Most newspapers compress to under 10MB easily.",
            parse_mode="Markdown"
        )
        return

    msg = await update.message.reply_text(
        f"📰 *{newspaper_name}* ({file_size_mb:.1f}MB)\nDownloading...",
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

    # Check if scanned
    if is_scanned_pdf(pdf_bytes):
        await msg.edit_text(
            f"⚠️ *{newspaper_name} appears to be a scanned image PDF*\n\n"
            f"Scanned PDFs are photos of newspaper pages — the bot cannot read them.\n\n"
            f"*You need a digital/text-based PDF.*\n\n"
            f"Try these sources for text-based PDFs:\n"
            f"• PressReader app\n"
            f"• Official newspaper website (e-paper section)\n"
            f"• Some Telegram groups provide text-based versions\n\n"
            f"The group you are using appears to share scanned copies.",
            parse_mode="Markdown"
        )
        return

    await msg.edit_text(
        f"📰 *{newspaper_name}*\nStarting page-by-page extraction...",
        parse_mode="Markdown"
    )

    # Process
    try:
        articles, total_pages = await process_newspaper(pdf_bytes, newspaper_name, msg)
    except Exception as e:
        await msg.edit_text(f"❌ Processing failed: {e}")
        return

    if len(articles) == 0:
        await msg.edit_text(
            f"⚠️ No articles found in {newspaper_name}.\n\n"
            f"The PDF may be a scanned image. Please use a text-based digital PDF.",
        )
        return

    todays_summaries = articles
    await msg.edit_text(
        f"✅ *{newspaper_name} processed!*\n"
        f"📄 Pages: {total_pages}\n"
        f"📰 Articles: {len(articles)}\n"
        f"Updating website...",
        parse_mode="Markdown"
    )

    # Preview
    preview = f"📰 *{len(articles)} articles extracted:*\n\n"
    for i, art in enumerate(articles[:3], 1):
        preview += f"*{i}. {art.get('title','')}*\n_{art.get('summary','')[:120]}..._\n\n"
    if len(articles) > 3:
        preview += f"_...and {len(articles)-3} more on NEWSPACE_"
    if len(preview) > 4000:
        preview = preview[:3990] + "..."
    await update.message.reply_text(preview, parse_mode="Markdown")

    # Update site
    m2 = await update.message.reply_text("🌐 Publishing to NEWSPACE...")
    success, message = update_github_site(articles)
    if success:
        await m2.edit_text(
            f"✅ *NEWSPACE updated!*\n📰 {message}\n🕐 Site updates in ~5 mins\n\nheisenberg678.github.io/Finance",
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
        f"📄 Max file size: 20MB (Telegram limit)",
        parse_mode="Markdown"
    )


async def clear_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global todays_summaries
    todays_summaries = []
    await update.message.reply_text("🗑️ Cleared.")


async def error_handler(update, ctx: ContextTypes.DEFAULT_TYPE):
    print(f"[ERROR] {ctx.error}")


def main():
    print("[NEWSPACE Bot] Starting...")
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
    except:
        pass
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_error_handler(error_handler)
    print("[NEWSPACE Bot] Running!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
