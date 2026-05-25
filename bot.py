"""
Bot X - Eleições Brasil 2026
Posts automáticos às 8h, 16h, 19h e 22h + alertas inteligentes
"""

import os
import json
import time
import random
import logging
import requests
import tweepy
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

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
CHARTS_DIR.mkdir(exist_ok=True)

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
# POLYMARKET — dados atuais
# ─────────────────────────────────────────────

def fetch_polymarket_data():
    try:
        url = f"{GAMMA_BASE}/events?slug={POLYMARKET_MARKET_SLUG}"
        log.info("Buscando mercado: %s", url)
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            log.error("Nenhum evento encontrado.")
            return None
        markets = data[0].get("markets", [])
        if not markets:
            log.error("Evento sem markets.")
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
                candidates.append({
                    "candidate": name,
                    "probability": prob,
                    "token_id": clob_token_ids[0],
                })
        if not candidates:
            log.error("Nenhum candidato extraído.")
            return None
        candidates.sort(key=lambda x: x["probability"], reverse=True)
        log.info("Top 5: %s", [(c["candidate"], c["probability"]) for c in candidates[:5]])
        return candidates
    except Exception as e:
        log.exception("Erro ao buscar Polymarket: %s", e)
        return None


# ─────────────────────────────────────────────
# HISTÓRICO REAL (CLOB API)
# ─────────────────────────────────────────────

def fetch_price_history(token_id):
    try:
        resp = requests.get(
            f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval": "max", "fidelity": 1440},
            timeout=20,
        )
        resp.raise_for_status()
        history_raw = resp.json().get("history", [])
        result = []
        for pt in history_raw:
            ts = pt.get("t")
            price = pt.get("p")
            if ts and price is not None:
                dt = datetime.fromtimestamp(ts, tz=BRAZIL_TZ)
                result.append({"date": dt, "price": round(float(price) * 100, 1)})
        log.info("Histórico token %s...: %d pontos", token_id[:10], len(result))
        return result
    except Exception as e:
        log.warning("Erro histórico token %s: %s", token_id[:10], e)
        return []


def smooth_series(prices, window=3):
    if len(prices) < window:
        return prices
    arr = np.array(prices, dtype=float)
    kernel = np.ones(window) / window
    smoothed = np.convolve(arr, kernel, mode="same")
    smoothed[:window] = arr[:window]
    smoothed[-window:] = arr[-window:]
    return smoothed.tolist()


# ─────────────────────────────────────────────
# TENDÊNCIA 3 DIAS
# ─────────────────────────────────────────────

def get_trend_3days(token_id):
    """Retorna '▲', '▼' ou '↔' baseado nos últimos 3 dias do histórico."""
    history = fetch_price_history(token_id)
    if len(history) < 4:
        return "↔"
    recent = [h["price"] for h in history[-4:]]
    delta = recent[-1] - recent[0]
    if delta > 0.3:
        return "▲"
    elif delta < -0.3:
        return "▼"
    return "↔"


# ─────────────────────────────────────────────
# GRÁFICO estilo Polymarket
# ─────────────────────────────────────────────

def build_chart(top5):
    fig, ax = plt.subplots(figsize=(12, 5.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    has_data = False
    legend_items = []

    for idx, c in enumerate(top5):
        token_id = c.get("token_id", "")
        name     = c["candidate"]
        prob     = c["probability"]
        color    = COLORS[idx % len(COLORS)]
        if not token_id:
            continue
        history = fetch_price_history(token_id)
        if not history:
            continue
        has_data = True
        dates  = [h["date"] for h in history]
        prices = smooth_series([h["price"] for h in history], window=3)
        ax.plot(dates, prices, color=color, linewidth=1.8, alpha=0.92, solid_capstyle="round")
        ax.annotate(
            f"{prices[-1]:.0f}%",
            xy=(dates[-1], prices[-1]),
            xytext=(5, 0),
            textcoords="offset points",
            color=color, fontsize=9,
            fontweight="bold", va="center",
            annotation_clip=False,
        )
        legend_items.append((color, name, prob))

    if not has_data:
        plt.close()
        return None

    ax.yaxis.set_label_position("right")
    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.tick_params(axis="y", colors="#888888", labelsize=9, length=0, pad=6)
    ax.tick_params(axis="x", colors="#888888", labelsize=9, length=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(axis="y", color="#EEEEEE", linewidth=0.8, linestyle="-")
    ax.grid(axis="x", visible=False)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    plt.subplots_adjust(right=0.87, top=0.82, left=0.04, bottom=0.10)

    x_cursor = 0.01
    y_leg = 0.94
    for color, name, prob in legend_items:
        fig.text(x_cursor, y_leg, "●", color=color, fontsize=10,
                 transform=fig.transFigure, va="center")
        label = f" {name}  {prob:.1f}%"
        fig.text(x_cursor + 0.018, y_leg, label, color="#333333", fontsize=8.2,
                 transform=fig.transFigure, va="center")
        x_cursor += 0.02 + len(label) * 0.0055
        if x_cursor > 0.95:
            x_cursor = 0.01
            y_leg -= 0.06

    fig.text(
        0.99, 0.01,
        f"Gerado em {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')} • Fonte: Polymarket",
        ha="right", va="bottom", color="#AAAAAA", fontsize=7,
        transform=fig.transFigure,
    )

    chart_path = CHARTS_DIR / f"chart_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(chart_path, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close()
    log.info("Gráfico salvo: %s", chart_path)
    return chart_path


# ─────────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────────

def load_json(path):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_last_post():
    return load_json(LAST_POST_FILE)


def save_last_post(data):
    save_json(LAST_POST_FILE, data)


def load_first_post_day():
    return load_json(FIRST_POST_DAY_FILE)


def save_first_post_day(data):
    save_json(FIRST_POST_DAY_FILE, data)


def load_known_candidates():
    d = load_json(KNOWN_CANDIDATES_FILE)
    return set(d) if d else set()


def save_known_candidates(names):
    save_json(KNOWN_CANDIDATES_FILE, list(names))


def load_last_ranking():
    return load_json(LAST_RANKING_FILE)


def save_last_ranking(ranking):
    save_json(LAST_RANKING_FILE, ranking)


def load_weekly_baseline():
    return load_json(WEEKLY_FILE)


def save_weekly_baseline(data):
    save_json(WEEKLY_FILE, data)


# ─────────────────────────────────────────────
# VARIAÇÕES DE TEXTO — alertas
# ─────────────────────────────────────────────

def alert_header(name, delta_day):
    """Gera cabeçalho com variação acumulada do dia. Sorteia entre variações."""
    abs_d   = abs(delta_day)
    subindo = delta_day > 0
    is_renan = name == RENAN_NAME

    if is_renan:
        if abs_d >= 4:
            opts = [
                f"🚀 RENAN DISPARA! Candidato surpreende e sobe {abs_d:.1f}pp hoje!",
                f"🚀 RENAN EXPLODE! +{abs_d:.1f}pp em um único dia nas apostas!",
                f"🚀 IMPRESSIONANTE! Renan Santos acumula +{abs_d:.1f}pp hoje nas apostas internacionais!",
            ]
        elif abs_d >= 2:
            opts = [
                f"🔥 Renan Santos em ALTA — sobe {abs_d:.1f}pp no dia!",
                f"🔥 Renan disparando — já acumula +{abs_d:.1f}pp hoje!",
                f"🔥 Que dia pro Renan! +{abs_d:.1f}pp acumulados desde hoje cedo",
            ]
        else:
            opts = [
                f"⭐ Renan Santos avança nas apostas! +{abs_d:.1f}pp desde hoje cedo",
                f"⭐ Renan em movimento — subiu {abs_d:.1f}pp no dia até agora",
                f"⭐ Renan Santos ganhando terreno — +{abs_d:.1f}pp desde a manhã",
            ]
    else:
        short = name.split()[0]  # primeiro nome para textos mais curtos
        if subindo:
            if abs_d >= 4:
                opts = [
                    f"🚀 {name} acumula +{abs_d:.1f}pp hoje nas apostas",
                    f"📈 Grande movimento — {short} sobe {abs_d:.1f}pp no dia nas apostas",
                    f"📊 {name} em forte alta — +{abs_d:.1f}pp acumulados hoje",
                ]
            elif abs_d >= 2:
                opts = [
                    f"📈 EM ALTA — {short} sobe {abs_d:.1f}pp no acumulado do dia",
                    f"📈 {name} em alta — +{abs_d:.1f}pp desde hoje cedo",
                    f"📈 {short} ganhando força ao longo do dia — +{abs_d:.1f}pp acumulados",
                ]
            else:
                opts = [
                    f"📊 ATUALIZAÇÃO — {short} subiu {abs_d:.1f}pp no acumulado do dia",
                    f"📊 {name} avança {abs_d:.1f}pp desde a manhã",
                    f"📊 {short} em leve alta hoje — +{abs_d:.1f}pp acumulados",
                ]
        else:
            if abs_d >= 4:
                opts = [
                    f"📉 DERRETEU! {name} despenca {abs_d:.1f}pp nas apostas hoje",
                    f"📉 QUE TOMBO! {short} perde {abs_d:.1f}pp em um único dia",
                    f"📉 {name} acumula -{abs_d:.1f}pp hoje nas apostas internacionais",
                ]
            elif abs_d >= 2:
                opts = [
                    f"📉 EM BAIXA — {short} recua {abs_d:.1f}pp no acumulado do dia",
                    f"📉 {name} perdendo força ao longo do dia — -{abs_d:.1f}pp",
                    f"📉 {short} em queda — acumula -{abs_d:.1f}pp desde hoje cedo",
                ]
            else:
                opts = [
                    f"📊 ATUALIZAÇÃO — {short} caiu {abs_d:.1f}pp no acumulado do dia",
                    f"📊 {name} recua {abs_d:.1f}pp desde a manhã",
                    f"📊 {short} em leve baixa hoje — -{abs_d:.1f}pp acumulados",
                ]

    return random.choice(opts)


def renan_flavio_line(candidates):
    renan  = next((c for c in candidates if c["candidate"] == RENAN_NAME), None)
    flavio = next((c for c in candidates if c["candidate"] == FLAVIO_NAME), None)
    if renan and flavio:
        diff = round(flavio["probability"] - renan["probability"], 1)
        return f"📌 Renan se aproxima de Flávio: diferença caiu para {diff}pp"
    return None


# ─────────────────────────────────────────────
# FORMATO DE VARIAÇÃO
# ─────────────────────────────────────────────

def format_var(delta):
    if delta is None:
        return " —"
    if delta > 0:
        return f" ▲ +{delta:.1f}pp"
    if delta < 0:
        return f" ▼ {delta:.1f}pp"
    return " ↔ 0.0pp"


# ─────────────────────────────────────────────
# TWEETS
# ─────────────────────────────────────────────

def build_scheduled_tweet(candidates, last_post, label, include_trend=True):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    top5 = candidates[:5]
    lines = [
        "🗳 Eleição Presidencial Brasil 2026",
        f"📊 Polymarket — {now_str}",
        "",
    ]
    for i, c in enumerate(top5):
        name  = c["candidate"]
        prob  = c["probability"]
        delta = None
        if last_post:
            prev = next(
                (x["probability"] for x in last_post.get("candidates", []) if x["candidate"] == name),
                None,
            )
            if prev is not None:
                delta = round(prob - prev, 1)
        lines.append(f"{MEDAL[i]} {name}: {prob:.1f}%{format_var(delta)}")

    # Tendência 3 dias
    if include_trend:
        trend_parts = []
        for c in top5[:3]:
            tid = c.get("token_id", "")
            if tid:
                t = get_trend_3days(tid)
                trend_parts.append(f"{c['candidate'].split()[0]} {t}")
        if trend_parts:
            lines += ["", "📈 Tendência 3 dias: " + " | ".join(trend_parts)]

    lines += ["", f"🕐 {label}", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_alert_tweet(candidates, first_post_day, last_post, trigger_name, trigger_delta_day):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    top5    = candidates[:5]
    header  = alert_header(trigger_name, trigger_delta_day)

    lines = [header, "", "🗳 Eleição Presidencial Brasil 2026", f"📊 Polymarket — {now_str}", ""]

    for i, c in enumerate(top5):
        name  = c["candidate"]
        prob  = c["probability"]
        # Variação desde o primeiro post do dia
        delta_day = None
        if first_post_day:
            prev = next(
                (x["probability"] for x in first_post_day.get("candidates", []) if x["candidate"] == name),
                None,
            )
            if prev is not None:
                delta_day = round(prob - prev, 1)
        lines.append(f"{MEDAL[i]} {name}: {prob:.1f}%{format_var(delta_day)}")

    # Linha Renan x Flávio
    add_renan_line = (
        trigger_name == RENAN_NAME and trigger_delta_day > 0
    ) or (
        trigger_name == FLAVIO_NAME and trigger_delta_day < 0
    )
    if add_renan_line:
        extra = renan_flavio_line(candidates)
        if extra:
            lines += ["", extra]

    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_daily_summary_tweet(candidates, first_post_day):
    now_str  = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    top5     = candidates[:5]
    best_name  = None
    best_delta = 0

    lines = [
        f"🌙 Resumo do dia — {now_str}",
        "",
        "Como os candidatos fecharam o dia",
        "nas apostas do Polymarket:",
        "",
    ]

    for i, c in enumerate(top5):
        name  = c["candidate"]
        prob  = c["probability"]
        delta = None
        if first_post_day:
            prev = next(
                (x["probability"] for x in first_post_day.get("candidates", []) if x["candidate"] == name),
                None,
            )
            if prev is not None:
                delta = round(prob - prev, 1)
                if abs(delta) > abs(best_delta):
                    best_delta = delta
                    best_name  = name
        lines.append(f"{MEDAL[i]} {name}: {prob:.1f}%{format_var(delta)}")

    if best_name:
        direction = "avançou" if best_delta > 0 else "recuou"
        lines += [
            "",
            f"📌 Destaque do dia: {best_name}",
            f"foi quem mais se moveu — {format_var(best_delta).strip()} desde a manhã",
        ]

    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_weekly_summary_tweet(candidates, weekly_baseline):
    now_str  = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    top5     = candidates[:5]
    best_name  = None
    best_delta = 0
    prev_lula  = None
    prev_flavio = None

    lines = [
        f"📅 Resumo da semana — {now_str}",
        "",
        "Como os candidatos variaram",
        "nos últimos 7 dias no Polymarket:",
        "",
    ]

    for i, c in enumerate(top5):
        name  = c["candidate"]
        prob  = c["probability"]
        delta = None
        if weekly_baseline:
            prev = next(
                (x["probability"] for x in weekly_baseline.get("candidates", []) if x["candidate"] == name),
                None,
            )
            if prev is not None:
                delta = round(prob - prev, 1)
                if abs(delta) > abs(best_delta):
                    best_delta = delta
                    best_name  = name
                if name == LULA_NAME:
                    prev_lula = prev
                if name == FLAVIO_NAME:
                    prev_flavio = prev
        lines.append(f"{MEDAL[i]} {name}: {prob:.1f}%{format_var(delta)}")

    if best_name:
        direction = "maior avanço" if best_delta > 0 else "maior queda"
        lines += [
            "",
            f"📌 Destaque da semana: {best_name}",
            f"{direction} — {format_var(best_delta).strip()} em 7 dias",
        ]

    if prev_lula and prev_flavio:
        lines += [
            "",
            "Comparando com 7 dias atrás:",
            f"Lula era líder com {prev_lula:.1f}% | Flávio em {prev_flavio:.1f}%",
        ]

    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_overtake_tweet(overtaker, overtaken, candidates):
    o1 = next((c for c in candidates if c["candidate"] == overtaker), None)
    o2 = next((c for c in candidates if c["candidate"] == overtaken), None)
    if not o1 or not o2:
        return None

    # Determina intensidade
    is_historic = (
        (overtaker == RENAN_NAME and overtaken == FLAVIO_NAME) or
        (overtaker == FLAVIO_NAME and overtaken == LULA_NAME)
    )

    if is_historic:
        header = "🚨 VIRADA HISTÓRICA NAS APOSTAS!"
        footer = "O favoritismo mudou de mão\nnas apostas internacionais."
    else:
        header = "📊 MUDANÇA NO RANKING!"
        footer = ""

    i1 = next((i for i, c in enumerate(candidates) if c["candidate"] == overtaker), 0)
    i2 = next((i for i, c in enumerate(candidates) if c["candidate"] == overtaken), 1)

    lines = [
        header,
        "",
        f"{overtaker} ultrapassou",
        f"{overtaken} nas apostas do Polymarket.",
        "",
        f"{MEDAL[i1]} {overtaker}: {o1['probability']:.1f}% ▲",
        f"{MEDAL[i2]} {overtaken}: {o2['probability']:.1f}% ▼",
    ]
    if footer:
        lines += ["", footer]
    lines += ["", "#Eleicoes2026 #Brasil #Polymarket #Virada"]
    return "\n".join(lines)


def build_new_candidate_tweet(name):
    lines = [
        "🚨 ÚLTIMA HORA — Novo candidato nas apostas!",
        "",
        f"{name} acaba de aparecer nas probabilidades",
        "do Polymarket para a Eleição Presidencial 2026.",
        "",
        "🔔 Fique de olho nas próximas atualizações.",
        "",
        f"#Eleicoes2026 #Brasil #Polymarket #{name.replace(' ', '')}",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# TWITTER / X
# ─────────────────────────────────────────────

def get_twitter_client():
    return tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )


def get_twitter_api_v1():
    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_SECRET,
        TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET,
    )
    return tweepy.API(auth)


def post_tweet(text, chart_path=None):
    try:
        media_id = None
        if chart_path and chart_path.exists():
            try:
                api_v1 = get_twitter_api_v1()
                media = api_v1.media_upload(str(chart_path))
                media_id = media.media_id
                log.info("Mídia enviada, media_id=%s", media_id)
            except Exception as e:
                log.warning("Não foi possível enviar imagem: %s", e)

        client = get_twitter_client()
        kwargs = {"text": text}
        if media_id:
            kwargs["media_ids"] = [media_id]

        response = client.create_tweet(**kwargs)
        tweet_id = response.data["id"]
        log.info("Tweet postado! ID: %s", tweet_id)
        return tweet_id
    except Exception as e:
        log.exception("Erro ao postar tweet: %s", e)
        return None


# ─────────────────────────────────────────────
# LÓGICA PRINCIPAL
# ─────────────────────────────────────────────

def run_scheduled_post(label):
    log.info("Post agendado: %s", label)
    candidates = fetch_polymarket_data()
    if not candidates:
        log.error("Sem dados. Post cancelado.")
        return

    last_post = load_last_post()

    # Salva primeiro post do dia
    today_str = datetime.now(BRAZIL_TZ).date().isoformat()
    first_day = load_first_post_day()
    if not first_day or first_day.get("date") != today_str:
        save_first_post_day({"date": today_str, "candidates": candidates})
        log.info("Primeiro post do dia salvo.")

    text       = build_scheduled_tweet(candidates, last_post, label)
    chart_path = build_chart(candidates[:5])
    tweet_id   = post_tweet(text, chart_path)

    if tweet_id:
        save_last_post({
            "tweet_id": tweet_id,
            "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
            "candidates": candidates,
        })
        known = load_known_candidates()
        known.update(c["candidate"] for c in candidates)
        save_known_candidates(known)
        save_last_ranking([c["candidate"] for c in candidates[:5]])
        log.info("Estado salvo.")


def run_daily_summary():
    log.info("Resumo do dia")
    candidates = fetch_polymarket_data()
    if not candidates:
        return
    first_day = load_first_post_day()
    text       = build_daily_summary_tweet(candidates, first_day)
    chart_path = build_chart(candidates[:5])
    tweet_id   = post_tweet(text, chart_path)
    if tweet_id:
        save_last_post({
            "tweet_id": tweet_id,
            "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
            "candidates": candidates,
        })
        log.info("Resumo do dia postado.")


def run_weekly_summary():
    log.info("Resumo da semana")
    candidates = fetch_polymarket_data()
    if not candidates:
        return
    weekly = load_weekly_baseline()
    text   = build_weekly_summary_tweet(candidates, weekly)
    chart_path = build_chart(candidates[:5])
    tweet_id   = post_tweet(text, chart_path)
    if tweet_id:
        # Atualiza baseline semanal após postar
        save_weekly_baseline({
            "date": datetime.now(BRAZIL_TZ).date().isoformat(),
            "candidates": candidates,
        })
        save_last_post({
            "tweet_id": tweet_id,
            "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
            "candidates": candidates,
        })
        log.info("Resumo semanal postado e baseline atualizado.")


def check_alert():
    last_post  = load_last_post()
    candidates = fetch_polymarket_data()
    if not candidates:
        return

    # ── Novos candidatos ────────────────────────────────────────
    known = load_known_candidates()
    if known:
        for c in candidates:
            if c["candidate"] not in known:
                log.info("Novo candidato: %s", c["candidate"])
                post_tweet(build_new_candidate_tweet(c["candidate"]))
                known.add(c["candidate"])
        save_known_candidates(known)
    else:
        save_known_candidates({c["candidate"] for c in candidates})

    # ── Ultrapassagens ──────────────────────────────────────────
    last_ranking = load_last_ranking()
    new_ranking  = [c["candidate"] for c in candidates[:5]]
    if last_ranking and new_ranking != last_ranking:
        for pos, name in enumerate(new_ranking):
            if pos < len(last_ranking) and name != last_ranking[pos]:
                # Quem estava nessa posição antes
                overtaken = last_ranking[pos]
                if overtaken in new_ranking and new_ranking.index(overtaken) > pos:
                    log.info("Ultrapassagem: %s passou %s", name, overtaken)
                    text = build_overtake_tweet(name, overtaken, candidates)
                    if text:
                        chart_path = build_chart(candidates[:5])
                        post_tweet(text, chart_path)
                    break  # Uma ultrapassagem por ciclo
    save_last_ranking(new_ranking)

    if not last_post:
        return

    # ── Alertas de variação ─────────────────────────────────────
    first_day = load_first_post_day()
    last_map  = {c["candidate"]: c["probability"] for c in last_post.get("candidates", [])}
    first_map = {c["candidate"]: c["probability"] for c in (first_day or {}).get("candidates", [])}

    for c in candidates[:5]:
        name  = c["candidate"]
        prob  = c["probability"]
        prev  = last_map.get(name)

        if prev is None:
            continue

        delta_since_last = round(prob - prev, 1)

        if abs(delta_since_last) < ALERT_THRESHOLD:
            continue

        # Renan — não posta se estiver caindo
        if name == RENAN_NAME and delta_since_last < 0:
            log.info("Queda do Renan ignorada.")
            continue

        # Delta acumulado do dia para o texto
        delta_day = None
        prev_day = first_map.get(name)
        if prev_day is not None:
            delta_day = round(prob - prev_day, 1)
        if delta_day is None:
            delta_day = delta_since_last

        log.info("Alerta: %s delta_last=%.1f delta_day=%.1f", name, delta_since_last, delta_day)
        text       = build_alert_tweet(candidates, first_day, last_post, name, delta_day)
        chart_path = build_chart(candidates[:5])
        tweet_id   = post_tweet(text, chart_path)

        if tweet_id:
            save_last_post({
                "tweet_id": tweet_id,
                "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
                "candidates": candidates,
            })
        break  # Um alerta por ciclo


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
    now    = datetime.now(BRAZIL_TZ)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_scheduler():
    log.info("Bot iniciado.")
    schedule = {hour: next_time(hour) for hour, _, _ in SCHEDULED_HOURS}

    # Baseline semanal — se não existir, cria agora
    if not load_weekly_baseline():
        candidates = fetch_polymarket_data()
        if candidates:
            save_weekly_baseline({
                "date": datetime.now(BRAZIL_TZ).date().isoformat(),
                "candidates": candidates,
            })
            log.info("Baseline semanal criado.")

    for hour, _, label in SCHEDULED_HOURS:
        log.info("Proximo %s: %s", label, schedule[hour].strftime("%d/%m %H:%M"))

    while True:
        now     = datetime.now(BRAZIL_TZ)
        weekday = now.weekday()  # 4 = sexta

        for hour, kind, label in SCHEDULED_HOURS:
            if now >= schedule[hour]:
                if kind == "summary":
                    run_daily_summary()
                else:
                    run_scheduled_post(label)
                schedule[hour] = next_time(hour)

        # Resumo semanal — sexta às 22h (junto com resumo do dia)
        # Já tratado acima; aqui verificamos se é sexta para também rodar weekly
        if weekday == 4 and now.hour == 22 and now.minute < 5:
            weekly = load_weekly_baseline()
            last_weekly_date = weekly.get("date") if weekly else None
            today_str = now.date().isoformat()
            if last_weekly_date != today_str:
                run_weekly_summary()

        check_alert()
        time.sleep(300)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_scheduled_post("🔧 Teste manual")
    else:
        run_scheduler()
