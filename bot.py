"""
Bot X - Polymarket Eleições 2026 + QueroApoiar Rankings
"""

import os, json, time, random, logging, requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import tweepy

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")

TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")
TWITTER_BEARER_TOKEN        = os.environ.get("TWITTER_BEARER_TOKEN", "")
POLYMARKET_SLUG             = os.environ.get("POLYMARKET_MARKET_SLUG", "brazil-presidential-election")

DATA_DIR   = Path(__file__).parent / "data"
LOGS_DIR   = Path(__file__).parent / "logs"
CHARTS_DIR = Path(__file__).parent / "charts"
for d in [DATA_DIR, LOGS_DIR, CHARTS_DIR]:
    d.mkdir(exist_ok=True)

# State files
POLY_LAST_POST_FILE  = DATA_DIR / "poly_last_post.json"
POLY_FIRST_DAY_FILE  = DATA_DIR / "poly_first_day.json"
POLY_WEEKLY_FILE     = DATA_DIR / "poly_weekly.json"
POLY_RANKING_FILE    = DATA_DIR / "poly_ranking.json"
POLY_PENDING_FILE    = DATA_DIR / "poly_pending.json"
QA_DAILY_FILE        = DATA_DIR / "qa_daily.json"
QA_MILESTONES_FILE   = DATA_DIR / "qa_milestones.json"
QA_LAST_ALERT_FILE   = DATA_DIR / "qa_last_alert.json"
QA_RANKING_FILE      = DATA_DIR / "qa_ranking.json"
DAILY_COUNT_FILE     = DATA_DIR / "daily_count.json"

CLOB_BASE  = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

QA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

MEDAL  = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
COLORS = ["#5B9BD5", "#4C9BE8", "#E6A817", "#E07B39", "#9B8FEE"]
BORDA_CORES = {1: "#FFD700", 2: "#C0C0C0", 3: "#CD7F32"}

RENAN_NAME  = "Renan Santos"
FLAVIO_NAME = "Flávio Bolsonaro"
LULA_NAME   = "Luiz Inácio Lula da Silva"
MISSAO_PARTIDO   = "Missão"
MISSAO_CANDIDATO = "Renan Santos"

MAX_DAILY_POSTS  = 5
ALERT_THRESHOLD  = 1.0
BIG_MOVE_THRESHOLD = 2.0

# QueroApoiar marcos
QA_MARCOS_PARTIDO = [m * 250_000 for m in range(7, 100)]   # 1.750k, 2.000k, ...
QA_MARCOS_CAND    = [m * 250_000 for m in range(5, 100)]   # 1.250k, 1.500k, ...

DISCLAIMER = (
    "⚠️ IMPORTANTE: Isso NÃO é pesquisa eleitoral.\n"
    "Os números abaixo refletem única e exclusivamente\n"
    "a percepção de usuários do Polymarket — pessoas\n"
    "do mundo inteiro que apostam no resultado das eleições.\n"
    "Não se trata de pesquisa eleitoral, não reflete a\n"
    "opinião dos eleitores brasileiros e não pode, sob\n"
    "nenhuma hipótese, ser comparado ou interpretado\n"
    "como intenção de voto."
)

SHORT_NAMES = {
    LULA_NAME: "Lula",
    "Flávio Bolsonaro": "Flávio",
    "Renan Santos": "Renan",
    "Fernando Haddad": "Haddad",
    "Romeu Zema": "Zema",
    "Michelle Bolsonaro": "Michelle",
    "Joaquim Barbosa": "J. Barbosa",
}
def sname(n): return SHORT_NAMES.get(n, n.split()[0])

OPENING_PHRASES = [
    "🌀 Guinada no cenário eleitoral!",
    "⚡ O tabuleiro das eleições mudou!",
    "🎯 Atenção: o jogo eleitoral está se movendo!",
    "📡 Sinal de alerta no Polymarket!",
    "🏁 Nova virada nas apostas eleitorais!",
]
LEADER_PHRASES = [
    "segue liderando com folga!",
    "ainda na frente — e não para de crescer.",
    "mantém a liderança.", "continua no topo.",
    "segura o 1º lugar.", "segue disparado na liderança.",
]
MARCO_PHRASES = [
    "atingiu o marco de", "acaba de ultrapassar",
    "superou a barreira de", "chegou em",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bot")


# ─────────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────────

def load_json(p):
    try: return json.load(open(p, encoding="utf-8")) if Path(p).exists() else None
    except: return None
def save_json(p, d): json.dump(d, open(p,"w",encoding="utf-8"), ensure_ascii=False, indent=2)

def load_daily_count():
    d = load_json(DAILY_COUNT_FILE) or {}
    today = datetime.now(BRAZIL_TZ).date().isoformat()
    if d.get("date") != today:
        d = {"date": today, "count": 0}
    return d

def increment_daily_count():
    d = load_daily_count()
    d["count"] = d.get("count", 0) + 1
    save_json(DAILY_COUNT_FILE, d)

def can_post_daily(big_move=False):
    if big_move: return True  # variação >2% não tem limite
    return load_daily_count().get("count", 0) < MAX_DAILY_POSTS


# ─────────────────────────────────────────────
# POLYMARKET
# ─────────────────────────────────────────────

def fetch_poly_data():
    try:
        resp = requests.get(f"{GAMMA_BASE}/events?slug={POLYMARKET_SLUG}", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data: return None
        markets = data[0].get("markets", [])
        cands = []
        for m in markets:
            name = m.get("groupItemTitle") or m.get("question","?")
            prices = m.get("outcomePrices","[]")
            if isinstance(prices,str): prices = json.loads(prices)
            tokens = m.get("clobTokenIds","[]")
            if isinstance(tokens,str): tokens = json.loads(tokens)
            if prices and tokens:
                cands.append({"candidate": name,
                               "probability": round(float(prices[0])*100,1),
                               "token_id": tokens[0]})
        if not cands: return None
        cands.sort(key=lambda x: x["probability"], reverse=True)
        return cands
    except Exception as e:
        log.exception("Erro Polymarket: %s", e); return None

def fetch_price_history(token_id, since=None):
    try:
        resp = requests.get(f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval":"max","fidelity":1440}, timeout=20)
        resp.raise_for_status()
        result = []
        for pt in resp.json().get("history",[]):
            ts, price = pt.get("t"), pt.get("p")
            if ts and price is not None:
                dt = datetime.fromtimestamp(ts, tz=BRAZIL_TZ)
                if since and dt < since: continue
                result.append({"date": dt, "price": round(float(price)*100,1)})
        return result
    except Exception as e:
        log.warning("Erro histórico %s: %s", token_id[:10], e); return []

def smooth(prices, w=3):
    if len(prices) < w: return prices
    arr = np.array(prices, dtype=float)
    s = np.convolve(arr, np.ones(w)/w, mode="same")
    s[:w]=arr[:w]; s[-w:]=arr[-w:]; return s.tolist()

def get_trend(token_id):
    h = fetch_price_history(token_id)
    if len(h) < 4: return "↔"
    d = [x["price"] for x in h[-4:]]
    return "▲" if d[-1]-d[0]>0.3 else ("▼" if d[-1]-d[0]<-0.3 else "↔")


# ─────────────────────────────────────────────
# GRÁFICOS POLYMARKET
# ─────────────────────────────────────────────

def build_poly_lines(top5):
    fig, ax = plt.subplots(figsize=(10,10))
    fig.patch.set_facecolor("#0D1117"); ax.set_facecolor("#0D1117")
    has_data = False; legend_items = []
    for idx, c in enumerate(top5):
        tid = c.get("token_id","")
        if not tid: continue
        h = fetch_price_history(tid)
        if not h: continue
        has_data = True
        dates = [x["date"] for x in h]
        prices = smooth([x["price"] for x in h])
        color = COLORS[idx % len(COLORS)]
        ax.plot(dates, prices, color=color, linewidth=2, alpha=0.92, solid_capstyle="round")
        ax.annotate(f"{prices[-1]:.0f}%", xy=(dates[-1], prices[-1]),
                    xytext=(5,0), textcoords="offset points",
                    color=color, fontsize=9, fontweight="bold", va="center", annotation_clip=False)
        legend_items.append((color, c["candidate"], c["probability"]))
    if not has_data: plt.close(); return None
    ax.yaxis.set_label_position("right"); ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.0f}%"))
    ax.tick_params(axis="y", colors="#666", labelsize=9, length=0, pad=6)
    ax.tick_params(axis="x", colors="#666", labelsize=9, length=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(axis="y", color="#1E1E2E", linewidth=0.8); ax.grid(axis="x", visible=False)
    ax.set_axisbelow(True)
    for spine in ax.spines.values(): spine.set_visible(False)
    plt.subplots_adjust(right=0.87, top=0.82, left=0.04, bottom=0.08)
    x_cur = 0.01; y_leg = 0.94
    for color, name, prob in legend_items:
        fig.text(x_cur, y_leg, "●", color=color, fontsize=10, transform=fig.transFigure, va="center")
        label = f" {sname(name)}  {prob:.1f}%"
        fig.text(x_cur+0.018, y_leg, label, color="#CCCCCC", fontsize=8, transform=fig.transFigure, va="center")
        x_cur += 0.02 + len(label)*0.0055
        if x_cur > 0.95: x_cur=0.01; y_leg-=0.06
    fig.text(0.99,0.01, f"Polymarket • {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')}",
             ha="right", va="bottom", color="#333", fontsize=7, transform=fig.transFigure)
    path = CHARTS_DIR / f"poly_lines_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); return path

def build_poly_single(c):
    tid = c.get("token_id","")
    if not tid: return None
    h = fetch_price_history(tid)
    if not h: return None
    dates = [x["date"] for x in h]
    prices = smooth([x["price"] for x in h])
    name = c["candidate"]; prob = c["probability"]
    color = "#E6A817" if name==RENAN_NAME else ("#4C9BE8" if name==FLAVIO_NAME else "#5B9BD5")
    p_min=min(prices); p_max=max(prices)
    margin = max((p_max-p_min)*0.15, 1.5)
    y_min=max(0,p_min-margin); y_max=p_max+margin
    delta_total = round(prices[-1]-prices[0],1)
    delta_str = f"▲{delta_total:.1f}%" if delta_total>=0 else f"▼{abs(delta_total):.1f}%"
    fig, ax = plt.subplots(figsize=(10,10))
    fig.patch.set_facecolor("#0D1117"); ax.set_facecolor("#0D1117")
    ax.plot(dates, prices, color=color, linewidth=2.2, solid_capstyle="round")
    ax.fill_between(dates, prices, y_min, color=color, alpha=0.08)
    ax.scatter([dates[-1]], [prices[-1]], color=color, s=60, zorder=5)
    ax.set_ylim(y_min, y_max)
    ax.yaxis.set_label_position("right"); ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.0f}%"))
    ax.tick_params(axis="y", colors="#666", labelsize=9, length=0, pad=6)
    ax.tick_params(axis="x", colors="#666", labelsize=9, length=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(axis="y", color="#1A1A2E", linewidth=0.8); ax.grid(axis="x", visible=False)
    ax.set_axisbelow(True)
    for spine in ax.spines.values(): spine.set_visible(False)
    fig.text(0.04, 0.93, sname(name), color="white", fontsize=13, fontweight="bold", transform=fig.transFigure)
    fig.text(0.04, 0.86, f"{prob:.0f}% chance", color=color, fontsize=12, fontweight="bold", transform=fig.transFigure)
    fig.text(0.22, 0.86, delta_str, color=color, fontsize=11, transform=fig.transFigure)
    fig.text(0.99, 0.01, f"Polymarket • {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')}",
             ha="right", va="bottom", color="#333", fontsize=7, transform=fig.transFigure)
    plt.subplots_adjust(right=0.88, top=0.80, left=0.04, bottom=0.08)
    path = CHARTS_DIR / f"poly_single_{sname(name).lower()}_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); return path


# ─────────────────────────────────────────────
# GRÁFICOS QUEROAPOIAR
# ─────────────────────────────────────────────

def download_img(url, size=(90,90)):
    try:
        from PIL import Image
        from io import BytesIO
        r = requests.get(url, headers=QA_HEADERS, timeout=10)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGBA")
        img = img.resize(size, Image.LANCZOS)
        return np.array(img)
    except: return None

def build_qa_grid(items, title):
    fig, axes = plt.subplots(2,2, figsize=(9,9))
    fig.patch.set_facecolor("#0D1117")
    plt.subplots_adjust(hspace=0.12, wspace=0.10, top=0.90, bottom=0.04, left=0.03, right=0.97)
    fig.text(0.5,0.95, title, ha="center", color="white", fontsize=13, fontweight="bold", transform=fig.transFigure)
    fig.text(0.5,0.91, f"QueroApoiar • {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y')}",
             ha="center", color="#555", fontsize=9, transform=fig.transFigure)
    for idx in range(4):
        row,col = divmod(idx,2)
        ax = axes[row][col]
        ax.set_facecolor("#161B22"); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")
        pos = idx+1; borda = BORDA_CORES.get(pos,"#30363D"); lw = 2.0 if pos==1 else (1.2 if pos<=3 else 0.5)
        rect = mpatches.FancyBboxPatch((0.03,0.03),0.94,0.94, boxstyle="round,pad=0.02",
                    linewidth=lw, edgecolor=borda, facecolor="#161B22", transform=ax.transAxes, zorder=0)
        ax.add_patch(rect)
        if idx >= len(items): continue
        item = items[idx]
        crown = "👑 " if pos==1 else ""; cor = BORDA_CORES.get(pos,"#8899A6")
        ax.text(0.5,0.92, f"{crown}{pos}º", transform=ax.transAxes,
                color=cor, fontsize=11, fontweight="bold", va="top", ha="center")
        img_arr = download_img(item["img_url"]) if item.get("img_url") else None
        if img_arr is not None:
            ib = OffsetImage(img_arr, zoom=0.52)
            ab = AnnotationBbox(ib,(0.5,0.57), frameon=False, xycoords="axes fraction")
            ax.add_artist(ab)
        else:
            circ = plt.Circle((0.5,0.57),0.20,color="#2D3741",transform=ax.transAxes,zorder=2)
            ax.add_patch(circ)
            ax.text(0.5,0.57,item["name"][0].upper(),transform=ax.transAxes,
                    color="#8899A6",fontsize=22,fontweight="bold",ha="center",va="center",zorder=3)
        nome = item["name"]; nome = nome[:12]+"…" if len(nome)>14 else nome
        ax.text(0.5,0.31,nome,transform=ax.transAxes,color="white",fontsize=9.5,fontweight="bold",ha="center",va="center")
        ax.text(0.5,0.18,item["valor_str"],transform=ax.transAxes,color="#00BA7C",fontsize=8.5,fontweight="bold",ha="center",va="center")
        apoio = item.get("apoio",0)
        if apoio:
            apoio_txt = f"{int(apoio):,} apoiadores".replace(",",".") if isinstance(apoio,(int,float)) else str(apoio)
            ax.text(0.5,0.07,apoio_txt,transform=ax.transAxes,color="#8899A6",fontsize=7.5,ha="center",va="center")
    path = CHARTS_DIR / f"qa_grid_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); return path

def build_qa_single_card(item, pos=1, subtitle=""):
    fig, ax = plt.subplots(figsize=(5,5))
    fig.patch.set_facecolor("#0D1117"); ax.set_facecolor("#0D1117")
    ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")
    borda = BORDA_CORES.get(pos,"#30363D"); lw = 2.5 if pos==1 else 1.5
    rect = mpatches.FancyBboxPatch((0.05,0.05),0.90,0.90,boxstyle="round,pad=0.03",
                linewidth=lw,edgecolor=borda,facecolor="#161B22",transform=ax.transAxes,zorder=0)
    ax.add_patch(rect)
    crown = "👑 " if pos==1 else ""; cor = BORDA_CORES.get(pos,"#8899A6")
    ax.text(0.5,0.91,f"{crown}{pos}º lugar",transform=ax.transAxes,color=cor,fontsize=13,fontweight="bold",ha="center",va="top")
    img_arr = download_img(item["img_url"],(110,110)) if item.get("img_url") else None
    if img_arr is not None:
        ib = OffsetImage(img_arr,zoom=0.65)
        ab = AnnotationBbox(ib,(0.5,0.58),frameon=False,xycoords="axes fraction")
        ax.add_artist(ab)
    else:
        circ = plt.Circle((0.5,0.58),0.25,color="#2D3741",transform=ax.transAxes,zorder=2)
        ax.add_patch(circ)
        ax.text(0.5,0.58,item["name"][0].upper(),transform=ax.transAxes,color="#8899A6",fontsize=28,fontweight="bold",ha="center",va="center",zorder=3)
    ax.text(0.5,0.31,item["name"],transform=ax.transAxes,color="white",fontsize=11,fontweight="bold",ha="center",va="center")
    ax.text(0.5,0.19,item["valor_str"],transform=ax.transAxes,color="#00BA7C",fontsize=10,fontweight="bold",ha="center",va="center")
    if subtitle:
        ax.text(0.5,0.09,subtitle,transform=ax.transAxes,color="#8899A6",fontsize=8.5,ha="center",va="center")
    path = CHARTS_DIR / f"qa_card_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path,dpi=150,bbox_inches="tight",facecolor="#0D1117"); plt.close(); return path


# ─────────────────────────────────────────────
# TEXTOS POLYMARKET
# ─────────────────────────────────────────────

def fv(delta):
    if delta is None: return " —"
    if delta>0: return f" ▲ +{delta:.1f}pp"
    if delta<0: return f" ▼ {delta:.1f}pp"
    return " ↔ 0.0pp"

def best_delta(d_last, d_day):
    if d_day is None: return d_last
    if -1 < d_day < 1: return d_last
    return d_day if abs(d_day)>=abs(d_last) else d_last

def alert_header(name, delta):
    abs_d=abs(delta); subindo=delta>0; short=sname(name)
    if name==RENAN_NAME:
        if abs_d>=4: return random.choice([f"🚀 RENAN DISPARA! +{abs_d:.1f}pp hoje!",f"🚀 RENAN EXPLODE! +{abs_d:.1f}pp nas apostas!"])
        elif abs_d>=2: return random.choice([f"🔥 Renan Santos em ALTA — sobe {abs_d:.1f}pp!",f"🔥 Que dia pro Renan! +{abs_d:.1f}pp acumulados"])
        else: return random.choice([f"⭐ ALERTA — Renan Santos avança +{abs_d:.1f}pp",f"⭐ ALERTA — Renan ganhando terreno +{abs_d:.1f}pp"])
    else:
        if subindo:
            if abs_d>=4: return random.choice([f"🚀 {short} acumula +{abs_d:.1f}pp hoje",f"📈 Grande movimento — {short} sobe {abs_d:.1f}pp"])
            elif abs_d>=2: return random.choice([f"🚨 URGENTE — 📈 {short} em alta — +{abs_d:.1f}pp",f"🚨 URGENTE — 📈 {name} ganhando força +{abs_d:.1f}pp"])
            else: return random.choice([f"📊 ATUALIZAÇÃO — {short} subiu {abs_d:.1f}pp",f"📊 ATUALIZAÇÃO — {name} avança {abs_d:.1f}pp"])
        else:
            if abs_d>=4: return random.choice([f"📉 DERRETEU! {name} despenca {abs_d:.1f}pp",f"📉 QUE TOMBO! {short} perde {abs_d:.1f}pp"])
            elif abs_d>=2: return random.choice([f"🚨 URGENTE — 📉 {short} em baixa — -{abs_d:.1f}pp",f"🚨 URGENTE — 📉 {name} recua {abs_d:.1f}pp"])
            else: return random.choice([f"📊 ATUALIZAÇÃO — {short} caiu {abs_d:.1f}pp",f"📊 ATUALIZAÇÃO — {name} recua {abs_d:.1f}pp"])

def renan_flavio_line(cands):
    r = next((c for c in cands if c["candidate"]==RENAN_NAME),None)
    f = next((c for c in cands if c["candidate"]==FLAVIO_NAME),None)
    if r and f: return f"📌 Renan se aproxima de Flávio: diferença {round(f['probability']-r['probability'],1)}pp"
    return None

def build_poly_alert_tweet(cands, first_day, last_post, trigger_name, delta_last, delta_day):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    last_map  = {c["candidate"]:c["probability"] for c in (last_post or {}).get("candidates",[])}
    first_map = {c["candidate"]:c["probability"] for c in (first_day or {}).get("candidates",[])}
    use_delta = best_delta(delta_last, delta_day)
    header = alert_header(trigger_name, use_delta)
    lines = [
        DISCLAIMER, "",
        random.choice(OPENING_PHRASES), "",
        header, "",
        "🗳 Eleição Presidencial Brasil 2026",
        f"📊 Polymarket — {now_str}", "",
    ]
    for i,c in enumerate(cands[:5]):
        name=c["candidate"]; prob=c["probability"]
        dl = round(prob-last_map[name],1) if name in last_map else None
        dd = round(prob-first_map[name],1) if name in first_map else None
        ud = best_delta(dl,dd) if dl is not None else None
        day_str = f" | dia: {'+' if (dd or 0)>0 else ''}{dd:.1f}pp" if dd is not None else ""
        lines.append(f"{MEDAL[i]} {sname(name):<8} | {prob:4.1f}%{fv(ud)}{day_str}")
    add_renan = (trigger_name==RENAN_NAME and delta_last>0) or (trigger_name==FLAVIO_NAME and delta_last<0)
    if add_renan:
        extra = renan_flavio_line(cands)
        if extra: lines += ["", extra]
    return "\n".join(lines)

def build_poly_weekly_tweet(cands, weekly):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    w_map = {c["candidate"]:c["probability"] for c in (weekly or {}).get("candidates",[])}
    best_name=None; best_delta_val=0
    prev_lula=None; prev_flavio=None
    lines = [
        DISCLAIMER, "",
        f"📅 Resumo da semana — {now_str}", "",
        "Como os candidatos variaram nos últimos 7 dias:", "",
    ]
    for i,c in enumerate(cands[:5]):
        name=c["candidate"]; prob=c["probability"]
        delta = round(prob-w_map[name],1) if name in w_map else None
        lines.append(f"{MEDAL[i]} {sname(name):<8} | {prob:4.1f}%{fv(delta)} na semana")
        if delta and abs(delta)>abs(best_delta_val): best_delta_val=delta; best_name=name
        if name==LULA_NAME: prev_lula=w_map.get(name)
        if name==FLAVIO_NAME: prev_flavio=w_map.get(name)
    if best_name:
        dir_str = "maior avanço" if best_delta_val>0 else "maior queda"
        lines += ["", f"📌 Destaque: {sname(best_name)}", f"{dir_str} — {fv(best_delta_val).strip()} em 7 dias"]
    if prev_lula and prev_flavio:
        lines += ["", "Comparando com 7 dias atrás:",
                  f"Lula estava em {prev_lula:.1f}% | Flávio em {prev_flavio:.1f}%"]
    return "\n".join(lines)

def build_poly_overtake_tweet(overtaker, overtaken, cands):
    o1 = next((c for c in cands if c["candidate"]==overtaker),None)
    o2 = next((c for c in cands if c["candidate"]==overtaken),None)
    if not o1 or not o2: return None
    i1 = next((i for i,c in enumerate(cands) if c["candidate"]==overtaker),0)
    i2 = next((i for i,c in enumerate(cands) if c["candidate"]==overtaken),1)
    historic = (overtaker==RENAN_NAME and overtaken==FLAVIO_NAME) or \
               (overtaker==FLAVIO_NAME and overtaken==LULA_NAME)
    header = "🚨 VIRADA HISTÓRICA NAS APOSTAS!" if historic else "📊 MUDANÇA NO RANKING!"
    footer = "O favoritismo mudou de mão\nnas apostas internacionais." if historic else ""
    lines = [DISCLAIMER, "", header, "",
             f"{overtaker} ultrapassou {overtaken} no Polymarket!", "",
             f"{MEDAL[i1]} {sname(overtaker)}: {o1['probability']:.1f}% ▲",
             f"{MEDAL[i2]} {sname(overtaken)}: {o2['probability']:.1f}% ▼"]
    if footer: lines += ["", footer]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# TEXTOS QUEROAPOIAR
# ─────────────────────────────────────────────

def fmt_valor(v):
    return "R$ " + f"{v:,.2f}".replace(",","X").replace(".",",").replace("X",".")

def fmt_marco(v):
    if v>=1_000_000:
        n=v/1_000_000; s=f"{n:.2f}".rstrip("0").rstrip(".").replace(".",",")
        return f"R$ {s} milhão"
    return f"R$ {int(v/1_000)} mil"

def fmt_delta_qa(new_val, old_val):
    if old_val is None: return ""
    d = new_val-old_val
    if abs(d)<0.01: return "\n   ↔ sem variação"
    sign="▲ +" if d>0 else "▼ "
    return f"\n   {sign}{fmt_valor(abs(d))} desde o último post"

def build_qa_marco_tweet(name, valor, marco, apoio, old_valor=None):
    ph = random.choice(MARCO_PHRASES)
    dv = fmt_delta_qa(valor, old_valor)
    lines = [
        f"🚀 MARCO HISTÓRICO — {name}!", "",
        f"{name} {ph}",
        f"{fmt_marco(marco)} em arrecadação!", "",
        f"💰 Total: {fmt_valor(valor)}{dv}", "",
        f"👥 {int(apoio):,} apoiadores".replace(",","."),
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# TWITTER
# ─────────────────────────────────────────────

def get_client():
    return tweepy.Client(bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY, consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN, access_token_secret=TWITTER_ACCESS_TOKEN_SECRET)

def get_api_v1():
    auth = tweepy.OAuth1UserHandler(TWITTER_API_KEY, TWITTER_API_SECRET,
                                     TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET)
    return tweepy.API(auth)

def post_tweet(text, chart_path=None, retries=5, retry_delay=240):
    for attempt in range(1, retries+1):
        try:
            media_id = None
            if chart_path and Path(chart_path).exists():
                try:
                    media = get_api_v1().media_upload(str(chart_path))
                    media_id = media.media_id
                except Exception as e: log.warning("Mídia: %s", e)
            kwargs = {"text": text}
            if media_id: kwargs["media_ids"] = [media_id]
            resp = get_client().create_tweet(**kwargs)
            tid = resp.data["id"]; log.info("Tweet: %s", tid)
            return tid
        except Exception as e:
            log.warning("Tentativa %d: %s", attempt, e)
            if attempt < retries: time.sleep(retry_delay)
    return None


# ─────────────────────────────────────────────
# POSTS POLYMARKET
# ─────────────────────────────────────────────

def run_poly_weekly():
    log.info("Resumo semanal Polymarket")
    cands = fetch_poly_data()
    if not cands: return
    weekly = load_json(POLY_WEEKLY_FILE)
    text   = build_poly_weekly_tweet(cands, weekly)
    chart  = build_poly_lines(cands[:5])
    tid    = post_tweet(text, chart)
    if tid:
        save_json(POLY_WEEKLY_FILE, {"date": datetime.now(BRAZIL_TZ).date().isoformat(), "candidates": cands})
        save_json(POLY_LAST_POST_FILE, {"tweet_id": tid, "timestamp": datetime.now(BRAZIL_TZ).isoformat(), "candidates": cands})
        increment_daily_count()
        log.info("Resumo semanal OK.")

def check_poly_alerts():
    if not can_post_daily():
        log.info("Limite diário de posts atingido.")
        return

    cands = fetch_poly_data()
    if not cands: return

    last_post = load_json(POLY_LAST_POST_FILE)
    if not last_post: return

    today_str = datetime.now(BRAZIL_TZ).date().isoformat()
    first_day = load_json(POLY_FIRST_DAY_FILE)
    if not first_day or first_day.get("date") != today_str:
        save_json(POLY_FIRST_DAY_FILE, {"date": today_str, "candidates": cands})
        first_day = {"date": today_str, "candidates": cands}

    last_map  = {c["candidate"]:c["probability"] for c in last_post.get("candidates",[])}
    first_map = {c["candidate"]:c["probability"] for c in first_day.get("candidates",[])}

    # Ultrapassagens
    last_rank = load_json(POLY_RANKING_FILE) or {}
    new_rank  = [c["candidate"] for c in cands[:5]]
    if last_rank.get("ranking") and new_rank != last_rank["ranking"]:
        for pos, name in enumerate(new_rank):
            if pos < len(last_rank["ranking"]) and name != last_rank["ranking"][pos]:
                overtaken = last_rank["ranking"][pos]
                if overtaken in new_rank and new_rank.index(overtaken) > pos:
                    text  = build_poly_overtake_tweet(name, overtaken, cands)
                    chart = build_poly_lines(cands[:5])
                    if text:
                        tid = post_tweet(text, chart)
                        if tid:
                            save_json(POLY_LAST_POST_FILE, {"tweet_id":tid,"timestamp":datetime.now(BRAZIL_TZ).isoformat(),"candidates":cands})
                            increment_daily_count()
                    break
    save_json(POLY_RANKING_FILE, {"ranking": new_rank})

    if not can_post_daily(): return

    # Alertas de variação
    for c in cands[:5]:
        name = c["candidate"]; prob = c["probability"]
        prev = last_map.get(name)
        if prev is None: continue
        delta_last = round(prob-prev, 1)
        if abs(delta_last) < ALERT_THRESHOLD: continue
        if name==RENAN_NAME and delta_last<0:
            log.info("Queda do Renan ignorada."); continue

        big_move = abs(delta_last) >= BIG_MOVE_THRESHOLD
        if not can_post_daily(big_move=big_move): continue

        delta_day = round(prob-first_map[name],1) if name in first_map else delta_last
        text  = build_poly_alert_tweet(cands, first_day, last_post, name, delta_last, delta_day)
        chart = build_poly_single(c)
        tid   = post_tweet(text, chart)
        if tid:
            save_json(POLY_LAST_POST_FILE, {"tweet_id":tid,"timestamp":datetime.now(BRAZIL_TZ).isoformat(),"candidates":cands})
            increment_daily_count()
        break


# ─────────────────────────────────────────────
# POSTS QUEROAPOIAR
# ─────────────────────────────────────────────

def scrape_parties():
    try:
        resp = requests.get("https://api.queroapoiar.com.br/api/stats/partidos/exec",
                            headers=QA_HEADERS, timeout=15)
        resp.raise_for_status()
        raw = resp.json().get("partidos") or []
        LOGO_MAP = {
            "Missão":"missao","Novo":"novo","PSOL":"psol","Psol":"psol",
            "PCdoB":"pcdob","PT":"pt","PSD":"psd","PL":"pl","Podemos":"podemos",
            "UP":"up","PP":"pp","Solidariedade":"solidariedade","PSB":"psb",
            "MDB":"mdb","PDT":"pdt","DC":"dc","Republicanos":"republicanos",
            "PSDB":"psdb","PV":"pv","Rede":"rede","Agir":"agir",
            "União Brasil":"uniao-brasil","Avante":"avante","PRD":"prd",
        }
        parties = []
        for item in raw:
            nome=item.get("nome",""); total=float(item.get("total") or 0)
            apoio=item.get("apoiadores") or 0
            slug=LOGO_MAP.get(nome,nome.lower().replace(" ","-"))
            parties.append({"name":nome,"valor":total,
                             "valor_str":fmt_valor(total),
                             "img_url":f"https://queroapoiar.com.br/assets/partidos/{slug}.webp",
                             "apoio":apoio})
        parties.sort(key=lambda x: x["valor"], reverse=True)
        return parties
    except Exception as e:
        log.exception("Erro partidos: %s", e); return None

def scrape_candidates_qa():
    try:
        for url in [
            "https://api.queroapoiar.com.br/api/stats/campanhas/exec?ano=2026",
            "https://api.queroapoiar.com.br/api/stats/candidatos/exec",
        ]:
            try:
                resp = requests.get(url, headers=QA_HEADERS, timeout=15)
                if resp.status_code != 200: continue
                data = resp.json()
                items = data.get("candidatos") or data.get("campanhas") or []
                if not items: continue
                cands = []
                for item in items:
                    cargo=str(item.get("cargo","")).lower()
                    if cargo and "president" not in cargo and "presid" not in cargo: continue
                    nome=item.get("nome") or ""; total=float(item.get("total") or 0)
                    apoio=int(item.get("apoiadores") or 0)
                    img=item.get("foto") or item.get("image") or ""
                    if img and img.startswith("/"): img="https://queroapoiar.com.br"+img
                    cands.append({"name":nome,"valor":total,"valor_str":fmt_valor(total),"img_url":img,"apoio":apoio})
                if cands:
                    cands.sort(key=lambda x: x["valor"], reverse=True)
                    return cands
            except: continue
        return None
    except Exception as e:
        log.exception("Erro candidatos QA: %s", e); return None

def bootstrap_qa_milestones():
    ms = load_json(QA_MILESTONES_FILE) or {}
    changed = False
    parties = scrape_parties()
    cands   = scrape_candidates_qa()
    if parties:
        mp = next((p for p in parties if p["name"]==MISSAO_PARTIDO),None)
        if mp:
            val=mp["valor"]; key="missao_p"
            highest=max((m for m in QA_MARCOS_PARTIDO if val>=m),default=0)
            if highest>ms.get(key,0):
                ms[key]=highest; ms["missao_p_val"]=val; changed=True
                log.info("Bootstrap QA partido: %s", fmt_marco(highest))
    if cands:
        rc = next((c for c in cands if c["name"]==MISSAO_CANDIDATO),None)
        if rc:
            val=rc["valor"]; key="renan_qa"
            highest=max((m for m in QA_MARCOS_CAND if val>=m),default=0)
            if highest>ms.get(key,0):
                ms[key]=highest; ms["renan_qa_val"]=val; changed=True
                log.info("Bootstrap QA candidato: %s", fmt_marco(highest))
    if changed: save_json(QA_MILESTONES_FILE,ms); log.info("Bootstrap QA concluído.")

def check_qa_marcos():
    ms = load_json(QA_MILESTONES_FILE) or {}
    parties = scrape_parties()
    cands   = scrape_candidates_qa()

    if parties:
        mp = next((p for p in parties if p["name"]==MISSAO_PARTIDO),None)
        if mp:
            val=mp["valor"]; apoio=mp.get("apoio",0)
            key="missao_p"; old_val=ms.get("missao_p_val")
            last_m=ms.get(key,0)
            next_m=next((m for m in QA_MARCOS_PARTIDO if m>last_m and val>=m),None)
            if next_m:
                text  = build_qa_marco_tweet(MISSAO_PARTIDO,val,next_m,apoio,old_val)
                apoio_txt=f"{int(apoio):,} apoiadores".replace(",",".")
                chart = build_qa_single_card(mp,1,apoio_txt)
                tid   = post_tweet(text,chart)
                if tid:
                    ms[key]=next_m; ms["missao_p_val"]=val
                    save_json(QA_MILESTONES_FILE,ms)
                    log.info("Marco QA partido: %s", fmt_marco(next_m))

    if cands:
        rc = next((c for c in cands if c["name"]==MISSAO_CANDIDATO),None)
        if rc:
            val=rc["valor"]; apoio=rc.get("apoio",0)
            key="renan_qa"; old_val=ms.get("renan_qa_val")
            last_m=ms.get(key,0)
            next_m=next((m for m in QA_MARCOS_CAND if m>last_m and val>=m),None)
            if next_m:
                text  = build_qa_marco_tweet(MISSAO_CANDIDATO,val,next_m,apoio,old_val)
                apoio_txt=f"{int(apoio):,} apoiadores".replace(",",".")
                chart = build_qa_single_card(rc,1,apoio_txt)
                tid   = post_tweet(text,chart)
                if tid:
                    ms[key]=next_m; ms["renan_qa_val"]=val
                    save_json(QA_MILESTONES_FILE,ms)
                    log.info("Marco QA candidato: %s", fmt_marco(next_m))


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

def next_time(hour, minute=0):
    now = datetime.now(BRAZIL_TZ)
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if t <= now: t += timedelta(days=1)
    return t

def run_scheduler():
    log.info("Bot iniciado — Polymarket + QueroApoiar")
    bootstrap_qa_milestones()

    next_weekly = next_time(21)
    log.info("Próximo resumo semanal: %s", next_weekly.strftime("%d/%m %H:%M"))

    while True:
        now     = datetime.now(BRAZIL_TZ)
        weekday = now.weekday()  # 4=sexta

        # Resumo semanal Polymarket — sexta às 21h
        if weekday == 4 and now >= next_weekly:
            w = load_json(POLY_WEEKLY_FILE)
            if not w or w.get("date") != now.date().isoformat():
                run_poly_weekly()
            next_weekly = next_time(21)

        # Alertas Polymarket — qualquer hora
        check_poly_alerts()

        # Marcos QueroApoiar — qualquer hora (sem limite diário)
        check_qa_marcos()

        time.sleep(300)


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--test-weekly":     run_poly_weekly()
    elif arg == "--test-alerts":   check_poly_alerts()
    elif arg == "--test-qa":       check_qa_marcos()
    else: run_scheduler()
