"""
Bot X - Eleições Brasil 2026
Posta percentuais do Polymarket automaticamente às 8h e 16h,
e sempre que algum candidato variar mais de 1% desde o último post.
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

# ─────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")

# Credenciais Twitter/X  →  preencha com suas chaves de API
TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY", "SUA_API_KEY")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET", "SUA_API_SECRET")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN", "SEU_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "SEU_ACCESS_TOKEN_SECRET")
TWITTER_BEARER_TOKEN        = os.environ.get("TWITTER_BEARER_TOKEN", "SEU_BEARER_TOKEN")

# Polymarket — slug do mercado de eleição presidencial Brasil 2026
# Ajuste o MARKET_SLUG conforme o slug real no Polymarket
POLYMARKET_MARKET_SLUG = os.environ.get(
    "POLYMARKET_MARKET_SLUG",
    "brazil-2026-presidential-election-winner"
)

# Arquivos de estado
DATA_DIR        = Path(__file__).parent / "data"
LOGS_DIR        = Path(__file__).parent / "logs"
CHARTS_DIR      = Path(__file__).parent / "charts"
HISTORY_FILE    = DATA_DIR / "history.json"
LAST_POST_FILE  = DATA_DIR / "last_post.json"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
CHARTS_DIR.mkdir(exist_ok=True)

# Variação mínima para disparar post extra (em pontos percentuais)
ALERT_THRESHOLD = 1.0

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("eleicoes_bot")


# ─────────────────────────────────────────────
# POLYMARKET
# ─────────────────────────────────────────────

def fetch_polymarket_data() -> list[dict] | None:
    """
    Busca os dados de probabilidade do mercado no Polymarket via API CLOB.
    Retorna lista de dicts: [{"candidate": str, "probability": float}, ...]
    ordenada do maior para o menor.
    """
    try:
        # 1) Busca o mercado pelo slug
        url_market = f"https://gamma-api.polymarket.com/markets?slug={POLYMARKET_MARKET_SLUG}"
        resp = requests.get(url_market, timeout=15)
        resp.raise_for_status()
        markets = resp.json()

        if not markets:
            log.error("Nenhum mercado encontrado para o slug '%s'", POLYMARKET_MARKET_SLUG)
            return None

        market = markets[0]
        tokens = market.get("tokens", [])

        if not tokens:
            log.error("Mercado sem tokens/outcomes.")
            return None

        # Cada token tem outcome (nome) e price (probabilidade 0-1)
        candidates = []
        for token in tokens:
            name = token.get("outcome", "Desconhecido")
            prob = float(token.get("price", 0)) * 100  # converte para %
            candidates.append({"candidate": name, "probability": round(prob, 1)})

        # Ordena do maior para o menor
        candidates.sort(key=lambda x: x["probability"], reverse=True)
        return candidates

    except Exception as e:
        log.exception("Erro ao buscar dados do Polymarket: %s", e)
        return None


# ─────────────────────────────────────────────
# HISTÓRICO
# ─────────────────────────────────────────────

def load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history: list[dict]):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_last_post() -> dict | None:
    if LAST_POST_FILE.exists():
        with open(LAST_POST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_last_post(data: dict):
    with open(LAST_POST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_to_history(candidates: list[dict]):
    """Guarda snapshot com timestamp no histórico diário."""
    history = load_history()
    now = datetime.now(BRAZIL_TZ).isoformat()

    # Evita duplicatas no mesmo dia (mantém último snapshot do dia)
    today_str = datetime.now(BRAZIL_TZ).date().isoformat()
    history = [h for h in history if h["date"] != today_str]

    history.append({
        "date": today_str,
        "timestamp": now,
        "candidates": candidates,
    })
    save_history(history)


# ─────────────────────────────────────────────
# GRÁFICO
# ─────────────────────────────────────────────

CANDIDATE_COLORS = [
    "#E63946",  # vermelho
    "#457B9D",  # azul
    "#2A9D8F",  # verde-azulado
    "#E9C46A",  # amarelo
    "#F4A261",  # laranja
]

def build_chart(top5_names: list[str]) -> Path:
    """Gera gráfico de linhas com histórico diário dos top-5 candidatos."""
    history = load_history()

    if len(history) < 2:
        log.warning("Histórico insuficiente para gráfico (%d pontos).", len(history))
        return None

    dates = [datetime.fromisoformat(h["date"]) for h in history]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    fig.patch.set_facecolor("#0D1117")
    ax.set_facecolor("#0D1117")

    for idx, name in enumerate(top5_names):
        probs = []
        for snapshot in history:
            val = next(
                (c["probability"] for c in snapshot["candidates"] if c["candidate"] == name),
                None,
            )
            probs.append(val)

        # Interpola None com NaN para matplotlib
        probs_arr = np.array([p if p is not None else np.nan for p in probs], dtype=float)
        color = CANDIDATE_COLORS[idx % len(CANDIDATE_COLORS)]

        ax.plot(
            dates, probs_arr,
            label=name,
            color=color,
            linewidth=2.5,
            marker="o",
            markersize=4,
        )
        # Rótulo no último ponto
        last_valid = next(
            ((d, v) for d, v in zip(reversed(dates), reversed(probs_arr)) if not np.isnan(v)),
            None,
        )
        if last_valid:
            ax.annotate(
                f"{last_valid[1]:.1f}%",
                xy=last_valid,
                xytext=(6, 0),
                textcoords="offset points",
                color=color,
                fontsize=8,
                fontweight="bold",
            )

    # Eixos e grade
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.tick_params(colors="#AAAAAA", labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333333")
    ax.grid(color="#222222", linestyle="--", linewidth=0.7, alpha=0.7)

    # Título e legenda
    ax.set_title(
        "Eleição Presidencial Brasil 2026 — Polymarket",
        color="white", fontsize=13, fontweight="bold", pad=14,
    )
    ax.set_ylabel("Probabilidade (%)", color="#AAAAAA", fontsize=9)
    legend = ax.legend(
        loc="upper left", fontsize=8.5,
        facecolor="#1A1F2E", edgecolor="#333333", labelcolor="white",
        framealpha=0.9,
    )

    plt.tight_layout()
    chart_path = CHARTS_DIR / f"chart_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(chart_path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close()
    log.info("Gráfico salvo em %s", chart_path)
    return chart_path


# ─────────────────────────────────────────────
# FORMATAÇÃO DO POST
# ─────────────────────────────────────────────

MEDAL = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

def format_variation(delta: float | None) -> str:
    if delta is None:
        return "  —"
    if delta > 0:
        return f"  ▲ +{delta:.1f}pp"
    if delta < 0:
        return f"  ▼ {delta:.1f}pp"
    return "  ↔ 0.0pp"


def build_tweet_text(candidates: list[dict], last_post: dict | None, reason: str) -> str:
    """Monta o texto do tweet com top-5 + variações."""
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    top5 = candidates[:5]

    lines = [
        "🗳️ *Eleição Presidencial Brasil 2026*",
        f"📊 Probabilidades Polymarket — {now_str}",
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

    lines += [
        "",
        f"📈 Fonte: Polymarket",
        f"🕐 {reason}",
        "#Eleições2026 #Brasil #Polymarket",
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
    """API v1.1 necessária para upload de mídia."""
    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_SECRET,
        TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET,
    )
    return tweepy.API(auth)


def post_tweet(text: str, chart_path: Path | None = None):
    """Posta tweet com texto e, opcionalmente, imagem do gráfico."""
    try:
        media_id = None

        if chart_path and chart_path.exists():
            api_v1 = get_twitter_api_v1()
            media = api_v1.media_upload(str(chart_path))
            media_id = media.media_id
            log.info("Mídia enviada, media_id=%s", media_id)

        client = get_twitter_client()
        kwargs = {"text": text}
        if media_id:
            kwargs["media_ids"] = [media_id]

        response = client.create_tweet(**kwargs)
        tweet_id = response.data["id"]
        log.info("Tweet postado com sucesso! ID: %s", tweet_id)
        return tweet_id

    except Exception as e:
        log.exception("Erro ao postar tweet: %s", e)
        return None


# ─────────────────────────────────────────────
# LÓGICA PRINCIPAL
# ─────────────────────────────────────────────

def run_post(reason: str):
    """Busca dados, gera gráfico e posta."""
    log.info("Iniciando post — motivo: %s", reason)

    candidates = fetch_polymarket_data()
    if not candidates:
        log.error("Sem dados do Polymarket. Post cancelado.")
        return

    last_post = load_last_post()

    # Salva no histórico (um snapshot por dia)
    append_to_history(candidates)

    # Texto do tweet
    text = build_tweet_text(candidates, last_post, reason)
    log.info("Texto do tweet:\n%s", text)

    # Gráfico
    top5_names = [c["candidate"] for c in candidates[:5]]
    chart_path = build_chart(top5_names)

    # Posta
    tweet_id = post_tweet(text, chart_path)

    if tweet_id:
        save_last_post({
            "tweet_id": tweet_id,
            "timestamp": datetime.now(BRAZIL_TZ).isoformat(),
            "candidates": candidates,
        })
        log.info("Estado salvo em last_post.json")
    else:
        log.warning("Tweet não postado — estado NÃO atualizado.")


def check_alert():
    """
    Verifica se algum candidato variou mais de ALERT_THRESHOLD pp
    desde o último post. Se sim, dispara post extra.
    """
    last_post = load_last_post()
    if not last_post:
        return  # Ainda não há post anterior

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
        reason = f"⚡ Alerta: variação > {ALERT_THRESHOLD}pp — {', '.join(triggered)}"
        log.info("Alerta disparado: %s", triggered)
        run_post(reason)
    else:
        log.info("Sem variação relevante. Nenhum post extra.")


# ─────────────────────────────────────────────
# SCHEDULER INTERNO (sem dependências externas)
# ─────────────────────────────────────────────

def next_scheduled_time(hour: int) -> datetime:
    """Retorna o próximo datetime (hoje ou amanhã) para o horário dado."""
    now = datetime.now(BRAZIL_TZ)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def seconds_until(dt: datetime) -> float:
    now = datetime.now(BRAZIL_TZ)
    return max(0.0, (dt - now).total_seconds())


def run_scheduler():
    """Loop principal: agenda posts das 8h e 16h + verificação de alerta a cada 5 min."""
    log.info("Bot iniciado. Aguardando próximos horários agendados...")

    # Próximos horários agendados
    next_8h  = next_scheduled_time(8)
    next_16h = next_scheduled_time(16)

    CHECK_INTERVAL = 300  # segundos entre verificações de alerta

    while True:
        now = datetime.now(BRAZIL_TZ)

        # Post das 8h
        if now >= next_8h:
            run_post("🌅 Post agendado das 08:00")
            next_8h = next_scheduled_time(8)

        # Post das 16h
        if now >= next_16h:
            run_post("🌆 Post agendado das 16:00")
            next_16h = next_scheduled_time(16)

        # Verificação de alerta
        check_alert()

        # Aguarda próximo ciclo (5 min)
        time.sleep(CHECK_INTERVAL)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_post("🔧 Post manual de teste")
