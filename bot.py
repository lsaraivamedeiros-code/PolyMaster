"""
Bot X - Eleições Brasil 2026 — versão completa
"""

import os, json, time, random, logging, requests, tweepy
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")

TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")
TWITTER_BEARER_TOKEN        = os.environ.get("TWITTER_BEARER_TOKEN", "")
POLYMARKET_MARKET_SLUG      = os.environ.get("POLYMARKET_MARKET_SLUG", "brazil-presidential-election")

DATA_DIR              = Path(__file__).parent / "data"
LOGS_DIR              = Path(__file__).parent / "logs"
CHARTS_DIR            = Path(__file__).parent / "charts"
LAST_POST_FILE        = DATA_DIR / "last_post.json"
FIRST_POST_DAY_FILE   = DATA_DIR / "first_post_day.json"
KNOWN_CANDIDATES_FILE = DATA_DIR / "known_candidates.json"
LAST_RANKING_FILE     = DATA_DIR / "last_ranking.json"
WEEKLY_FILE           = DATA_DIR / "weekly_baseline.json"
PENDING_FILE          = DATA_DIR / "pending_actions.json"
RECORDS_FILE          = DATA_DIR / "records.json"

for d in [DATA_DIR, LOGS_DIR, CHARTS_DIR]:
    d.mkdir(exist_ok=True)

ALERT_THRESHOLD  = 1.0
CLOB_BASE        = "https://clob.polymarket.com"
GAMMA_BASE       = "https://gamma-api.polymarket.com"
MARCH_CUTOFF     = datetime(2026, 3, 1, tzinfo=BRAZIL_TZ)

RENAN_NAME  = "Renan Santos"
FLAVIO_NAME = "Flávio Bolsonaro"
LULA_NAME   = "Luiz Inácio Lula da Silva"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("eleicoes_bot")

COLORS = ["#5B9BD5", "#4C9BE8", "#E6A817", "#E07B39", "#9B8FEE"]
MEDAL  = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

# Mapa de nomes curtos para exibição nos posts
SHORT_NAMES = {
    "Luiz Inácio Lula da Silva": "Lula",
    "Flávio Bolsonaro": "Flávio",
    "Renan Santos": "Renan Santos",
    "Fernando Haddad": "Haddad",
    "Romeu Zema": "Zema",
    "Michelle Bolsonaro": "Michelle",
    "Jair Bolsonaro": "J. Bolsonaro",
    "Geraldo Alckmin": "Alckmin",
    "Ciro Gomes": "Ciro",
    "Joaquim Barbosa": "J. Barbosa",
}

def short_name(name):
    return SHORT_NAMES.get(name, name.split()[0])

# Alterna entre gráfico de linhas e barras para posts agendados
_chart_toggle = {"use_bars": False}


# ─────────────────────────────────────────────
# POLYMARKET
# ─────────────────────────────────────────────

def fetch_polymarket_data():
    try:
        url = f"{GAMMA_BASE}/events?slug={POLYMARKET_MARKET_SLUG}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data: return None
        markets = data[0].get("markets", [])
        if not markets: return None
        candidates = []
        for market in markets:
            name = market.get("groupItemTitle") or market.get("question", "?")
            prices = market.get("outcomePrices", "[]")
            if isinstance(prices, str): prices = json.loads(prices)
            tokens = market.get("clobTokenIds", "[]")
            if isinstance(tokens, str): tokens = json.loads(tokens)
            if prices and tokens:
                candidates.append({
                    "candidate": name,
                    "probability": round(float(prices[0]) * 100, 1),
                    "token_id": tokens[0],
                })
        if not candidates: return None
        candidates.sort(key=lambda x: x["probability"], reverse=True)
        log.info("Top5: %s", [(c["candidate"], c["probability"]) for c in candidates[:5]])
        return candidates
    except Exception as e:
        log.exception("Erro Polymarket: %s", e); return None


def fetch_price_history(token_id, since=None):
    """Busca histórico. Se `since` for datetime, filtra a partir dessa data."""
    try:
        resp = requests.get(
            f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval": "max", "fidelity": 1440},
            timeout=20,
        )
        resp.raise_for_status()
        result = []
        for pt in resp.json().get("history", []):
            ts, price = pt.get("t"), pt.get("p")
            if ts and price is not None:
                dt = datetime.fromtimestamp(ts, tz=BRAZIL_TZ)
                if since and dt < since: continue
                result.append({"date": dt, "price": round(float(price)*100, 1)})
        return result
    except Exception as e:
        log.warning("Erro histórico %s: %s", token_id[:10], e); return []


def smooth_series(prices, window=3):
    if len(prices) < window: return prices
    arr = np.array(prices, dtype=float)
    s = np.convolve(arr, np.ones(window)/window, mode="same")
    s[:window] = arr[:window]; s[-window:] = arr[-window:]
    return s.tolist()


def get_trend_3days(token_id):
    h = fetch_price_history(token_id)
    if len(h) < 4: return "↔"
    d = [x["price"] for x in h[-4:]]
    return "▲" if d[-1]-d[0] > 0.3 else ("▼" if d[-1]-d[0] < -0.3 else "↔")


# ─────────────────────────────────────────────
# RECORDES
# ─────────────────────────────────────────────

def load_records():
    r = load_json(RECORDS_FILE)
    return r if r else {}

def save_records(r): save_json(RECORDS_FILE, r)

def check_records(candidates):
    """
    Verifica recordes e retorna lista de eventos.
    Limites:
    - renan_alltime: so posta se variacao >= 0.5pp desde o ultimo post de recorde no dia
    - renan_weekly:  maximo 1 post por dia
    """
    events    = []
    records   = load_records()
    today_str = datetime.now(BRAZIL_TZ).date().isoformat()

    for c in candidates[:5]:
        name = c["candidate"]
        prob = c["probability"]
        tid  = c.get("token_id", "")
        if not tid: continue

        if name == RENAN_NAME:
            # Recorde historico geral — exige 0.5pp de gap desde ultimo post de recorde hoje
            history_all = fetch_price_history(tid)
            if history_all:
                max_ever    = max(h["price"] for h in history_all[:-1]) if len(history_all) > 1 else 0
                prev_record = records.get("renan_alltime", 0)
                last_today  = records.get("renan_alltime_last_post_today", {})
                last_prob   = last_today.get("prob", 0) if last_today.get("date") == today_str else 0
                gap_ok      = round(prob - last_prob, 1) >= 0.5
                # prob > prev_record garante que é genuinamente novo
                # prob != prev_record evita repost após reinicialização
                if prob > max_ever and prob > prev_record and gap_ok and round(prob - prev_record, 1) >= 0.5:
                    events.append({"type": "renan_alltime", "candidate": name, "prob": prob, "prev": max_ever})
                    records["renan_alltime"] = prob
                    records["renan_alltime_last_post_today"] = {"date": today_str, "prob": prob}

            # Recorde semanal — maximo 1 por dia
            since_7d   = datetime.now(BRAZIL_TZ) - timedelta(days=7)
            history_7d = fetch_price_history(tid, since=since_7d)
            if history_7d:
                max_week      = max(h["price"] for h in history_7d[:-1]) if len(history_7d) > 1 else 0
                prev_weekly   = records.get("renan_weekly", 0)
                already_today = records.get("renan_weekly_last_date", "") == today_str
                # prev_weekly já foi postado — só posta se superou em pelo menos 0.5pp
                if prob > max_week and round(prob - prev_weekly, 1) >= 0.5 and not already_today:
                    events.append({"type": "renan_weekly", "candidate": name, "prob": prob, "prev": max_week})
                    records["renan_weekly"] = prob
                    records["renan_weekly_last_date"] = today_str

        if name == LULA_NAME:
            history_march = fetch_price_history(tid, since=MARCH_CUTOFF)
            if history_march:
                min_march = min(h["price"] for h in history_march[:-1]) if len(history_march) > 1 else 999
                prev_low  = records.get("lula_low", 999)
                if prob < min_march and prob < prev_low:
                    events.append({"type": "lula_low", "candidate": name, "prob": prob, "prev": min_march})
                    records["lula_low"] = prob

        if name == FLAVIO_NAME:
            history_march = fetch_price_history(tid, since=MARCH_CUTOFF)
            if history_march:
                min_march = min(h["price"] for h in history_march[:-1]) if len(history_march) > 1 else 999
                prev_low  = records.get("flavio_low", 999)
                if prob < min_march and prob < prev_low:
                    events.append({"type": "flavio_low", "candidate": name, "prob": prob, "prev": min_march})
                    records["flavio_low"] = prob

    save_records(records)
    return events


def build_chart_lines(top5):
    fig, ax = plt.subplots(figsize=(10, 10))
    fig.patch.set_facecolor("#0D1117"); ax.set_facecolor("#0D1117")
    has_data = False; legend_items = []

    for idx, c in enumerate(top5):
        tid = c.get("token_id", "")
        if not tid: continue
        history = fetch_price_history(tid)
        if not history: continue
        has_data = True
        dates  = [h["date"] for h in history]
        prices = smooth_series([h["price"] for h in history])
        color  = COLORS[idx % len(COLORS)]
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

    x_cursor = 0.01; y_leg = 0.94
    for color, name, prob in legend_items:
        fig.text(x_cursor, y_leg, "●", color=color, fontsize=10, transform=fig.transFigure, va="center")
        label = f" {name}  {prob:.1f}%"
        fig.text(x_cursor+0.018, y_leg, label, color="#CCCCCC", fontsize=8, transform=fig.transFigure, va="center")
        x_cursor += 0.02 + len(label)*0.0055
        if x_cursor > 0.95: x_cursor = 0.01; y_leg -= 0.06

    fig.text(0.99, 0.01,
             f"Gerado em {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')} • Fonte: Polymarket",
             ha="right", va="bottom", color="#333", fontsize=7, transform=fig.transFigure)

    path = CHARTS_DIR / f"chart_lines_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); return path


def build_chart_bars(top5, title_suffix=""):
    """Gráfico de barras horizontais — usado no resumo do dia."""
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor("#0D1117"); ax.set_facecolor("#0D1117")

    names  = [f"{MEDAL[i]} {short_name(c['candidate'])}" for i, c in enumerate(top5)]
    probs  = [c["probability"] for c in top5]
    colors = COLORS[:len(top5)]

    bars = ax.barh(names[::-1], probs[::-1], color=colors[::-1], height=0.55, edgecolor="none")
    for bar, prob in zip(bars, probs[::-1]):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
                f"{prob:.1f}%", va="center", ha="left", color=bar.get_facecolor(), fontsize=11, fontweight="bold")

    ax.set_xlim(0, max(probs) * 1.25)
    ax.tick_params(axis="y", colors="#CCCCCC", labelsize=10, length=0)
    ax.tick_params(axis="x", colors="#555", labelsize=8, length=0)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.0f}%"))
    ax.grid(axis="x", color="#1E1E2E", linewidth=0.8); ax.grid(axis="y", visible=False)
    ax.set_axisbelow(True)
    for spine in ax.spines.values(): spine.set_visible(False)

    title = f"Eleição Presidencial Brasil 2026{title_suffix}"
    ax.set_title(title, color="white", fontsize=12, fontweight="bold", pad=14)
    fig.text(0.99, 0.01,
             f"Gerado em {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')} • Fonte: Polymarket",
             ha="right", va="bottom", color="#333", fontsize=7, transform=fig.transFigure)

    plt.tight_layout()
    path = CHARTS_DIR / f"chart_bars_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); return path


def build_chart_single(candidate):
    """Gráfico individual escuro com escala dramática."""
    tid = candidate.get("token_id", "")
    if not tid: return None
    history = fetch_price_history(tid)
    if not history: return None

    dates  = [h["date"] for h in history]
    prices = smooth_series([h["price"] for h in history])
    name   = candidate["candidate"]
    prob   = candidate["probability"]
    color  = "#E6A817" if name == RENAN_NAME else ("#4C9BE8" if name == FLAVIO_NAME else "#5B9BD5")

    p_min = min(prices); p_max = max(prices)
    margin = max((p_max - p_min) * 0.15, 1.5)
    y_min = max(0, p_min - margin); y_max = p_max + margin
    delta_total = round(prices[-1] - prices[0], 1)
    delta_str = f"▲{delta_total:.1f}%" if delta_total >= 0 else f"▼{abs(delta_total):.1f}%"

    fig, ax = plt.subplots(figsize=(10, 10))
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

    fig.text(0.04, 0.93, name, color="white", fontsize=13, fontweight="bold", transform=fig.transFigure)
    fig.text(0.04, 0.86, f"{prob:.0f}% chance", color=color, fontsize=12, fontweight="bold", transform=fig.transFigure)
    fig.text(0.22, 0.86, delta_str, color=color, fontsize=11, transform=fig.transFigure)
    fig.text(0.99, 0.01,
             f"Gerado em {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')} • Fonte: Polymarket",
             ha="right", va="bottom", color="#333", fontsize=7, transform=fig.transFigure)

    plt.subplots_adjust(right=0.88, top=0.80, left=0.04, bottom=0.08)
    path = CHARTS_DIR / f"chart_single_{name.split()[0].lower()}_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); return path


# ─────────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────────

def load_json(path):
    try:
        return json.load(open(path, encoding="utf-8")) if path.exists() else None
    except: return None

def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def load_last_post():       return load_json(LAST_POST_FILE)
def save_last_post(d):      save_json(LAST_POST_FILE, d)
def load_first_post_day():  return load_json(FIRST_POST_DAY_FILE)
def save_first_post_day(d): save_json(FIRST_POST_DAY_FILE, d)
def load_known_candidates():
    d = load_json(KNOWN_CANDIDATES_FILE); return set(d) if d else set()
def save_known_candidates(s): save_json(KNOWN_CANDIDATES_FILE, list(s))
def load_last_ranking():    return load_json(LAST_RANKING_FILE)
def save_last_ranking(r):   save_json(LAST_RANKING_FILE, r)
def load_weekly():          return load_json(WEEKLY_FILE)
def save_weekly(d):         save_json(WEEKLY_FILE, d)
def load_pending():         return load_json(PENDING_FILE) or []
def save_pending(d):        save_json(PENDING_FILE, d)

def _update_last_post(candidates, tweet_id):
    save_last_post({
        "tweet_id": tweet_id,
        "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
        "candidates": candidates,
    })


# ─────────────────────────────────────────────
# LÓGICA DE VARIAÇÃO — evita "0.0pp"
# ─────────────────────────────────────────────

def best_delta(delta_last, delta_day):
    """
    Retorna o melhor número para usar no texto:
    - Se delta_day entre -1 e +1: usa delta_last
    - Caso contrário: usa o de maior valor absoluto
    """
    if delta_day is None: return delta_last
    if -1 < delta_day < 1: return delta_last
    return delta_day if abs(delta_day) >= abs(delta_last) else delta_last


# ─────────────────────────────────────────────
# TEXTOS
# ─────────────────────────────────────────────

OPENING_PHRASES = [
    "🌀 Guinada no cenário eleitoral brasileiro!",
    "⚡ O tabuleiro das eleições acabou de mudar!",
    "🔄 Movimento inesperado nas apostas eleitorais!",
    "🎯 Atenção: o jogo eleitoral está se movendo!",
    "📣 Mudança de ventos no cenário das eleições 2026!",
    "🏁 Nova virada nas apostas da corrida presidencial!",
    "📡 Sinal de alerta no Polymarket!",
    "👀 Algo está acontecendo nas apostas eleitorais!",
]

def pick_phrase(): return random.choice(OPENING_PHRASES)

def alert_header(name, delta):
    abs_d = abs(delta); subindo = delta > 0; short = name.split()[0]
    if name == RENAN_NAME:
        if abs_d >= 4: return random.choice([
            f"🚀 RENAN DISPARA! Candidato surpreende e sobe {abs_d:.1f}pp hoje!",
            f"🚀 RENAN EXPLODE! +{abs_d:.1f}pp em um único dia nas apostas!",
            f"🚀 IMPRESSIONANTE! Renan Santos acumula +{abs_d:.1f}pp hoje!",
        ])
        elif abs_d >= 2: return random.choice([
            f"🔥 Renan Santos em ALTA — sobe {abs_d:.1f}pp no dia!",
            f"🔥 Renan disparando — já acumula +{abs_d:.1f}pp hoje!",
            f"🔥 Que dia pro Renan! +{abs_d:.1f}pp acumulados desde hoje cedo",
        ])
        else: return random.choice([
            f"⭐ ALERTA — Renan Santos avança nas apostas! +{abs_d:.1f}pp",
            f"⭐ ALERTA — Renan em movimento — subiu {abs_d:.1f}pp no dia",
            f"⭐ ALERTA — Renan Santos ganhando terreno — +{abs_d:.1f}pp",
        ])
    else:
        if subindo:
            if abs_d >= 4: return random.choice([
                f"🚀 {name} acumula +{abs_d:.1f}pp hoje nas apostas",
                f"📈 Grande movimento — {short} sobe {abs_d:.1f}pp no dia",
                f"📊 {name} em forte alta — +{abs_d:.1f}pp acumulados hoje",
            ])
            elif abs_d >= 2: return random.choice([
                f"🚨 URGENTE — 📈 {short} em alta — +{abs_d:.1f}pp no dia",
                f"🚨 URGENTE — 📈 {name} ganhando força — +{abs_d:.1f}pp",
                f"🚨 URGENTE — 📈 {short} acumula +{abs_d:.1f}pp no dia",
            ])
            else: return random.choice([
                f"📊 ATUALIZAÇÃO — {short} subiu {abs_d:.1f}pp no dia",
                f"📊 ATUALIZAÇÃO — {name} avança {abs_d:.1f}pp",
                f"📊 ATUALIZAÇÃO — {short} em leve alta — +{abs_d:.1f}pp",
            ])
        else:
            if abs_d >= 4: return random.choice([
                f"📉 DERRETEU! {name} despenca {abs_d:.1f}pp hoje",
                f"📉 QUE TOMBO! {short} perde {abs_d:.1f}pp em um dia",
                f"📉 {name} acumula -{abs_d:.1f}pp hoje nas apostas",
            ])
            elif abs_d >= 2: return random.choice([
                f"🚨 URGENTE — 📉 {short} em baixa — recua {abs_d:.1f}pp",
                f"🚨 URGENTE — 📉 {name} perdendo força — -{abs_d:.1f}pp",
                f"🚨 URGENTE — 📉 {short} em queda — -{abs_d:.1f}pp",
            ])
            else: return random.choice([
                f"📊 ATUALIZAÇÃO — {short} caiu {abs_d:.1f}pp no dia",
                f"📊 ATUALIZAÇÃO — {name} recua {abs_d:.1f}pp",
                f"📊 ATUALIZAÇÃO — {short} em leve baixa — -{abs_d:.1f}pp",
            ])


def format_var(delta):
    if delta is None: return "   —  "
    if delta > 0:     return f"▲ +{delta:.1f}pp"
    if delta < 0:     return f"▼ {delta:.1f}pp"
    return "↔  0.0pp"


def candidates_block(candidates, last_map, first_map, highlight_name=None):
    lines = []
    for i, c in enumerate(candidates[:5]):
        name  = c["candidate"]
        prob  = c["probability"]
        d_last = round(prob - last_map[name], 1) if name in last_map else None
        d_day  = round(prob - first_map[name], 1) if name in first_map else None
        use_d  = best_delta(d_last, d_day) if d_last is not None else None
        day_str = f" | dia {'+' if (d_day or 0)>0 else ''}{d_day:.1f}pp" if d_day is not None else ""
        lines.append(f"{MEDAL[i]} {short_name(name):<12} | {prob:4.1f}% | {format_var(use_d)}{day_str}")
    return "\n".join(lines)


def renan_flavio_line(candidates):
    r = next((c for c in candidates if c["candidate"] == RENAN_NAME), None)
    f = next((c for c in candidates if c["candidate"] == FLAVIO_NAME), None)
    if r and f:
        diff = round(f["probability"] - r["probability"], 1)
        return f"📌 Renan se aproxima de Flávio: diferença {diff}pp"
    return None


def build_scheduled_tweet(candidates, last_post, label):
    now_str  = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    last_map = {c["candidate"]: c["probability"] for c in (last_post or {}).get("candidates", [])}
    lines = [
        "🗳 Eleição Presidencial Brasil 2026",
        f"📊 Polymarket — {now_str}", "",
    ]
    for i, c in enumerate(candidates[:5]):
        delta = round(c["probability"] - last_map[c["candidate"]], 1) if c["candidate"] in last_map else None
        lines.append(f"{MEDAL[i]} {short_name(c['candidate']):<12} | {c['probability']:4.1f}% | {format_var(delta)}")
    trend_parts = []
    for c in candidates[:3]:
        tid = c.get("token_id", "")
        if tid: trend_parts.append(f"{c['candidate'].split()[0]} {get_trend_3days(tid)}")
    if trend_parts: lines += ["", "📈 Tendência 3 dias: " + " | ".join(trend_parts)]
    lines += ["", f"🕐 {label}", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_alert_tweet(candidates, first_day, last_post, trigger_name, delta_last, delta_day):
    now_str   = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    last_map  = {c["candidate"]: c["probability"] for c in (last_post or {}).get("candidates", [])}
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}
    use_delta = best_delta(delta_last, delta_day)
    opening   = pick_phrase()
    header    = alert_header(trigger_name, use_delta)

    lines = [opening, "", header, "",
             "🗳 Eleição Presidencial Brasil 2026",
             f"📊 Polymarket — {now_str}", ""]

    for i, c in enumerate(candidates[:5]):
        name  = c["candidate"]
        prob  = c["probability"]
        d_l   = round(prob - last_map[name], 1) if name in last_map else None
        d_d   = round(prob - first_map[name], 1) if name in first_map else None
        use_d = best_delta(d_l, d_d) if d_l is not None else None
        day_str = f" | dia {'+' if (d_d or 0)>0 else ''}{d_d:.1f}pp" if d_d is not None else ""
        lines.append(f"{MEDAL[i]} {short_name(name):<12} | {prob:4.1f}% | {format_var(use_d)}{day_str}")

    add_renan = (trigger_name == RENAN_NAME and delta_last > 0) or \
                (trigger_name == FLAVIO_NAME and delta_last < 0)
    if add_renan:
        extra = renan_flavio_line(candidates)
        if extra: lines += ["", extra]
    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_record_tweet(event, candidates, first_day):
    name    = event["candidate"]
    prob    = event["prob"]
    prev    = event["prev"]
    etype   = event["type"]
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}
    delta_day = round(prob - first_map.get(name, prob), 1)

    c_obj = next((c for c in candidates if c["candidate"] == name), None)
    idx   = next((i for i, c in enumerate(candidates[:5]) if c["candidate"] == name), 0)

    if etype == "renan_alltime":
        lines = [
            pick_phrase(),
            "",
            "🏆 RECORDE HISTÓRICO — Renan Santos!",
            "",
            f"Renan Santos atinge sua maior probabilidade",
            f"de todos os tempos no Polymarket!",
            "",
            f"{MEDAL[idx]} {short_name(name)}: {prob:.1f}% ▲ +{delta_day:.1f}pp no dia",
            "",
            f"📈 Nunca esteve tão alto nas apostas!",
            f"Anterior: {prev:.1f}%",
            "",
            "#Eleicoes2026 #Brasil #Polymarket #RecordeRenan",
        ]
    elif etype == "renan_weekly":
        lines = [
            pick_phrase(),
            "",
            "⭐ RECORDE SEMANAL — Renan Santos!",
            "",
            f"Renan Santos atinge sua maior probabilidade",
            f"dos últimos 7 dias no Polymarket!",
            "",
            f"{MEDAL[idx]} {short_name(name)}: {prob:.1f}% ▲ +{delta_day:.1f}pp no dia",
            "",
            f"📈 Máxima dos últimos 7 dias!",
            f"Anterior (semana): {prev:.1f}%",
            "",
            "#Eleicoes2026 #Brasil #Polymarket #RenanEmAlta",
        ]
    elif etype == "lula_low":
        lines = [
            pick_phrase(),
            "",
            "📉 MÍNIMA HISTÓRICA desde 1º de março!",
            "",
            f"Lula atinge sua menor probabilidade",
            f"desde março no Polymarket.",
            "",
            f"{MEDAL[idx]} {short_name(name)}: {prob:.1f}% ▼ {delta_day:.1f}pp no dia",
            "",
            f"📊 Dado válido a partir de 01/03/2026",
            f"Anterior (desde março): {prev:.1f}%",
            "",
            "#Eleicoes2026 #Brasil #Polymarket",
        ]
    elif etype == "flavio_low":
        lines = [
            pick_phrase(),
            "",
            "📉 MÍNIMA HISTÓRICA desde 1º de março!",
            "",
            f"Flávio Bolsonaro atinge sua menor probabilidade",
            f"desde março no Polymarket.",
            "",
            f"{MEDAL[idx]} {short_name(name)}: {prob:.1f}% ▼ {delta_day:.1f}pp no dia",
            "",
            f"📊 Dado válido a partir de 01/03/2026",
            f"Anterior (desde março): {prev:.1f}%",
            "",
            "#Eleicoes2026 #Brasil #Polymarket",
        ]
    else:
        return None

    return "\n".join(lines)


def build_daily_summary(candidates, first_day):
    now_str   = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}
    best_name = None; best_delta_val = 0

    lines = [f"🌙 Resumo do dia — {now_str}", "",
             "Como os candidatos fecharam o dia", "nas apostas do Polymarket:", ""]
    for i, c in enumerate(candidates[:5]):
        delta = round(c["probability"] - first_map[c["candidate"]], 1) if c["candidate"] in first_map else None
        lines.append(f"{MEDAL[i]} {short_name(c['candidate']):<12} | {c['probability']:4.1f}% | {format_var(delta)} no dia")
        if delta and abs(delta) > abs(best_delta_val): best_delta_val = delta; best_name = c["candidate"]
    if best_name:
        dir_str = "avançou" if best_delta_val > 0 else "recuou"
        lines += ["", f"📌 Destaque do dia: {best_name}",
                  f"foi quem mais {dir_str} — {format_var(best_delta_val)} desde a manhã"]
    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_weekly_summary(candidates, weekly):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    w_map   = {c["candidate"]: c["probability"] for c in (weekly or {}).get("candidates", [])}
    best_name = None; best_delta_val = 0
    prev_lula = None; prev_flavio = None

    lines = [f"📅 Resumo da semana — {now_str}", "",
             "Como os candidatos variaram", "nos últimos 7 dias no Polymarket:", ""]
    for i, c in enumerate(candidates[:5]):
        delta = round(c["probability"] - w_map[c["candidate"]], 1) if c["candidate"] in w_map else None
        lines.append(f"{MEDAL[i]} {short_name(c['candidate']):<12} | {c['probability']:4.1f}% | {format_var(delta)} na semana")
        if delta and abs(delta) > abs(best_delta_val): best_delta_val = delta; best_name = c["candidate"]
        if c["candidate"] == LULA_NAME:   prev_lula   = w_map.get(LULA_NAME)
        if c["candidate"] == FLAVIO_NAME: prev_flavio = w_map.get(FLAVIO_NAME)
    if best_name:
        dir_str = "maior avanço" if best_delta_val > 0 else "maior queda"
        lines += ["", f"📌 Destaque: {best_name}", f"{dir_str} — {format_var(best_delta_val)} em 7 dias"]
    if prev_lula and prev_flavio:
        lines += ["", "Comparando com 7 dias atrás:",
                  f"Lula era líder com {prev_lula:.1f}% | Flávio em {prev_flavio:.1f}%"]
    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_overtake_tweet_1(overtaker, overtaken, candidates):
    o1 = next((c for c in candidates if c["candidate"] == overtaker), None)
    o2 = next((c for c in candidates if c["candidate"] == overtaken), None)
    if not o1 or not o2: return None
    i1 = next((i for i,c in enumerate(candidates) if c["candidate"] == overtaker), 0)
    i2 = next((i for i,c in enumerate(candidates) if c["candidate"] == overtaken), 1)
    historic = (overtaker == RENAN_NAME and overtaken == FLAVIO_NAME) or \
               (overtaker == FLAVIO_NAME and overtaken == LULA_NAME)
    header = "🚨 VIRADA HISTÓRICA NAS APOSTAS!" if historic else "📊 MUDANÇA NO RANKING!"
    footer = "O favoritismo mudou de mão\nnas apostas internacionais." if historic else ""
    lines = [header, "", f"{overtaker} acaba de ultrapassar", f"{overtaken} no Polymarket!", "",
             f"{MEDAL[i1]} {overtaker}: {o1['probability']:.1f}% ▲",
             f"{MEDAL[i2]} {overtaken}: {o2['probability']:.1f}% ▼"]
    if footer: lines += ["", footer]
    lines += ["", "#Eleicoes2026 #Brasil #Polymarket #Virada"]
    return "\n".join(lines)


def build_overtake_tweet_2(overtaker, overtaken, candidates):
    o1 = next((c for c in candidates if c["candidate"] == overtaker), None)
    o2 = next((c for c in candidates if c["candidate"] == overtaken), None)
    if not o1 or not o2: return None
    first_day = load_first_post_day()
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}
    d1 = round(o1["probability"] - first_map.get(overtaker, o1["probability"]), 1)
    d2 = round(o2["probability"] - first_map.get(overtaken, o2["probability"]), 1)
    i1 = next((i for i,c in enumerate(candidates) if c["candidate"] == overtaker), 0)
    i2 = next((i for i,c in enumerate(candidates) if c["candidate"] == overtaken), 1)
    lines = [f"📉 {overtaken} fica para trás", "",
             f"Após a virada de hoje, {overtaken}",
             f"encerra abaixo de {overtaker} no Polymarket.", "",
             f"{MEDAL[i2]} {overtaken}: {o2['probability']:.1f}% {format_var(d2)} no dia",
             f"{MEDAL[i1]} {overtaker}: {o1['probability']:.1f}% {format_var(d1)} no dia",
             "", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_new_candidate_tweet(name):
    return "\n".join(["🚨 ÚLTIMA HORA — Novo candidato nas apostas!", "",
        f"{name} acaba de aparecer nas probabilidades",
        "do Polymarket para a Eleição Presidencial 2026.", "",
        "🔔 Fique de olho nas próximas atualizações.", "",
        f"#Eleicoes2026 #Brasil #Polymarket #{name.replace(' ','')}"])


def build_removed_candidate_tweet(name):
    return "\n".join(["📢 ÚLTIMA HORA — Candidato fora das apostas", "",
        f"{name} foi removido das probabilidades",
        "do Polymarket para a Eleição Presidencial 2026.", "",
        "#Eleicoes2026 #Brasil #Polymarket"])


# ─────────────────────────────────────────────
# TWITTER
# ─────────────────────────────────────────────

def get_client():
    return tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY, consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN, access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )

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
                except Exception as e:
                    log.warning("Erro mídia: %s", e)
            kwargs = {"text": text}
            if media_id: kwargs["media_ids"] = [media_id]
            resp = get_client().create_tweet(**kwargs)
            tid  = resp.data["id"]
            log.info("Tweet postado! ID: %s", tid)
            return tid
        except Exception as e:
            log.warning("Tentativa %d/%d falhou: %s", attempt, retries, e)
            if attempt < retries: time.sleep(retry_delay)
    log.error("Todas as tentativas falharam.")
    return None


# ─────────────────────────────────────────────
# PENDENTES
# ─────────────────────────────────────────────

def add_pending(atype, data, delay_minutes):
    pending = load_pending()
    execute_at = (datetime.now(BRAZIL_TZ) + timedelta(minutes=delay_minutes)).isoformat()
    pending.append({"type": atype, "data": data, "execute_at": execute_at})
    save_pending(pending)

def process_pending(candidates):
    pending = load_pending(); remaining = []
    now = datetime.now(BRAZIL_TZ)
    for action in pending:
        if now < datetime.fromisoformat(action["execute_at"]):
            remaining.append(action); continue
        atype = action["type"]; data = action["data"]
        log.info("Processando pendente: %s", atype)
        if atype == "overtake_followup":
            text = build_overtake_tweet_2(data["overtaker"], data["overtaken"], candidates)
            if text:
                chart = build_chart_single(next((c for c in candidates if c["candidate"] == data["overtaken"]), candidates[0]))
                tid = post_tweet(text, chart)
                if tid: _update_last_post(candidates, tid)
    save_pending(remaining)


# ─────────────────────────────────────────────
# POSTS PRINCIPAIS
# ─────────────────────────────────────────────

def run_scheduled_post(label):
    log.info("Post agendado: %s", label)
    candidates = fetch_polymarket_data()
    if not candidates: return

    last_post = load_last_post()
    today_str = datetime.now(BRAZIL_TZ).date().isoformat()
    first_day = load_first_post_day()
    if not first_day or first_day.get("date") != today_str:
        save_first_post_day({"date": today_str, "candidates": candidates})

    text  = build_scheduled_tweet(candidates, last_post, label)
    # Alterna entre linhas e barras
    _chart_toggle["use_bars"] = not _chart_toggle["use_bars"]
    chart = build_chart_bars(candidates[:5]) if _chart_toggle["use_bars"] else build_chart_lines(candidates[:5])
    tid   = post_tweet(text, chart)
    if tid:
        _update_last_post(candidates, tid)
        known = load_known_candidates()
        known.update(c["candidate"] for c in candidates)
        save_known_candidates(known)
        save_last_ranking([c["candidate"] for c in candidates[:5]])
        # Salva horário do último post fixo para lógica de skip
        save_json(DATA_DIR / "last_fixed_post.json", {"timestamp": datetime.now(BRAZIL_TZ).isoformat()})


def run_daily_summary():
    candidates = fetch_polymarket_data()
    if not candidates: return
    text  = build_daily_summary(candidates, load_first_post_day())
    chart = build_chart_bars(candidates[:5], " — Resumo do dia")
    tid   = post_tweet(text, chart)
    if tid: _update_last_post(candidates, tid)


def run_weekly_summary():
    candidates = fetch_polymarket_data()
    if not candidates: return
    weekly = load_weekly()
    text   = build_weekly_summary(candidates, weekly)
    chart  = build_chart_lines(candidates[:5])
    tid    = post_tweet(text, chart)
    if tid:
        save_weekly({"date": datetime.now(BRAZIL_TZ).date().isoformat(), "candidates": candidates})
        _update_last_post(candidates, tid)


def should_skip_fixed_post():
    """Retorna True se houve alerta/post extra nos últimos 30 min."""
    last_fixed = load_json(DATA_DIR / "last_alert_post.json")
    if not last_fixed: return False
    last_ts = datetime.fromisoformat(last_fixed["timestamp"])
    diff = (datetime.now(BRAZIL_TZ) - last_ts).total_seconds() / 60
    return diff < 30


def check_alert():
    candidates = fetch_polymarket_data()
    if not candidates: return

    process_pending(candidates)

    # Novos / removidos
    known = load_known_candidates()
    current_names = {c["candidate"] for c in candidates}
    if known:
        for name in current_names - known:
            post_tweet(build_new_candidate_tweet(name))
        for name in known - current_names:
            post_tweet(build_removed_candidate_tweet(name))
    save_known_candidates(current_names)

    # Recordes
    first_day = load_first_post_day()
    record_events = check_records(candidates)
    for event in record_events:
        log.info("Recorde detectado: %s", event["type"])
        text  = build_record_tweet(event, candidates, first_day)
        c_obj = next((c for c in candidates if c["candidate"] == event["candidate"]), None)
        chart = build_chart_single(c_obj) if c_obj else None
        tid   = post_tweet(text, chart)
        if tid:
            _update_last_post(candidates, tid)
            save_json(DATA_DIR / "last_alert_post.json", {"timestamp": datetime.now(BRAZIL_TZ).isoformat()})

    # Ultrapassagens
    last_ranking = load_last_ranking()
    new_ranking  = [c["candidate"] for c in candidates[:5]]
    if last_ranking and new_ranking != last_ranking:
        for pos, name in enumerate(new_ranking):
            if pos < len(last_ranking) and name != last_ranking[pos]:
                overtaken = last_ranking[pos]
                if overtaken in new_ranking and new_ranking.index(overtaken) > pos:
                    text1  = build_overtake_tweet_1(name, overtaken, candidates)
                    chart1 = build_chart_single(next((c for c in candidates if c["candidate"] == name), candidates[0]))
                    tid1   = post_tweet(text1, chart1)
                    if tid1:
                        _update_last_post(candidates, tid1)
                        save_json(DATA_DIR / "last_alert_post.json", {"timestamp": datetime.now(BRAZIL_TZ).isoformat()})
                    add_pending("overtake_followup", {"overtaker": name, "overtaken": overtaken}, 30)
                    break
    save_last_ranking(new_ranking)

    last_post = load_last_post()
    if not last_post: return

    last_map  = {c["candidate"]: c["probability"] for c in last_post.get("candidates", [])}
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}

    for c in candidates[:5]:
        name  = c["candidate"]
        prob  = c["probability"]
        prev  = last_map.get(name)
        if prev is None: continue
        delta_last = round(prob - prev, 1)
        if abs(delta_last) < ALERT_THRESHOLD: continue
        if name == RENAN_NAME and delta_last < 0:
            log.info("Queda do Renan ignorada."); continue

        delta_day = round(prob - first_map[name], 1) if name in first_map else delta_last
        log.info("Alerta: %s d_last=%.1f d_day=%.1f", name, delta_last, delta_day)

        text  = build_alert_tweet(candidates, first_day, last_post, name, delta_last, delta_day)
        chart = build_chart_single(c)
        tid   = post_tweet(text, chart)
        if tid:
            _update_last_post(candidates, tid)
            save_json(DATA_DIR / "last_alert_post.json", {"timestamp": datetime.now(BRAZIL_TZ).isoformat()})
        break


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

SCHEDULED_HOURS = [
    (8,  "scheduled", "🌅 Post agendado 08:00"),
    (11, "scheduled", "☀️ Post agendado 11:00"),
    (13, "scheduled", "🌤 Post agendado 13:00"),
    (18, "scheduled", "🌆 Post agendado 18:00"),
    (21, "summary",   "🌙 Resumo do dia"),
]

def next_time(hour):
    now = datetime.now(BRAZIL_TZ)
    t = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if t <= now: t += timedelta(days=1)
    return t


def run_scheduler():
    log.info("Bot iniciado.")
    schedule = {h: next_time(h) for h, _, _ in SCHEDULED_HOURS}

    if not load_weekly():
        cands = fetch_polymarket_data()
        if cands:
            save_weekly({"date": datetime.now(BRAZIL_TZ).date().isoformat(), "candidates": cands})
            log.info("Baseline semanal criado.")

    for h, _, label in SCHEDULED_HOURS:
        log.info("Próximo %s: %s", label, schedule[h].strftime("%d/%m %H:%M"))

    while True:
        now = datetime.now(BRAZIL_TZ)
        for h, kind, label in SCHEDULED_HOURS:
            if now >= schedule[h]:
                if kind == "summary":
                    run_daily_summary()
                elif should_skip_fixed_post():
                    log.info("Post fixo %s pulado — alerta recente < 30min", label)
                else:
                    run_scheduled_post(label)
                schedule[h] = next_time(h)

        # Resumo semanal sexta às 21h
        if now.weekday() == 4 and now.hour == 21 and now.minute < 5:
            w = load_weekly()
            if not w or w.get("date") != now.date().isoformat():
                run_weekly_summary()

        check_alert()
        time.sleep(300)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_scheduled_post("🔧 Teste manual")
    else:
        run_scheduler()
