"""
Bot X - Eleições Brasil 2026
"""

import os
import json
import time
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

DATA_DIR     = Path(__file__).parent / "data"
LOGS_DIR     = Path(__file__).parent / "logs"
CHARTS_DIR   = Path(__file__).parent / "charts"
HISTORY_FILE = DATA_DIR / "history.json"
LAST_POST_FILE = DATA_DIR / "last_post.json"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
CHARTS_DIR.mkdir(exist_ok=True)

ALERT_THRESHOLD = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("eleicoes_bot")


def fetch_polymarket_data():
    try:
        # Busca via API gamma do Polymarket
        url = f"https://gamma-api.polymarket.com/events?slug={POLYMARKET_MARKET_SLUG}"
        log.info("Buscando: %s", url)
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        log.info("Resposta recebida: %s itens", len(data))

        if not data:
            log.error("Nenhum evento encontrado para slug '%s'", POLYMARKET_MARKET_SLUG)
            return None

        event = data[0]
        markets = event.get("markets", [])

        if not markets:
            log.error("Evento sem markets.")
            return None

        candidates = []
        for market in markets:
            name = market.get("groupItemTitle") or market.get("question", "Desconhecido")
            # outcomePrices é uma string JSON como '["0.46", "0.54"]'
            outcome_prices = market.get("outcomePrices", "[]")
            outcomes = market.get("outcomes", "[]")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)

            # Pega o preço do "Yes" (primeiro outcome)
            if outcome_prices:
                prob = float(outcome_prices[0]) * 100
                candidates.append({"candidate": name, "probability": round(prob, 1)})

        if not candidates:
            log.error("Nenhum candidato extraído.")
            return None

        candidates.sort(key=lambda x: x["probability"], reverse=True)
        log.info("Candidatos: %s", candidates)
        return candidates

    except Exception as e:
        log.exception("Erro ao buscar Polymarket: %s", e)
        return None


def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_last_post():
    if LAST_POST_FILE.exists():
        with open(LAST_POST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_last_post(data):
    with open(LAST_POST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_to_history(candidates):
    history = load_history()
    today_str = datetime.now(BRAZIL_TZ).date().isoformat()
    history = [h for h in history if h["date"] != today_str]
    history.append({
        "date": today_str,
        "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
        "candidates": candidates,
    })
    save_history(history)


CANDIDATE_COLORS = ["#E63946", "#457B9D", "#2A9D8F", "#E9C46A", "#F4A261"]

def build_chart(top5_names):
    history = load_history()
    if len(history) < 2:
        log.warning("Histórico insuficiente para gráfico.")
        return None

    dates = [datetime.fromisoformat(h["date"]) for h in history]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    fig.patch.set_facecolor("#0D1117")
    ax.set_facecolor("#0D1117")

    for idx, name in enumerate(top5_names):
        probs = []
        for snapshot in history:
            val = next((c["probability"] for c in snapshot["candidates"] if c["candidate"] == name), None)
            probs.append(val)
        probs_arr = np.array([p if p is not None else np.nan for p in probs], dtype=float)
        color = CANDIDATE_COLORS[idx % len(CANDIDATE_COLORS)]
        ax.plot(dates, probs_arr, label=name, color=color, linewidth=2.5, marker="o", markersize=4)
        last_valid = next(((d, v) for d, v in zip(reversed(dates), reversed(probs_arr)) if not np.isnan(v)), None)
        if last_valid:
            ax.annotate(f"{last_valid[1]:.1f}%", xy=last_valid, xytext=(6, 0),
                        textcoords="offset points", color=color, fontsize=8, fontweight="bold")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.tick_params(colors="#AAAAAA", labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333333")
    ax.grid(color="#222222", linestyle="--", linewidth=0.7, alpha=0.7)
    ax.set_title("Eleição Presidencial Brasil 2026 — Polymarket", color="white", fontsize=13, fontweight="bold", pad=14)
    ax.set_ylabel("Probabilidade (%)", color="#AAAAAA", fontsize=9)
    ax.legend(loc="upper left", fontsize=8.5, facecolor="#1A1F2E", edgecolor="#333333", labelcolor="white", framealpha=0.9)

    plt.tight_layout()
    chart_path = CHARTS_DIR / f"chart_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(chart_path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close()
    log.info("Gráfico salvo: %s", chart_path)
    return chart_path


MEDAL = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

def format_variation(delta):
    if delta is None:
        return "  —"
    if delta > 0:
        return f"  ▲ +{delta:.1f}pp"
    if delta < 0:
        return f"  ▼ {delta:.1f}pp"
    return "  ↔ 0.0pp"


def build_tweet_text(candidates, last_post, reason):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    top5 = candidates[:5]
    lines = [
        "🗳️ Eleição Presidencial Brasil 2026",
        f"📊 Probabilidades Polymarket — {now_str}",
        "",
    ]
    for i, c in enumerate(top5):
        name = c["candidate"]
        prob = c["probability"]
        delta = None
        if last_post:
            prev = next((x["probability"] for x in last_post.get("candidates", []) if x["candidate"] == name), None)
            if prev is not None:
                delta = round(prob - prev, 1)
        lines.append(f"{MEDAL[i]} {name}: {prob:.1f}%{format_variation(delta)}")
    lines += ["", f"📈 Fonte: Polymarket", f"🕐 {reason}", "#Eleições2026 #Brasil #Polymarket"]
    return "\n".join(lines)


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
                log.warning("Não foi possível enviar imagem (pode precisar de plano pago): %s", e)

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


def run_post(reason):
    log.info("Iniciando post — motivo: %s", reason)
    candidates = fetch_polymarket_data()
    if not candidates:
        log.error("Sem dados do Polymarket. Post cancelado.")
        return

    last_post = load_last_post()
    append_to_history(candidates)

    text = build_tweet_text(candidates, last_post, reason)
    log.info("Texto do tweet:\n%s", text)

    top5_names = [c["candidate"] for c in candidates[:5]]
    chart_path = build_chart(top5_names)

    tweet_id = post_tweet(text, chart_path)
    if tweet_id:
        save_last_post({
            "tweet_id": tweet_id,
            "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
            "candidates": candidates,
        })


def check_alert():
    last_post = load_last_post()
    if not last_post:
        return
    candidates = fetch_polymarket_data()
    if not candidates:
        return
    last_candidates = {c["candidate"]: c["probability"] for c in last_post.get("candidates", [])}
    triggered = []
    for c in candidates[:5]:
        prev = last_candidates.get(c["candidate"])
        if prev is not None:
            delta = abs(c["probability"] - prev)
            if delta >= ALERT_THRESHOLD:
                triggered.append(f"{c['candidate']} ({delta:+.1f}pp)")
    if triggered:
        run_post(f"⚡ Alerta: variação > {ALERT_THRESHOLD}pp — {', '.join(triggered)}")


def next_scheduled_time(hour):
    now = datetime.now(BRAZIL_TZ)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_scheduler():
    log.info("Bot iniciado. Aguardando próximos horários agendados...")
    next_8h  = next_scheduled_time(8)
    next_16h = next_scheduled_time(16)
    CHECK_INTERVAL = 300

    while True:
        now = datetime.now(BRAZIL_TZ)
        if now >= next_8h:
            run_post("🌅 Post agendado das 08:00")
            next_8h = next_scheduled_time(8)
        if now >= next_16h:
            run_post("🌆 Post agendado das 16:00")
            next_16h = next_scheduled_time(16)
        check_alert()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_post("🔧 Post manual de teste")
