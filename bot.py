"""
Bot X - Eleições Brasil 2026
Posts automáticos às 8h, 16h e 21h + alertas inteligentes
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

DATA_DIR       = Path(__file__).parent / "data"
LOGS_DIR       = Path(__file__).parent / "logs"
CHARTS_DIR     = Path(__file__).parent / "charts"
LAST_POST_FILE = DATA_DIR / "last_post.json"
KNOWN_CANDIDATES_FILE = DATA_DIR / "known_candidates.json"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
CHARTS_DIR.mkdir(exist_ok=True)

ALERT_THRESHOLD = 1.0
CLOB_BASE  = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

RENAN_NAME = "Renan Santos"
FLAVIO_NAME = "Flávio Bolsonaro"
JOAQUIM_NAME = "Joaquim Barbosa"

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

def load_last_post():
    if LAST_POST_FILE.exists():
        with open(LAST_POST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_last_post(data):
    with open(LAST_POST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_known_candidates():
    if KNOWN_CANDIDATES_FILE.exists():
        with open(KNOWN_CANDIDATES_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_known_candidates(names):
    with open(KNOWN_CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(names), f, ensure_ascii=False)


# ─────────────────────────────────────────────
# CABEÇALHOS DE ALERTA
# ─────────────────────────────────────────────

def alert_header(name, delta):
    """
    Gera o cabeçalho do post extra com tom adequado ao candidato e à magnitude.
    delta > 0 = subida, delta < 0 = queda
    """
    abs_delta = abs(delta)
    subindo   = delta > 0
    is_renan  = name == RENAN_NAME

    if is_renan:
        # Renan — sempre enaltecendo quando sobe
        if abs_delta >= 4:
            return f"🚀 RENAN DISPARA! Candidato surpreende e sobe {abs_delta:.1f}pp nas apostas!"
        elif abs_delta >= 2:
            return f"🔥 Renan Santos em ALTA — sobe {abs_delta:.1f}pp nas apostas!"
        else:
            return f"⭐ Renan Santos avança nas apostas! +{abs_delta:.1f}pp"
    else:
        # Demais candidatos — neutro
        if subindo:
            if abs_delta >= 4:
                return f"🚀 DISPAROU! {name} sobe {abs_delta:.1f}pp nas apostas"
            elif abs_delta >= 2:
                return f"📈 EM ALTA — {name} sobe {abs_delta:.1f}pp nas apostas"
            else:
                return f"📊 ATUALIZAÇÃO — {name} subiu {abs_delta:.1f}pp"
        else:
            if abs_delta >= 4:
                return f"📉 DERRETEU! {name} despenca {abs_delta:.1f}pp nas apostas"
            elif abs_delta >= 2:
                return f"📉 EM BAIXA — {name} recua {abs_delta:.1f}pp nas apostas"
            else:
                return f"📊 ATUALIZAÇÃO — {name} caiu {abs_delta:.1f}pp"


def renan_flavio_line(candidates):
    """
    Linha extra mostrando aproximação Renan x Flávio.
    Retorna string ou None.
    """
    renan  = next((c for c in candidates if c["candidate"] == RENAN_NAME), None)
    flavio = next((c for c in candidates if c["candidate"] == FLAVIO_NAME), None)
    if renan and flavio:
        diff = round(flavio["probability"] - renan["probability"], 1)
        return f"📌 Renan se aproxima de Flávio: diferença caiu para {diff}pp"
    return None


# ─────────────────────────────────────────────
# TEXTO DOS TWEETS
# ─────────────────────────────────────────────

def format_variation(delta):
    if delta is None:
        return " —"
    if delta > 0:
        return f" ▲ +{delta:.1f}pp"
    if delta < 0:
        return f" ▼ {delta:.1f}pp"
    return " ↔ 0.0pp"


def build_scheduled_tweet(candidates, last_post, label):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    top5 = candidates[:5]
    lines = [
        "🗳 Eleição Presidencial Brasil 2026",
        f"📊 Polymarket — {now_str}",
        "",
    ]
    for i, c in enumerate(top5):
        name = c["candidate"]
        prob = c["probability"]
        delta = None
        if last_post:
            prev = next(
                (x["probability"] for x in last_post.get("candidates", []) if x["candidate"] == name),
                None,
            )
            if prev is not None:
                delta = round(prob - prev, 1)
        lines.append(f"{MEDAL[i]} {name}: {prob:.1f}%{format_variation(delta)}")
    lines += ["", f"🕐 {label}", "#Eleicoes2026 #Brasil #Polymarket"]
    return "\n".join(lines)


def build_alert_tweet(candidates, last_post, trigger_name, trigger_delta):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    top5    = candidates[:5]

    header = alert_header(trigger_name, trigger_delta)

    lines = [
        header,
        "",
        "🗳 Eleição Presidencial Brasil 2026",
        f"📊 Polymarket — {now_str}",
        "",
    ]
    for i, c in enumerate(top5):
        name = c["candidate"]
        prob = c["probability"]
        delta = None
        if last_post:
            prev = next(
                (x["probability"] for x in last_post.get("candidates", []) if x["candidate"] == name),
                None,
            )
            if prev is not None:
                delta = round(prob - prev, 1)
        lines.append(f"{MEDAL[i]} {name}: {prob:.1f}%{format_variation(delta)}")

    # Linha especial Renan x Flávio
    add_renan_line = (
        trigger_name == RENAN_NAME and trigger_delta > 0
    ) or (
        trigger_name == FLAVIO_NAME and trigger_delta < 0
    )
    if add_renan_line:
        extra = renan_flavio_line(candidates)
        if extra:
            lines += ["", extra]

    lines += ["", "#Eleicoes2026 #Brasil #Polymarket"]
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
    text      = build_scheduled_tweet(candidates, last_post, label)
    log.info("Texto:\n%s", text)

    chart_path = build_chart(candidates[:5])
    tweet_id   = post_tweet(text, chart_path)

    if tweet_id:
        save_last_post({
            "tweet_id": tweet_id,
            "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
            "candidates": candidates,
        })
        # Atualiza candidatos conhecidos
        known = load_known_candidates()
        known.update(c["candidate"] for c in candidates)
        save_known_candidates(known)
        log.info("Estado salvo.")


def check_alert():
    last_post = load_last_post()
    candidates = fetch_polymarket_data()
    if not candidates:
        return

    # ── Verifica novos candidatos ────────────────────────────────
    known = load_known_candidates()
    if known:  # só verifica se já temos uma lista base
        for c in candidates:
            if c["candidate"] not in known:
                log.info("Novo candidato detectado: %s", c["candidate"])
                text = build_new_candidate_tweet(c["candidate"])
                post_tweet(text, chart_path=None)
                known.add(c["candidate"])
        save_known_candidates(known)
    else:
        # Primeira execução — salva lista atual sem postar
        save_known_candidates({c["candidate"] for c in candidates})

    if not last_post:
        return

    last_map = {c["candidate"]: c["probability"] for c in last_post.get("candidates", [])}

    for c in candidates[:5]:
        name  = c["candidate"]
        prob  = c["probability"]
        prev  = last_map.get(name)

        if prev is None:
            continue

        delta = round(prob - prev, 1)
        abs_delta = abs(delta)

        if abs_delta < ALERT_THRESHOLD:
            continue

        # Renan — não posta se estiver caindo
        if name == RENAN_NAME and delta < 0:
            log.info("Queda do Renan ignorada (delta=%.1f)", delta)
            continue

        log.info("Alerta: %s delta=%.1f", name, delta)
        text       = build_alert_tweet(candidates, last_post, name, delta)
        chart_path = build_chart(candidates[:5])
        tweet_id   = post_tweet(text, chart_path)

        if tweet_id:
            save_last_post({
                "tweet_id": tweet_id,
                "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
                "candidates": candidates,
            })
            # Só processa um alerta por ciclo para evitar flood
            break


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

SCHEDULED_HOURS = [
    (8,  "🌅 Post agendado 08:00"),
    (16, "🌆 Post agendado 16:00"),
    (21, "🌙 Post agendado 21:00"),
]


def next_scheduled_time(hour):
    now = datetime.now(BRAZIL_TZ)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_scheduler():
    log.info("Bot iniciado. Configurando horarios...")

    schedule = {hour: next_scheduled_time(hour) for hour, _ in SCHEDULED_HOURS}
    for hour, label in SCHEDULED_HOURS:
        log.info("Proximo %s: %s", label, schedule[hour].strftime("%d/%m %H:%M"))

    while True:
        now = datetime.now(BRAZIL_TZ)

        for hour, label in SCHEDULED_HOURS:
            if now >= schedule[hour]:
                run_scheduled_post(label)
                schedule[hour] = next_scheduled_time(hour)

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
