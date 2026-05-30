"""
Bot X - QueroApoiar Rankings
Posts semanais: candidatos (quarta 17h) e partidos (sexta 17h)
"""

import os, json, time, random, logging, requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import tweepy

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")

TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")
TWITTER_BEARER_TOKEN        = os.environ.get("TWITTER_BEARER_TOKEN", "")

DATA_DIR   = Path(__file__).parent / "data"
LOGS_DIR   = Path(__file__).parent / "logs"
CHARTS_DIR = Path(__file__).parent / "charts"
QA_LAST_POST_FILE = DATA_DIR / "qa_last_post.json"

for d in [DATA_DIR, LOGS_DIR, CHARTS_DIR]:
    d.mkdir(exist_ok=True)

MEDAL = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]

QA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

LEADER_PHRASES = [
    "segue liderando com folga!",
    "ainda na frente — e não para de crescer.",
    "mantém a liderança no ranking.",
    "continua no topo das doações.",
    "lidera com destaque.",
    "é o mais apoiado até agora.",
    "segura o 1º lugar.",
    "segue disparado na liderança.",
    "não abre mão do topo.",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("qa_bot")


# ─────────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────────

def load_json(path):
    try:
        return json.load(open(path, encoding="utf-8")) if path.exists() else None
    except: return None

def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def load_qa_last_post(): return load_json(QA_LAST_POST_FILE) or {}
def save_qa_last_post(d): save_json(QA_LAST_POST_FILE, d)


# ─────────────────────────────────────────────
# SCRAPING
# ─────────────────────────────────────────────

def scrape_queroapoiar_candidates():
    """Scraping de presidenciáveis do QueroApoiar."""
    import re
    from bs4 import BeautifulSoup
    try:
        resp = requests.get(
            "https://queroapoiar.com.br/campanhas/2026",
            headers=QA_HEADERS, timeout=20
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        candidates = []
        cards = soup.find_all(["article", "div"], class_=re.compile(r"card|campaign|candidat", re.I))
        if not cards:
            cards = soup.select("div[class*='card'], article")

        for card in cards:
            try:
                name_el = card.select_one("h2, h3, [class*='name'], [class*='nome'], [class*='title']")
                if not name_el: continue
                name = name_el.get_text(strip=True)
                if not name or len(name) < 3: continue

                cargo_el = card.select_one("[class*='cargo'], [class*='role'], [class*='position'], [class*='subtitle'], p")
                cargo = cargo_el.get_text(strip=True).lower() if cargo_el else ""
                if cargo and "president" not in cargo and "presid" not in cargo:
                    continue

                valor_el = card.select_one("[class*='total'], [class*='value'], [class*='amount'], [class*='arrecad']")
                valor_str = valor_el.get_text(strip=True) if valor_el else "R$ 0"
                valor_num = float(re.sub(r"[^0-9,]", "", valor_str).replace(",", ".") or 0)

                img_el = card.select_one("img")
                img_url = ""
                if img_el:
                    img_url = img_el.get("src") or img_el.get("data-src") or ""
                    if img_url.startswith("/"):
                        img_url = "https://queroapoiar.com.br" + img_url

                apoio_el = card.select_one("[class*='apoiad'], [class*='support'], [class*='backer']")
                apoio = apoio_el.get_text(strip=True) if apoio_el else ""

                candidates.append({
                    "name": name, "valor": valor_num, "valor_str": valor_str,
                    "img_url": img_url, "cargo": cargo, "apoio": apoio,
                })
            except Exception:
                continue

        candidates.sort(key=lambda x: x["valor"], reverse=True)
        log.info("Candidatos encontrados: %d", len(candidates))
        return candidates[:8] if candidates else None

    except Exception as e:
        log.exception("Erro scraping candidatos: %s", e)
        return None


def scrape_queroapoiar_parties():
    """Scraping de partidos do QueroApoiar."""
    import re
    from bs4 import BeautifulSoup
    try:
        resp = requests.get(
            "https://queroapoiar.com.br/partidos",
            headers=QA_HEADERS, timeout=20
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        parties = []
        cards = soup.find_all(["article", "div"], class_=re.compile(r"card|party|partido", re.I))
        if not cards:
            cards = soup.select("div[class*='card'], article")

        for card in cards:
            try:
                name_el = card.select_one("h2, h3, [class*='name'], [class*='nome']")
                if not name_el: continue
                name = name_el.get_text(strip=True)
                if not name or len(name) < 2: continue

                valor_el = card.select_one("[class*='total'], [class*='value'], [class*='amount'], [class*='arrecad']")
                valor_str = valor_el.get_text(strip=True) if valor_el else "R$ 0"
                valor_num = float(re.sub(r"[^0-9,]", "", valor_str).replace(",", ".") or 0)

                img_el = card.select_one("img")
                img_url = ""
                if img_el:
                    img_url = img_el.get("src") or img_el.get("data-src") or ""
                    if img_url.startswith("/"):
                        img_url = "https://queroapoiar.com.br" + img_url

                apoio_el = card.select_one("[class*='apoiad'], [class*='support'], [class*='backer'], [class*='campaign']")
                apoio = apoio_el.get_text(strip=True) if apoio_el else ""

                parties.append({
                    "name": name, "valor": valor_num, "valor_str": valor_str,
                    "img_url": img_url, "apoio": apoio,
                })
            except Exception:
                continue

        parties.sort(key=lambda x: x["valor"], reverse=True)
        log.info("Partidos encontrados: %d", len(parties))
        return parties[:8] if parties else None

    except Exception as e:
        log.exception("Erro scraping partidos: %s", e)
        return None


# ─────────────────────────────────────────────
# IMAGENS
# ─────────────────────────────────────────────

def download_image(url, size=(100, 100)):
    """Baixa imagem de URL e retorna array numpy."""
    try:
        from PIL import Image
        from io import BytesIO
        resp = requests.get(url, headers=QA_HEADERS, timeout=10)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        img = img.resize(size, Image.LANCZOS)
        return np.array(img)
    except Exception as e:
        log.warning("Erro download imagem %s: %s", url[:60], e)
        return None


def build_qa_chart(items, title, kind="candidates"):
    """Grade 4x2 com foto/logo, nome, valor arrecadado e apoiadores."""
    try:
        from matplotlib.offsetbox import OffsetImage, AnnotationBbox

        n = min(8, len(items))
        fig, axes = plt.subplots(2, 4, figsize=(14, 8))
        fig.patch.set_facecolor("#0D1117")
        plt.subplots_adjust(hspace=0.18, wspace=0.10, top=0.88, bottom=0.04, left=0.02, right=0.98)

        fig.text(0.5, 0.95, title, ha="center", color="white",
                 fontsize=14, fontweight="bold", transform=fig.transFigure)
        fig.text(0.5, 0.91,
                 f"Fonte: queroapoiar.com.br • {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y')}",
                 ha="center", color="#555555", fontsize=9, transform=fig.transFigure)

        for idx in range(8):
            row, col = divmod(idx, 4)
            ax = axes[row][col]
            ax.set_facecolor("#161B22")
            ax.set_xlim(0, 1); ax.set_ylim(0, 1)
            ax.axis("off")

            # Borda do card
            rect = mpatches.FancyBboxPatch(
                (0.03, 0.03), 0.94, 0.94,
                boxstyle="round,pad=0.02", linewidth=1.2,
                edgecolor="#30363D", facecolor="#161B22",
                transform=ax.transAxes, zorder=0
            )
            ax.add_patch(rect)

            if idx >= n:
                continue

            item = items[idx]
            pos  = idx + 1

            # Número de posição
            pos_color = "#FFD700" if pos == 1 else ("#C0C0C0" if pos == 2 else ("#CD7F32" if pos == 3 else "#8899A6"))
            ax.text(0.08, 0.91, f"{pos}º", transform=ax.transAxes,
                    color=pos_color, fontsize=11, fontweight="bold", va="top", ha="left")

            # Foto ou logo
            img_arr = download_image(item["img_url"]) if item.get("img_url") else None
            if img_arr is not None:
                imagebox = OffsetImage(img_arr, zoom=0.55)
                ab = AnnotationBbox(imagebox, (0.5, 0.60),
                                    frameon=False, xycoords="axes fraction")
                ax.add_artist(ab)
            else:
                # Placeholder com inicial
                circle = plt.Circle((0.5, 0.60), 0.20, color="#2D3741",
                                    transform=ax.transAxes, zorder=2)
                ax.add_patch(circle)
                ax.text(0.5, 0.60, item["name"][0].upper(), transform=ax.transAxes,
                        color="#8899A6", fontsize=20, fontweight="bold",
                        ha="center", va="center", zorder=3)

            # Nome
            name_display = item["name"]
            if len(name_display) > 15: name_display = name_display[:13] + "…"
            ax.text(0.5, 0.32, name_display, transform=ax.transAxes,
                    color="white", fontsize=9, fontweight="bold",
                    ha="center", va="center")

            # Valor arrecadado
            ax.text(0.5, 0.19, item["valor_str"], transform=ax.transAxes,
                    color="#00BA7C", fontsize=8.5, fontweight="bold",
                    ha="center", va="center")

            # Apoiadores
            if item.get("apoio"):
                apoio_str = item["apoio"]
                if len(apoio_str) > 24: apoio_str = apoio_str[:22] + "…"
                ax.text(0.5, 0.08, apoio_str, transform=ax.transAxes,
                        color="#8899A6", fontsize=7,
                        ha="center", va="center")

        path = CHARTS_DIR / f"qa_{kind}_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(path, dpi=140, bbox_inches="tight", facecolor="#0D1117")
        plt.close()
        log.info("Chart salvo: %s", path)
        return path

    except Exception as e:
        log.exception("Erro build_qa_chart: %s", e)
        return None


# ─────────────────────────────────────────────
# TEXTOS DOS TWEETS
# ─────────────────────────────────────────────

def pick_leader(): return random.choice(LEADER_PHRASES)


def build_candidates_tweet(items):
    if not items: return None
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    top4    = items[:4]
    leader  = top4[0]
    lines   = [
        "💰 Ranking de Arrecadação — Presidenciáveis",
        f"📊 QueroApoiar — {now_str}",
        "",
        f"🥇 {leader['name']} {pick_leader()}",
        "",
    ]
    for i, item in enumerate(top4[1:], 1):
        lines.append(f"{MEDAL[i]} {item['name']:<16} | {item['valor_str']}")
    lines += ["", "🔗 queroapoiar.com.br", "#Eleicoes2026 #Brasil #QueroApoiar"]
    return "\n".join(lines)


def build_parties_tweet(items):
    if not items: return None
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    top4    = items[:4]
    leader  = top4[0]
    lines   = [
        "🏛️ Ranking de Arrecadação — Partidos",
        f"📊 QueroApoiar — {now_str}",
        "",
        f"🥇 {leader['name']} {pick_leader()}",
        "",
    ]
    for i, item in enumerate(top4[1:], 1):
        lines.append(f"{MEDAL[i]} {item['name']:<16} | {item['valor_str']}")
    lines += ["", "🔗 queroapoiar.com.br", "#Eleicoes2026 #Brasil #QueroApoiar"]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# TWITTER / X
# ─────────────────────────────────────────────

def get_client():
    return tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY, consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN, access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )

def get_api_v1():
    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_SECRET,
        TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET
    )
    return tweepy.API(auth)


def post_tweet(text, chart_path=None, retries=5, retry_delay=240):
    for attempt in range(1, retries + 1):
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
            tid  = resp.data["id"]
            log.info("Tweet postado! ID: %s", tid)
            return tid

        except Exception as e:
            log.warning("Tentativa %d/%d falhou: %s", attempt, retries, e)
            if attempt < retries: time.sleep(retry_delay)

    log.error("Todas as tentativas falharam.")
    return None


# ─────────────────────────────────────────────
# POSTS PRINCIPAIS
# ─────────────────────────────────────────────

def run_qa_candidates_post():
    log.info("Postando ranking de candidatos QueroApoiar")
    items = scrape_queroapoiar_candidates()
    if not items:
        log.error("Sem dados de candidatos.")
        return
    text  = build_candidates_tweet(items)
    chart = build_qa_chart(items, "Ranking Presidenciáveis — QueroApoiar", "candidates")
    tid   = post_tweet(text, chart)
    if tid:
        d = load_qa_last_post()
        d["candidates"] = {"tweet_id": tid, "date": datetime.now(BRAZIL_TZ).date().isoformat()}
        save_qa_last_post(d)
        log.info("Post candidatos OK: %s", tid)


def run_qa_parties_post():
    log.info("Postando ranking de partidos QueroApoiar")
    items = scrape_queroapoiar_parties()
    if not items:
        log.error("Sem dados de partidos.")
        return
    text  = build_parties_tweet(items)
    chart = build_qa_chart(items, "Ranking Partidos — QueroApoiar", "parties")
    tid   = post_tweet(text, chart)
    if tid:
        d = load_qa_last_post()
        d["parties"] = {"tweet_id": tid, "date": datetime.now(BRAZIL_TZ).date().isoformat()}
        save_qa_last_post(d)
        log.info("Post partidos OK: %s", tid)


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

def run_scheduler():
    log.info("Bot iniciado — posts QueroApoiar: quarta 17h (candidatos) | sexta 17h (partidos)")
    qa_done = {"candidates": None, "parties": None}

    while True:
        now     = datetime.now(BRAZIL_TZ)
        weekday = now.weekday()  # 0=seg … 4=sex
        today   = now.date().isoformat()

        # Quarta às 17h — candidatos presidenciáveis
        if weekday == 2 and now.hour == 17 and now.minute < 5:
            if qa_done["candidates"] != today:
                run_qa_candidates_post()
                qa_done["candidates"] = today

        # Sexta às 17h — partidos
        if weekday == 4 and now.hour == 17 and now.minute < 5:
            if qa_done["parties"] != today:
                run_qa_parties_post()
                qa_done["parties"] = today

        time.sleep(300)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    elif len(sys.argv) > 1 and sys.argv[1] == "--debug":
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get("https://queroapoiar.com.br/partidos", headers=QA_HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")
        # Mostra todas as classes únicas encontradas
        tags = soup.find_all(True)
        classes = set()
        for tag in tags:
            for c in tag.get("class", []):
                classes.add(c)
        print("CLASSES ENCONTRADAS:")
        for c in sorted(classes)[:80]:
            print(c)
        print("\nHTML (primeiros 3000 chars):")
        print(resp.text[:3000])
    if len(sys.argv) > 1 and sys.argv[1] == "--test-candidates":
        run_qa_candidates_post()
    elif len(sys.argv) > 1 and sys.argv[1] == "--test-parties":
        run_qa_parties_post()
    else:
        run_scheduler()
