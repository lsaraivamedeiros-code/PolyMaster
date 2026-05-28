"""
Bot X - Eleições Brasil 2026
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

for d in [DATA_DIR, LOGS_DIR, CHARTS_DIR]:
    d.mkdir(exist_ok=True)

ALERT_THRESHOLD = 1.0
CLOB_BASE  = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

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

COLORS = ["#5B9BD5", "#1F77B4", "#E6A817", "#E07B39", "#7B68EE"]
MEDAL  = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]


# ─────────────────────────────────────────────
# POLYMARKET
# ─────────────────────────────────────────────

def fetch_polymarket_data():
    try:
        url = f"{GAMMA_BASE}/events?slug={POLYMARKET_MARKET_SLUG}"
        log.info("Buscando mercado: %s", url)
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        markets = data[0].get("markets", [])
        if not markets:
            return None
        candidates = []
        for market in markets:
            name = market.get("groupItemTitle") or market.get("question", "Desconhecido")
            outcome_prices = market.get("outcomePrices", "[]")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)
            clob_token_ids = market.get("clobTokenIds", "[]")
            if isinstance(clob_token_ids, str):
                clob_token_ids = json.loads(clob_token_ids)
            if outcome_prices and clob_token_ids:
                prob = round(float(outcome_prices[0]) * 100, 1)
                candidates.append({"candidate": name, "probability": prob, "token_id": clob_token_ids[0]})
        if not candidates:
            return None
        candidates.sort(key=lambda x: x["probability"], reverse=True)
        log.info("Top 5: %s", [(c["candidate"], c["probability"]) for c in candidates[:5]])
        return candidates
    except Exception as e:
        log.exception("Erro Polymarket: %s", e)
        return None


def fetch_price_history(token_id):
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
                result.append({"date": datetime.fromtimestamp(ts, tz=BRAZIL_TZ), "price": round(float(price)*100,1)})
        log.info("Histórico %s: %d pts", token_id[:10], len(result))
        return result
    except Exception as e:
        log.warning("Erro histórico %s: %s", token_id[:10], e)
        return []


def smooth_series(prices, window=3):
    if len(prices) < window:
        return prices
    arr = np.array(prices, dtype=float)
    s = np.convolve(arr, np.ones(window)/window, mode="same")
    s[:window] = arr[:window]; s[-window:] = arr[-window:]
    return s.tolist()


def get_trend_3days(token_id):
    history = fetch_price_history(token_id)
    if len(history) < 4:
        return "↔"
    recent = [h["price"] for h in history[-4:]]
    delta = recent[-1] - recent[0]
    return "▲" if delta > 0.3 else ("▼" if delta < -0.3 else "↔")


# ─────────────────────────────────────────────
# GRÁFICOS
# ─────────────────────────────────────────────

def build_chart_all(top5):
    """Gráfico com todos os candidatos — estilo Polymarket claro."""
    fig, ax = plt.subplots(figsize=(12, 5.2))
    fig.patch.set_facecolor("white"); ax.set_facecolor("white")
    has_data = False; legend_items = []

    for idx, c in enumerate(top5):
        tid = c.get("token_id", "")
        if not tid:
            continue
        history = fetch_price_history(tid)
        if not history:
            continue
        has_data = True
        dates  = [h["date"] for h in history]
        prices = smooth_series([h["price"] for h in history])
        color  = COLORS[idx % len(COLORS)]
        ax.plot(dates, prices, color=color, linewidth=1.8, alpha=0.92, solid_capstyle="round")
        ax.annotate(f"{prices[-1]:.0f}%", xy=(dates[-1], prices[-1]),
                    xytext=(5,0), textcoords="offset points",
                    color=color, fontsize=9, fontweight="bold", va="center", annotation_clip=False)
        legend_items.append((color, c["candidate"], c["probability"]))

    if not has_data:
        plt.close(); return None

    ax.yaxis.set_label_position("right"); ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.0f}%"))
    ax.tick_params(axis="y", colors="#888888", labelsize=9, length=0, pad=6)
    ax.tick_params(axis="x", colors="#888888", labelsize=9, length=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(axis="y", color="#EEEEEE", linewidth=0.8, linestyle="-")
    ax.grid(axis="x", visible=False); ax.set_axisbelow(True)
    for spine in ax.spines.values(): spine.set_visible(False)
    plt.subplots_adjust(right=0.87, top=0.82, left=0.04, bottom=0.10)

    x_cursor = 0.01; y_leg = 0.94
    for color, name, prob in legend_items:
        fig.text(x_cursor, y_leg, "●", color=color, fontsize=10, transform=fig.transFigure, va="center")
        label = f" {name}  {prob:.1f}%"
        fig.text(x_cursor+0.018, y_leg, label, color="#333333", fontsize=8.2, transform=fig.transFigure, va="center")
        x_cursor += 0.02 + len(label)*0.0055
        if x_cursor > 0.95: x_cursor = 0.01; y_leg -= 0.06

    fig.text(0.99, 0.01,
             f"Gerado em {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')} • Fonte: Polymarket",
             ha="right", va="bottom", color="#AAAAAA", fontsize=7, transform=fig.transFigure)

    path = CHARTS_DIR / f"chart_all_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(); log.info("Gráfico todos: %s", path)
    return path


def build_chart_single(candidate):
    """
    Gráfico individual de um candidato — estilo Polymarket escuro.
    Escala Y ajustada ao range do candidato para maximizar visual do movimento.
    """
    tid = candidate.get("token_id", "")
    if not tid:
        return None
    history = fetch_price_history(tid)
    if not history:
        return None

    dates  = [h["date"] for h in history]
    prices = smooth_series([h["price"] for h in history])
    name   = candidate["candidate"]
    prob   = candidate["probability"]
    color  = "#5B9BD5"

    # Escala dramática — range mínimo de 3pp para sempre parecer movimento grande
    p_min = min(prices)
    p_max = max(prices)
    margin = max((p_max - p_min) * 0.15, 1.5)
    y_min = max(0, p_min - margin)
    y_max = p_max + margin

    # Variação total
    delta_total = round(prices[-1] - prices[0], 1)
    delta_str = f"▲{delta_total:.1f}%" if delta_total >= 0 else f"▼{abs(delta_total):.1f}%"

    fig, ax = plt.subplots(figsize=(11, 4.5))
    fig.patch.set_facecolor("#0D1117"); ax.set_facecolor("#0D1117")

    ax.plot(dates, prices, color=color, linewidth=2.2, solid_capstyle="round")
    ax.fill_between(dates, prices, y_min, color=color, alpha=0.08)
    ax.scatter([dates[-1]], [prices[-1]], color=color, s=50, zorder=5)

    ax.set_ylim(y_min, y_max)
    ax.yaxis.set_label_position("right"); ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.0f}%"))
    ax.tick_params(axis="y", colors="#666666", labelsize=9, length=0, pad=6)
    ax.tick_params(axis="x", colors="#666666", labelsize=9, length=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(axis="y", color="#1A1A2E", linewidth=0.8, linestyle="-")
    ax.grid(axis="x", visible=False); ax.set_axisbelow(True)
    for spine in ax.spines.values(): spine.set_visible(False)

    # Cabeçalho estilo Polymarket
    fig.text(0.04, 0.92, name, color="white", fontsize=13, fontweight="bold", transform=fig.transFigure)
    fig.text(0.04, 0.82, f"{prob:.0f}% chance", color=color, fontsize=12, fontweight="bold", transform=fig.transFigure)
    fig.text(0.18, 0.82, delta_str, color=color, fontsize=11, transform=fig.transFigure)
    fig.text(0.99, 0.01,
             f"Gerado em {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')} • Fonte: Polymarket",
             ha="right", va="bottom", color="#444444", fontsize=7, transform=fig.transFigure)

    plt.subplots_adjust(right=0.88, top=0.75, left=0.04, bottom=0.12)
    path = CHARTS_DIR / f"chart_single_{name.split()[0].lower()}_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=160, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); log.info("Gráfico individual %s: %s", name, path)
    return path


# ─────────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────────

def load_json(path): return json.load(open(path, encoding="utf-8")) if path.exists() else None
def save_json(path, data): json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

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

def alert_header(name, delta_day):
    abs_d = abs(delta_day); subindo = delta_day > 0
    short = name.split()[0]
    if name == RENAN_NAME:
        if abs_d >= 4:
            return random.choice([
                f"🚀 RENAN DISPARA! Candidato surpreende e sobe {abs_d:.1f}pp hoje!",
                f"🚀 RENAN EXPLODE! +{abs_d:.1f}pp em um único dia nas apostas!",
                f"🚀 IMPRESSIONANTE! Renan Santos acumula +{abs_d:.1f}pp hoje nas apostas internacionais!",
            ])
        elif abs_d >= 2:
            return random.choice([
                f"🔥 Renan Santos em ALTA — sobe {abs_d:.1f}pp no dia!",
                f"🔥 Renan disparando — já acumula +{abs_d:.1f}pp hoje!",
                f"🔥 Que dia pro Renan! +{abs_d:.1f}pp acumulados desde hoje cedo",
            ])
        else:
            return random.choice([
                f"⭐ ALERTA — Renan Santos avança nas apostas! +{abs_d:.1f}pp desde hoje cedo",
                f"⭐ ALERTA — Renan em movimento — subiu {abs_d:.1f}pp no dia até agora",
                f"⭐ ALERTA — Renan Santos ganhando terreno — +{abs_d:.1f}pp desde a manhã",
            ])
    else:
        if subindo:
            if abs_d >= 4:
                return random.choice([
                    f"🚀 {name} acumula +{abs_d:.1f}pp hoje nas apostas",
                    f"📈 Grande movimento — {short} sobe {abs_d:.1f}pp no dia",
                    f"📊 {name} em forte alta — +{abs_d:.1f}pp acumulados hoje",
                ])
            elif abs_d >= 2:
                return random.choice([
                    f"🚨 URGENTE — 📈 {short} em alta — +{abs_d:.1f}pp no acumulado do dia",
                    f"🚨 URGENTE — 📈 {name} ganhando força — +{abs_d:.1f}pp desde hoje cedo",
                    f"🚨 URGENTE — 📈 {short} acumula +{abs_d:.1f}pp no dia",
                ])
            else:
                return random.choice([
                    f"📊 ATUALIZAÇÃO — {short} subiu {abs_d:.1f}pp no acumulado do dia",
                    f"📊 ATUALIZAÇÃO — {name} avança {abs_d:.1f}pp desde a manhã",
                    f"📊 ATUALIZAÇÃO — {short} em leve alta hoje — +{abs_d:.1f}pp",
                ])
        else:
            if abs_d >= 4:
                return random.choice([
                    f"📉 DERRETEU! {name} despenca {abs_d:.1f}pp nas apostas hoje",
                    f"📉 QUE TOMBO! {short} perde {abs_d:.1f}pp em um único dia",
                    f"📉 {name} acumula -{abs_d:.1f}pp hoje nas apostas",
                ])
            elif abs_d >= 2:
                return random.choice([
                    f"🚨 URGENTE — 📉 {short} em baixa — recua {abs_d:.1f}pp no dia",
                    f"🚨 URGENTE — 📉 {name} perdendo força — -{abs_d:.1f}pp acumulados",
                    f"🚨 URGENTE — 📉 {short} em queda — -{abs_d:.1f}pp desde hoje cedo",
                ])
            else:
                return random.choice([
                    f"📊 ATUALIZAÇÃO — {short} caiu {abs_d:.1f}pp no acumulado do dia",
                    f"📊 ATUALIZAÇÃO — {name} recua {abs_d:.1f}pp desde a manhã",
                    f"📊 ATUALIZAÇÃO — {short} em leve baixa hoje — -{abs_d:.1f}pp",
                ])


def format_var(delta):
    if delta is None: return " —"
    if delta > 0:  return f" ▲ +{delta:.1f}pp"
    if delta < 0:  return f" ▼ {delta:.1f}pp"
    return " ↔ 0.0pp"


def candidates_line(c, last_map, first_map):
    name = c["candidate"]; prob = c["probability"]
    delta_last = round(prob - last_map[name], 1) if name in last_map else None
    delta_day  = round(prob - first_map[name], 1) if name in first_map else None
    day_str = f" | dia: {format_var(delta_day).strip()}" if delta_day is not None else ""
    return f"{prob:.1f}%{format_var(delta_last)}{day_str}"


def renan_flavio_line(candidates):
    r = next((c for c in candidates if c["candidate"] == RENAN_NAME), None)
    f = next((c for c in candidates if c["candidate"] == FLAVIO_NAME), None)
    if r and f:
        diff = round(f["probability"] - r["probability"], 1)
        return f"📌 Renan se aproxima de Flávio: diferença caiu para {diff}pp"
    return None


def build_scheduled_tweet(candidates, last_post, label):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    last_map = {c["candidate"]: c["probability"] for c in (last_post or {}).get("candidates", [])}
    lines = ["🗳 Eleição Presidencial Brasil 2026", f"📊 Polymarket — {now_str}", ""]
    for i, c in enumerate(candidates[:5]):
        delta = round(c["probability"] - last_map[c["candidate"]], 1) if c["candidate"] in last_map else None
        lines.append(f"{MEDAL[i]} {c['candidate']}: {c['probability']:.1f}%{format_var(delta)}")
    trend_parts = []
    for c in candidates[:3]:
        tid = c.get("token_id", "")
        if tid: trend_parts.append(f"{c['candidate'].split()[0]} {get_trend_3days(tid)}")
    if trend_parts: lines += ["", "📈 Tendência 3 dias: " + " | ".join(trend_parts)]
    lines += ["", f"🕐 {label}", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_alert_tweet(candidates, first_day, last_post, trigger_name, delta_day):
    now_str  = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    last_map = {c["candidate"]: c["probability"] for c in (last_post or {}).get("candidates", [])}
    first_map= {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}
    opening  = pick_phrase()
    header   = alert_header(trigger_name, delta_day)
    lines = [opening, "", header, "", "🗳 Eleição Presidencial Brasil 2026", f"📊 Polymarket — {now_str}", ""]
    for i, c in enumerate(candidates[:5]):
        lines.append(f"{MEDAL[i]} {c['candidate']}: {candidates_line(c, last_map, first_map)}")
    add_renan = (trigger_name == RENAN_NAME and delta_day > 0) or (trigger_name == FLAVIO_NAME and delta_day < 0)
    if add_renan:
        extra = renan_flavio_line(candidates)
        if extra: lines += ["", extra]
    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_renan_followup_tweet(candidates, first_day, last_post):
    now_str   = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    last_map  = {c["candidate"]: c["probability"] for c in (last_post or {}).get("candidates", [])}
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}
    renan     = next((c for c in candidates if c["candidate"] == RENAN_NAME), None)
    if not renan: return None
    delta_day = round(renan["probability"] - first_map.get(RENAN_NAME, renan["probability"]), 1)
    lines = [
        random.choice([
            "🔥 Renan Santos segue em alta!",
            "⭐ Renan Santos mantém o movimento de subida!",
            "🚀 Renan não para — movimento continua nas apostas!",
        ]),
        "",
        f"45 minutos após o alerta, Renan segue",
        f"avançando nas apostas do Polymarket.",
        "",
        "🗳 Eleição Presidencial Brasil 2026",
        f"📊 Polymarket — {now_str}",
        "",
    ]
    for i, c in enumerate(candidates[:5]):
        lines.append(f"{MEDAL[i]} {c['candidate']}: {candidates_line(c, last_map, first_map)}")
    extra = renan_flavio_line(candidates)
    if extra: lines += ["", extra]
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
    lines = [header, "",
             f"{overtaker} acaba de ultrapassar",
             f"{overtaken} no Polymarket!", "",
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
    delta_overtaken = round(o2["probability"] - first_map.get(overtaken, o2["probability"]), 1)
    delta_overtaker = round(o1["probability"] - first_map.get(overtaker, o1["probability"]), 1)
    lines = [
        f"📉 {overtaken} fica para trás",
        "",
        f"Após a virada de hoje, {overtaken}",
        f"encerra abaixo de {overtaker}",
        "nas apostas do Polymarket.",
        "",
        f"{'🥇🥈🥉4️⃣5️⃣'[['🥇','🥈','🥉','4️⃣','5️⃣'].index(MEDAL[next((i for i,c in enumerate(candidates) if c['candidate']==overtaken), 0)])]} {overtaken}: {o2['probability']:.1f}%{format_var(delta_overtaken)} no dia",
        f"{'🥇🥈🥉4️⃣5️⃣'[['🥇','🥈','🥉','4️⃣','5️⃣'].index(MEDAL[next((i for i,c in enumerate(candidates) if c['candidate']==overtaker), 1)])]} {overtaker}: {o1['probability']:.1f}%{format_var(delta_overtaker)} no dia",
        "",
        "#Eleicoes2026 #Brasil #Polymarket",
    ]
    return "\n".join(lines)


def build_new_candidate_tweet(name):
    return "\n".join([
        "🚨 ÚLTIMA HORA — Novo candidato nas apostas!",
        "",
        f"{name} acaba de aparecer nas probabilidades",
        "do Polymarket para a Eleição Presidencial 2026.",
        "",
        "🔔 Fique de olho nas próximas atualizações.",
        "",
        f"#Eleicoes2026 #Brasil #Polymarket #{name.replace(' ','')}",
    ])


def build_removed_candidate_tweet(name):
    return "\n".join([
        "📢 ÚLTIMA HORA — Candidato fora das apostas",
        "",
        f"{name} foi removido das probabilidades",
        "do Polymarket para a Eleição Presidencial 2026.",
        "",
        f"#Eleicoes2026 #Brasil #Polymarket",
    ])


def build_daily_summary(candidates, first_day):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}
    best_name = None; best_delta = 0
    lines = [f"🌙 Resumo do dia — {now_str}", "", "Como os candidatos fecharam o dia", "nas apostas do Polymarket:", ""]
    for i, c in enumerate(candidates[:5]):
        delta = round(c["probability"] - first_map[c["candidate"]], 1) if c["candidate"] in first_map else None
        lines.append(f"{MEDAL[i]} {c['candidate']}: {c['probability']:.1f}%{format_var(delta)} no dia")
        if delta and abs(delta) > abs(best_delta): best_delta = delta; best_name = c["candidate"]
    if best_name:
        direction = "avançou" if best_delta > 0 else "recuou"
        lines += ["", f"📌 Destaque do dia: {best_name}", f"foi quem mais {direction} — {format_var(best_delta).strip()} desde a manhã"]
    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_weekly_summary(candidates, weekly):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    w_map = {c["candidate"]: c["probability"] for c in (weekly or {}).get("candidates", [])}
    best_name = None; best_delta = 0; prev_lula = None; prev_flavio = None
    lines = [f"📅 Resumo da semana — {now_str}", "", "Como os candidatos variaram", "nos últimos 7 dias no Polymarket:", ""]
    for i, c in enumerate(candidates[:5]):
        delta = round(c["probability"] - w_map[c["candidate"]], 1) if c["candidate"] in w_map else None
        lines.append(f"{MEDAL[i]} {c['candidate']}: {c['probability']:.1f}%{format_var(delta)} na semana")
        if delta and abs(delta) > abs(best_delta): best_delta = delta; best_name = c["candidate"]
        if c["candidate"] == LULA_NAME:   prev_lula   = w_map.get(LULA_NAME)
        if c["candidate"] == FLAVIO_NAME: prev_flavio = w_map.get(FLAVIO_NAME)
    if best_name:
        dir_str = "maior avanço" if best_delta > 0 else "maior queda"
        lines += ["", f"📌 Destaque da semana: {best_name}", f"{dir_str} — {format_var(best_delta).strip()} em 7 dias"]
    if prev_lula and prev_flavio:
        lines += ["", "Comparando com 7 dias atrás:", f"Lula era líder com {prev_lula:.1f}% | Flávio em {prev_flavio:.1f}%"]
    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


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
    """Posta tweet com até `retries` tentativas espaçadas por `retry_delay` segundos."""
    for attempt in range(1, retries+1):
        try:
            media_id = None
            if chart_path and Path(chart_path).exists():
                try:
                    media = get_api_v1().media_upload(str(chart_path))
                    media_id = media.media_id
                    log.info("Mídia enviada: %s", media_id)
                except Exception as e:
                    log.warning("Erro mídia: %s", e)
            kwargs = {"text": text}
            if media_id: kwargs["media_ids"] = [media_id]
            resp = get_client().create_tweet(**kwargs)
            tweet_id = resp.data["id"]
            log.info("Tweet postado! ID: %s", tweet_id)
            return tweet_id
        except Exception as e:
            log.warning("Tentativa %d/%d falhou: %s", attempt, retries, e)
            if attempt < retries:
                log.info("Aguardando %ds para nova tentativa...", retry_delay)
                time.sleep(retry_delay)
    log.error("Todas as tentativas falharam.")
    return None


# ─────────────────────────────────────────────
# AÇÕES PENDENTES (posts agendados com delay)
# ─────────────────────────────────────────────

def add_pending(action_type, data, delay_minutes):
    """Agenda uma ação para daqui a `delay_minutes` minutos."""
    pending = load_pending()
    execute_at = (datetime.now(BRAZIL_TZ) + timedelta(minutes=delay_minutes)).isoformat()
    pending.append({"type": action_type, "data": data, "execute_at": execute_at})
    save_pending(pending)
    log.info("Ação pendente agendada: %s em %d min", action_type, delay_minutes)


def process_pending(candidates):
    """Processa ações pendentes cujo horário já chegou."""
    pending = load_pending()
    remaining = []
    now = datetime.now(BRAZIL_TZ)

    for action in pending:
        execute_at = datetime.fromisoformat(action["execute_at"])
        if now < execute_at:
            remaining.append(action)
            continue

        atype = action["type"]
        data  = action["data"]
        log.info("Processando ação pendente: %s", atype)

        if atype == "overtake_followup":
            text = build_overtake_tweet_2(data["overtaker"], data["overtaken"], candidates)
            if text:
                chart = build_chart_single(next((c for c in candidates if c["candidate"] == data["overtaken"]), candidates[0]))
                tid = post_tweet(text, chart)
                if tid: _update_last_post(candidates, tid)

        elif atype == "renan_followup":
            text = build_renan_followup_tweet(candidates, load_first_post_day(), load_last_post())
            if text:
                chart = build_chart_all(candidates[:5])
                tid = post_tweet(text, chart)
                if tid: _update_last_post(candidates, tid)

    save_pending(remaining)


def _update_last_post(candidates, tweet_id):
    save_last_post({
        "tweet_id": tweet_id,
        "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
        "candidates": candidates,
    })


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
    chart = build_chart_all(candidates[:5])
    tid   = post_tweet(text, chart)
    if tid:
        _update_last_post(candidates, tid)
        known = load_known_candidates()
        known.update(c["candidate"] for c in candidates)
        save_known_candidates(known)
        save_last_ranking([c["candidate"] for c in candidates[:5]])


def run_daily_summary():
    candidates = fetch_polymarket_data()
    if not candidates: return
    text  = build_daily_summary(candidates, load_first_post_day())
    chart = build_chart_all(candidates[:5])
    tid   = post_tweet(text, chart)
    if tid: _update_last_post(candidates, tid)


def run_weekly_summary():
    candidates = fetch_polymarket_data()
    if not candidates: return
    weekly = load_weekly()
    text   = build_weekly_summary(candidates, weekly)
    chart  = build_chart_all(candidates[:5])
    tid    = post_tweet(text, chart)
    if tid:
        save_weekly({"date": datetime.now(BRAZIL_TZ).date().isoformat(), "candidates": candidates})
        _update_last_post(candidates, tid)


def check_alert():
    candidates = fetch_polymarket_data()
    if not candidates: return

    # Processa pendentes
    process_pending(candidates)

    # Novos / removidos candidatos
    known = load_known_candidates()
    current_names = {c["candidate"] for c in candidates}
    if known:
        for name in current_names - known:
            log.info("Novo candidato: %s", name)
            post_tweet(build_new_candidate_tweet(name))
        for name in known - current_names:
            log.info("Candidato removido: %s", name)
            post_tweet(build_removed_candidate_tweet(name))
    save_known_candidates(current_names)

    # Ultrapassagens
    last_ranking = load_last_ranking()
    new_ranking  = [c["candidate"] for c in candidates[:5]]
    if last_ranking and new_ranking != last_ranking:
        for pos, name in enumerate(new_ranking):
            if pos < len(last_ranking) and name != last_ranking[pos]:
                overtaken = last_ranking[pos]
                if overtaken in new_ranking and new_ranking.index(overtaken) > pos:
                    log.info("Ultrapassagem: %s passou %s", name, overtaken)
                    # Post 1 imediato
                    text1 = build_overtake_tweet_1(name, overtaken, candidates)
                    chart1 = build_chart_single(next((c for c in candidates if c["candidate"] == name), candidates[0]))
                    tid1 = post_tweet(text1, chart1)
                    if tid1: _update_last_post(candidates, tid1)
                    # Post 2 agendado para 30 min depois
                    add_pending("overtake_followup", {"overtaker": name, "overtaken": overtaken}, delay_minutes=30)
                    break
    save_last_ranking(new_ranking)

    last_post = load_last_post()
    if not last_post: return

    first_day = load_first_post_day()
    last_map  = {c["candidate"]: c["probability"] for c in last_post.get("candidates", [])}
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}

    for c in candidates[:5]:
        name  = c["candidate"]
        prob  = c["probability"]
        prev  = last_map.get(name)
        if prev is None: continue

        delta_since_last = round(prob - prev, 1)
        if abs(delta_since_last) < ALERT_THRESHOLD: continue

        # Renan não posta em queda
        if name == RENAN_NAME and delta_since_last < 0:
            log.info("Queda do Renan ignorada.")
            continue

        delta_day = round(prob - first_map[name], 1) if name in first_map else delta_since_last

        log.info("Alerta: %s delta_last=%.1f delta_day=%.1f", name, delta_since_last, delta_day)
        text  = build_alert_tweet(candidates, first_day, last_post, name, delta_day)
        # Gráfico individual para alertas, exceto Renan que usa todos
        if name == RENAN_NAME:
            chart = build_chart_single(c)
        else:
            chart = build_chart_single(c)
        tid = post_tweet(text, chart)
        if tid:
            _update_last_post(candidates, tid)
            # Se for Renan subindo, agenda follow-up com gráfico completo em 45 min
            if name == RENAN_NAME and delta_since_last > 0:
                add_pending("renan_followup", {}, delay_minutes=45)
        break


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

SCHEDULED_HOURS = [
    (8,  "scheduled", "🌅 Post agendado 08:00"),
    (16, "scheduled", "🌆 Post agendado 16:00"),
    (19, "scheduled", "🌇 Post agendado 19:00"),
    (22, "summary",   "🌙 Resumo do dia"),
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
                if kind == "summary": run_daily_summary()
                else: run_scheduled_post(label)
                schedule[h] = next_time(h)

        # Resumo semanal sexta às 22h
        if now.weekday() == 4 and now.hour == 22 and now.minute < 5:
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
