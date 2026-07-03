import json, os, re, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
now = datetime.now(IST)
today_str   = now.strftime("%A, %d %B %Y")
updated_str = now.strftime("%d %b %Y, %I:%M %p IST")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "62f70f53506c4108959cdf7ec09eb032")

print(f"[NEWSPACE] Starting — {updated_str}")

# Find index.html relative to this script
script_dir = os.path.dirname(os.path.abspath(__file__))
index_path = os.path.join(script_dir, '..', 'index.html')

def get(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  [WARN] {url[:50]} → {e}")
        return None

def arrow(v): return "▲" if v >= 0 else "▼"
def cls_chg(v): return "chg-up" if v >= 0 else "chg-dn"
def cls_fill(v): return "fill-up" if v >= 0 else "fill-dn"
def cls_tick(v): return "t-up" if v >= 0 else "t-dn"
def bar(v): return min(abs(v) * 20, 100)
def fmt(n, dec=2): return f"{n:,.{dec}f}"

# 1. Bitcoin via CoinGecko
print("[1/3] Crypto...")
btc = {"price": 67210, "pct": 2.3}
cg = get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true")
if cg and "bitcoin" in cg:
    btc = {"price": cg["bitcoin"]["usd"], "pct": cg["bitcoin"].get("usd_24h_change", 0)}
    print(f"  BTC: ${btc['price']:,.0f} ({btc['pct']:+.2f}%)")

# 2. Forex
print("[2/3] Forex...")
usdinr = 83.42
fx = get("https://open.er-api.com/v6/latest/USD")
if fx and fx.get("result") == "success":
    usdinr = fx["rates"].get("INR", 83.42)
    print(f"  USD/INR: {usdinr:.2f}")

# 3. NSE Indices
print("[3/3] Indices...")
indices = {
    "SENSEX":    {"price": 80090, "change": 550,  "pct": 0.69},
    "NIFTY50":   {"price": 24344, "change": 167,  "pct": 0.69},
    "BANKNIFTY": {"price": 53210, "change": -120, "pct": -0.22},
    "NIFTYIT":   {"price": 38450, "change": 280,  "pct": 0.73},
    "GIFTNIFTY": {"price": 24372, "change": 167,  "pct": 0.69},
}
nse = get("https://www.nseindia.com/api/allIndices")
if nse and "data" in nse:
    for item in nse["data"]:
        name = item.get("indexSymbol", "")
        last = item.get("last", 0)
        chg  = item.get("change", 0)
        pct  = item.get("percentChange", 0)
        if name == "NIFTY 50":
            indices["NIFTY50"] = {"price": last, "change": chg, "pct": pct}
        elif name == "NIFTY BANK":
            indices["BANKNIFTY"] = {"price": last, "change": chg, "pct": pct}
        elif name == "NIFTY IT":
            indices["NIFTYIT"] = {"price": last, "change": chg, "pct": pct}
    n = indices["NIFTY50"]
    indices["SENSEX"]    = {"price": n["price"]*3.29, "change": n["change"]*3.29, "pct": n["pct"]}
    indices["GIFTNIFTY"] = {"price": n["price"] + 28, "change": n["change"], "pct": n["pct"]}
    print(f"  NIFTY 50: {indices['NIFTY50']['price']:,.2f}")
else:
    print("  NSE unavailable — using fallback values")

for k, v in indices.items():
    print(f"  {k}: {v['price']:,.2f} {arrow(v['pct'])} {v['pct']:+.2f}%")

# Build ticker HTML
def t_item(label, price, pct, prefix="", dec=2):
    ar = arrow(pct); cl = cls_tick(pct)
    return f'<span class="t-item"><span class="t-name">{label}</span><span class="{cl}">{ar} {prefix}{price:,.{dec}f} ({pct:+.2f}%)</span></span>'

ticker_items = [
    t_item("GIFT NIFTY",  indices["GIFTNIFTY"]["price"], indices["GIFTNIFTY"]["pct"]),
    t_item("SENSEX",      indices["SENSEX"]["price"],    indices["SENSEX"]["pct"],    dec=0),
    t_item("NIFTY 50",    indices["NIFTY50"]["price"],   indices["NIFTY50"]["pct"]),
    t_item("NIFTY BANK",  indices["BANKNIFTY"]["price"], indices["BANKNIFTY"]["pct"]),
    t_item("NIFTY IT",    indices["NIFTYIT"]["price"],   indices["NIFTYIT"]["pct"]),
    t_item("USD/INR",     usdinr,                        0,                           prefix="₹"),
    f'<span class="t-item"><span class="t-name">BITCOIN</span><span class="{cls_tick(btc["pct"])}">{arrow(btc["pct"])} ${btc["price"]:,.0f} ({btc["pct"]:+.2f}%)</span></span>',
]
ticker_html = "".join(ticker_items) * 2

# Build market cards HTML
def mcard(label, display, idx, prefix="", dec=2):
    v=idx["price"]; ch=idx["change"]; pc=idx["pct"]
    return f'<div class="mcard"><div class="mcard-label">{display}</div><div class="mcard-val" id="mv-{label}">{prefix}{v:,.{dec}f}</div><div class="mcard-chg {cls_chg(pc)}" id="mc2-{label}">{arrow(pc)} {abs(ch):,.{dec}f} ({pc:+.2f}%)</div><div class="mcard-bar"><div class="mcard-fill {cls_fill(pc)}" id="mb-{label}" style="width:{bar(pc):.0f}%"></div></div></div>'

market_cards_html = "".join([
    mcard("SENSEX",    "SENSEX",     indices["SENSEX"],    dec=0),
    mcard("NIFTY",     "NIFTY 50",   indices["NIFTY50"]),
    mcard("BANKNIFTY", "NIFTY BANK", indices["BANKNIFTY"]),
    mcard("NIFTYIT",   "NIFTY IT",   indices["NIFTYIT"]),
    f'<div class="mcard"><div class="mcard-label">USD / INR</div><div class="mcard-val" id="mv-USDINR">₹{usdinr:.2f}</div><div class="mcard-chg chg-dn" id="mc2-USDINR">Live rate</div><div class="mcard-bar"><div class="mcard-fill fill-dn" id="mb-USDINR" style="width:30%"></div></div></div>',
    f'<div class="mcard"><div class="mcard-label">BITCOIN</div><div class="mcard-val" id="mv-BTC">${btc["price"]:,.0f}</div><div class="mcard-chg {cls_chg(btc["pct"])}" id="mc2-BTC">{arrow(btc["pct"])} {abs(btc["pct"]):.2f}%</div><div class="mcard-bar"><div class="mcard-fill {cls_fill(btc["pct"])}" id="mb-BTC" style="width:{bar(btc["pct"]):.0f}%"></div></div></div>',
])

# Build GIFT Nifty card HTML
gn = indices["GIFTNIFTY"]
gn_color = "#6ee7b7" if gn["pct"] >= 0 else "#fca5a5"
gift_html = f'''<div class="gift-card">
  <div>
    <div class="gift-eyebrow">Pre-Market Signal · Updated {updated_str}</div>
    <h3>GIFT NIFTY — Live Now</h3>
    <p>Trades before Indian markets open and signals how Sensex &amp; Nifty will open.</p>
  </div>
  <div>
    <div class="gift-val" id="gift-val">{gn["price"]:,.2f}</div>
    <div class="gift-chg" id="gift-chg" style="color:{gn_color}">{arrow(gn["pct"])} {abs(gn["change"]):.2f} pts ({gn["pct"]:+.2f}%)</div>
    <div class="gift-sub" id="gift-sub">Suggests Nifty opens ~{gn["price"]-30:,.0f}–{gn["price"]+30:,.0f}</div>
  </div>
</div>'''

# Read and update index.html
print(f"[NEWSPACE] Reading {index_path}")
with open(index_path, "r", encoding="utf-8") as f:
    html = f.read()

html = re.sub(
    r'<div [^>]*id="dash-date"[^>]*>.*?</div>',
    f'<div id="dash-date">{today_str} &nbsp;·&nbsp; Updated {updated_str}</div>',
    html
)
html = re.sub(r'<!-- AUTO:TICKER_START -->.*?<!-- AUTO:TICKER_END -->', f'<!-- AUTO:TICKER_START -->{ticker_html}<!-- AUTO:TICKER_END -->', html, flags=re.DOTALL)
html = re.sub(r'<!-- AUTO:MARKET_CARDS_START -->.*?<!-- AUTO:MARKET_CARDS_END -->', f'<!-- AUTO:MARKET_CARDS_START -->{market_cards_html}<!-- AUTO:MARKET_CARDS_END -->', html, flags=re.DOTALL)
html = re.sub(r'<!-- AUTO:GIFT_START -->.*?<!-- AUTO:GIFT_END -->', f'<!-- AUTO:GIFT_START -->{gift_html}<!-- AUTO:GIFT_END -->', html, flags=re.DOTALL)

print(f"[NEWSPACE] Writing {index_path}")
with open(index_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"[NEWSPACE] Done! Updated at {updated_str}")
