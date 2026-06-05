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
    except:
        return None

def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def load_qa_last_post(): return load_json(QA_LAST_POST_FILE) or {}
def save_qa_last_post(d): save_json(QA_LAST_POST_FILE, d)


# ─────────────────────────────────────────────
# DEBUG — inspeciona HTML do site
# ─────────────────────────────────────────────

def debug_site(url):
    from bs4 import BeautifulSoup
    resp = requests.get(url, headers=QA_HEADERS, timeout=20)
    print(f"Status: {resp.status_code}")
    soup = BeautifulSoup(resp.text, "lxml")
    tags = soup.find_all(True)
    classes = set()
    for tag in tags:
        for c in tag.get("class", []):
            classes.add(c)
    print("\nCLASSES ENCONTRADAS:")
    for c in sorted(classes)[:100]:
        print(" ", c)
    print("\nHTML (primeiros 4000 chars):")
    print(resp.text[:4000])


# ─────────────────────────────────────────────
# SCRAPING
# ─────────────────────────────────────────────

def scrape_queroapoiar_candidates():
    """Busca presidenciáveis via API do QueroApoiar."""
    try:
        # Tenta endpoint de campanhas 2026
        for endpoint in [
            "https://api.queroapoiar.com.br/api/stats/campanhas/exec?ano=2026",
            "https://api.queroapoiar.com.br/api/stats/candidatos/exec",
            "https://api.queroapoiar.com.br/api/stats/campanhas/exec",
        ]:
            try:
                resp = requests.get(endpoint, headers=QA_HEADERS, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    log.info("API candidatos OK: %s — keys: %s", endpoint, list(data.keys())[:5])
                    
                    # Tenta extrair lista de candidatos
                    items = (data.get("candidatos") or data.get("campanhas") or 
                             data.get("data") or data.get("results") or [])
                    
                    if items:
                        candidates = []
                        for item in items:
                            cargo = str(item.get("cargo", "")).lower()
                            # Filtra presidenciáveis
                            if cargo and "president" not in cargo and "presid" not in cargo:
                                continue
                            nome = item.get("nome") or item.get("name") or ""
                            total = float(item.get("total") or item.get("arrecadado") or 0)
                            apoiadores = item.get("apoiadores") or item.get("donors") or 0
                            img_url = item.get("foto") or item.get("image") or item.get("avatar") or ""
                            if img_url and img_url.startswith("/"):
                                img_url = "https://queroapoiar.com.br" + img_url
                            candidates.append({
                                "name": nome,
                                "valor": total,
                                "valor_str": f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                                "img_url": img_url,
                                "apoio": f"{apoiadores} apoiadores",
                            })
                        if candidates:
                            candidates.sort(key=lambda x: x["valor"], reverse=True)
                            log.info("Presidenciáveis: %d", len(candidates))
                            return candidates[:8]
            except Exception as e:
                log.warning("Endpoint %s falhou: %s", endpoint, e)
                continue

        log.error("Nenhum endpoint de candidatos funcionou.")
        return None
    except Exception as e:
        log.exception("Erro scraping candidatos: %s", e)
        return None


def scrape_queroapoiar_parties():
    """Busca partidos via API do QueroApoiar."""
    try:
        url = "https://api.queroapoiar.com.br/api/stats/partidos/exec"
        resp = requests.get(url, headers=QA_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        raw = data.get("partidos") or data.get("data") or []
        if not raw:
            log.error("API partidos sem dados.")
            return None

        parties = []
        for item in raw:
            nome  = item.get("nome") or item.get("name") or ""
            total = float(item.get("total") or 0)
            camp  = item.get("campanhas") or 0
            apoio = item.get("apoiadores") or 0

            # Logo: mapa de slugs baseado nos arquivos vistos na aba network do site
            LOGO_MAP = {
                "Missão": "missao", "Novo": "novo", "PSOL": "psol", "Psol": "psol",
                "PCdoB": "pcdob", "PT": "pt", "PSD": "psd", "PL": "pl",
                "Podemos": "podemos", "UP": "up", "PP": "pp",
                "Solidariedade": "solidariedade", "PSB": "psb", "PCB": "pcb",
                "Mobiliza": "mobiliza", "PDT": "pdt", "MDB": "mdb", "DC": "dc",
                "Republicanos": "republicanos", "PSDB": "psdb", "PV": "pv",
                "Rede": "rede", "Agir": "agir", "PCO": "pco",
                "União Brasil": "uniao-brasil", "Democrata": "democrata",
                "Avante": "avante", "PRD": "prd", "Cidadania": "cidadania",
            }
            slug = LOGO_MAP.get(nome, nome.lower().replace(" ", "-"))
            img_url = f"https://queroapoiar.com.br/assets/partidos/{slug}.webp"

            valor_str = f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

            parties.append({
                "name": nome,
                "valor": total,
                "valor_str": valor_str,
                "img_url": img_url,
                "apoio": f"{camp} campanhas • {apoio} apoiadores",
            })

        parties.sort(key=lambda x: x["valor"], reverse=True)
        log.info("Partidos: %d", len(parties))
        return parties[:8]

    except Exception as e:
        log.exception("Erro scraping partidos: %s", e)
        return None

# ─────────────────────────────────────────────
# IMAGENS
# ─────────────────────────────────────────────

def download_image(url, size=(100, 100)):
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
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis("off")

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
            pos_color = "#FFD700" if pos == 1 else ("#C0C0C0" if pos == 2 else ("#CD7F32" if pos == 3 else "#8899A6"))
            ax.text(0.08, 0.91, f"{pos}º", transform=ax.transAxes,
                    color=pos_color, fontsize=11, fontweight="bold", va="top", ha="left")

            img_arr = download_image(item["img_url"]) if item.get("img_url") else None
            if img_arr is not None:
                imagebox = OffsetImage(img_arr, zoom=0.55)
                ab = AnnotationBbox(imagebox, (0.5, 0.60),
                                    frameon=False, xycoords="axes fraction")
                ax.add_artist(ab)
            else:
                circle = plt.Circle((0.5, 0.60), 0.20, color="#2D3741",
                                    transform=ax.transAxes, zorder=2)
                ax.add_patch(circle)
                ax.text(0.5, 0.60, item["name"][0].upper(), transform=ax.transAxes,
                        color="#8899A6", fontsize=20, fontweight="bold",
                        ha="center", va="center", zorder=3)

            name_display = item["name"]
            if len(name_display) > 15:
                name_display = name_display[:13] + "…"
            ax.text(0.5, 0.32, name_display, transform=ax.transAxes,
                    color="white", fontsize=9, fontweight="bold",
                    ha="center", va="center")

            ax.text(0.5, 0.19, item["valor_str"], transform=ax.transAxes,
                    color="#00BA7C", fontsize=8.5, fontweight="bold",
                    ha="center", va="center")

            if item.get("apoio"):
                apoio_str = item["apoio"]
                if len(apoio_str) > 24:
                    apoio_str = apoio_str[:22] + "…"
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
        f"    {leader['valor_str']}",
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
        f"    {leader['valor_str']}",
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
            if media_id:
                kwargs["media_ids"] = [media_id]
            resp = get_client().create_tweet(**kwargs)
            tid  = resp.data["id"]
            log.info("Tweet postado! ID: %s", tid)
            return tid

        except Exception as e:
            log.warning("Tentativa %d/%d falhou: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(retry_delay)

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

# ─────────────────────────────────────────────
# MARCOS E ULTRAPASSAGENS
# ─────────────────────────────────────────────

MILESTONES_FILE   = DATA_DIR / "milestones.json"
LAST_ALERT_FILE   = DATA_DIR / "last_alert.json"
RANKING_FILE      = DATA_DIR / "last_ranking.json"
REPOST_QUEUE_FILE = DATA_DIR / "repost_queue.json"

MARCOS_ARRECADACAO_PARTIDO = [
    50_000, 100_000, 150_000, 200_000, 250_000, 300_000, 350_000, 400_000,
    450_000, 500_000, 600_000, 700_000, 750_000, 800_000, 900_000,
    1_000_000, 1_250_000, 1_500_000, 1_750_000, 2_000_000, 2_500_000,
    3_000_000, 4_000_000, 5_000_000, 7_500_000, 10_000_000,
]

MARCOS_ARRECADACAO_CANDIDATO = [
    25_000, 50_000, 75_000, 100_000, 150_000, 200_000, 250_000, 300_000,
    350_000, 400_000, 450_000, 500_000, 600_000, 700_000, 750_000,
    800_000, 900_000, 1_000_000, 1_250_000, 1_500_000, 2_000_000,
    2_500_000, 3_000_000, 5_000_000,
]

MARCOS_APOIADORES_PARTIDO = [
    500, 1_000, 1_500, 2_000, 3_000, 4_000, 5_000, 6_000, 7_000, 8_000,
    9_000, 10_000, 12_000, 15_000, 20_000, 25_000, 30_000, 40_000, 50_000,
]

MARCOS_APOIADORES_CANDIDATO = [
    250, 500, 750, 1_000, 1_500, 2_000, 2_500, 3_000, 4_000, 5_000,
    6_000, 7_000, 8_000, 10_000, 12_000, 15_000, 20_000, 25_000,
]

MISSAO_PARTIDO   = "Missão"
MISSAO_CANDIDATO = "Renan Santos"
MIN_ALERT_SECS   = 3 * 3600


def fmt_valor(v):
    s = f"{v:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_marco(v):
    if v >= 1_000_000:
        n = v / 1_000_000
        s = f"{n:.1f}".replace(".", ",")
        return f"R$ {s} milhao" if n != int(n) else f"R$ {int(n)} milhao"
    if v >= 1_000:
        return f"R$ {int(v/1_000)} mil"
    return fmt_valor(v)

def fmt_apoio_marco(v):
    if v >= 1_000:
        return f"{int(v/1_000)} mil apoiadores"
    return f"{v} apoiadores"


def load_milestones():    return load_json(MILESTONES_FILE) or {}
def save_milestones(d):   save_json(MILESTONES_FILE, d)
def load_last_alert():    return load_json(LAST_ALERT_FILE) or {}
def save_last_alert(d):   save_json(LAST_ALERT_FILE, d)
def load_qa_ranking():    return load_json(RANKING_FILE) or {}
def save_qa_ranking(d):   save_json(RANKING_FILE, d)
def load_repost_queue():  return load_json(REPOST_QUEUE_FILE) or []
def save_repost_queue(d): save_json(REPOST_QUEUE_FILE, d)


def can_post_alert():
    last = load_last_alert()
    if not last.get("timestamp"):
        return True
    elapsed = (datetime.now(BRAZIL_TZ) - datetime.fromisoformat(last["timestamp"])).total_seconds()
    return elapsed >= MIN_ALERT_SECS


def register_alert(text):
    now = datetime.now(BRAZIL_TZ)
    save_last_alert({"timestamp": now.isoformat()})
    if 0 <= now.hour < 6:
        queue = load_repost_queue()
        queue.append({"text": text, "queued_at": now.isoformat(), "reposted": False})
        save_repost_queue(queue)
        log.info("Post enfileirado para repost matinal.")


MARCO_PHRASES = [
    "acaba de ultrapassar",
    "acabou de cruzar a marca de",
    "superou a barreira de",
    "atingiu o marco de",
    "chegou em",
]
APOIO_PHRASES = ["conquistou seu", "chegou a", "atingiu a marca de", "alcancou"]
OVER_PHRASES  = ["ultrapassou", "passou a frente de", "superou", "ficou a frente de"]


def build_marco_arrecad(name, valor, marco, kind="partido"):
    emoji = "ROCKET" if kind == "partido" else "STAR"
    emj   = chr(0x1F680) if kind == "partido" else chr(0x2B50)
    tag   = name.replace(" ", "")
    ph    = random.choice(MARCO_PHRASES)
    lines = [
        f"{emj} MARCO HISTORICO — {name}!",
        "",
        f"{name} {ph}",
        f"{fmt_marco(marco)} em arrecadacao no QueroApoiar!",
        "",
        f"💰 Total arrecadado: {fmt_valor(valor)}",
        "",
        "Apoie tambem: queroapoiar.com.br",
        f"#Eleicoes2026 #{tag} #QueroApoiar",
    ]
    return "\n".join(lines)


def build_marco_apoio(name, apoiadores, marco, kind="partido"):
    emj   = "🎉" if kind == "partido" else "⭐"
    tag   = name.replace(" ", "")
    ph    = random.choice(APOIO_PHRASES)
    num   = f"{marco:,}".replace(",", ".")
    lines = [
        f"{emj} MARCO DE APOIADORES — {name}!",
        "",
        f"{name} {ph}",
        f"{num} apoiadores no QueroApoiar!",
        "",
        f"👥 Apoiadores: {apoiadores:,}".replace(",", "."),
        "",
        "Apoie tambem: queroapoiar.com.br",
        f"#Eleicoes2026 #{tag} #QueroApoiar",
    ]
    return "\n".join(lines)


def build_overtake(winner, loser, wval, lval, wpos, lpos, kind="partido"):
    header = "📊 MUDANCA NO RANKING DE PARTIDOS!" if kind == "partido" else "🔄 VIRADA NO RANKING DE PRESIDENCIAVEIS!"
    ph     = random.choice(OVER_PHRASES)
    mw     = MEDAL[wpos-1] if wpos <= 8 else f"{wpos}o"
    ml     = MEDAL[lpos-1] if lpos <= 8 else f"{lpos}o"
    lines  = [
        header, "",
        f"{winner} {ph} {loser}",
        "no ranking do QueroApoiar!", "",
        f"{mw} {winner:<16} | {fmt_valor(wval)} ▲",
        f"{ml} {loser:<16} | {fmt_valor(lval)} ▼",
        "",
        "🔗 queroapoiar.com.br",
        "#Eleicoes2026 #Brasil #QueroApoiar",
    ]
    return "\n".join(lines)


def build_repost(original):
    return "🌅 Bom dia! Caso tenha perdido:\n\n" + original


def parse_apoiadores(apoio_str):
    import re
    nums = re.findall(r"[0-9]+", apoio_str.replace(".", ""))
    return int(nums[-1]) if nums else 0


def check_marcos_and_overtakes():
    if not can_post_alert():
        log.info("Aguardando 3h entre alertas.")
        return

    milestones = load_milestones()
    posted     = False

    parties    = scrape_queroapoiar_parties()
    candidates = scrape_queroapoiar_candidates()

    # ── Marcos Missão partido ───────────────────────────────────
    if parties and not posted:
        mp = next((p for p in parties if p["name"] == MISSAO_PARTIDO), None)
        if mp:
            val   = mp["valor"]
            apoio = parse_apoiadores(mp.get("apoio", "0"))

            key = "missao_p_arrecad"
            last_m = milestones.get(key, 0)
            next_m = next((m for m in MARCOS_ARRECADACAO_PARTIDO if m > last_m and val >= m), None)
            if next_m:
                text = build_marco_arrecad(MISSAO_PARTIDO, val, next_m, "partido")
                tid  = post_tweet(text)
                if tid:
                    milestones[key] = next_m
                    save_milestones(milestones)
                    register_alert(text)
                    log.info("Marco arrecadacao Missao partido: %s", fmt_marco(next_m))
                    posted = True

            if not posted and apoio > 0:
                key2   = "missao_p_apoio"
                last_a = milestones.get(key2, 0)
                next_a = next((m for m in MARCOS_APOIADORES_PARTIDO if m > last_a and apoio >= m), None)
                if next_a:
                    text = build_marco_apoio(MISSAO_PARTIDO, apoio, next_a, "partido")
                    tid  = post_tweet(text)
                    if tid:
                        milestones[key2] = next_a
                        save_milestones(milestones)
                        register_alert(text)
                        log.info("Marco apoiadores Missao partido: %d", next_a)
                        posted = True

    # ── Marcos Renan Santos ─────────────────────────────────────
    if candidates and not posted:
        rc = next((c for c in candidates if c["name"] == MISSAO_CANDIDATO), None)
        if rc:
            val   = rc["valor"]
            apoio = parse_apoiadores(rc.get("apoio", "0"))

            key = "renan_arrecad"
            last_m = milestones.get(key, 0)
            next_m = next((m for m in MARCOS_ARRECADACAO_CANDIDATO if m > last_m and val >= m), None)
            if next_m:
                text = build_marco_arrecad(MISSAO_CANDIDATO, val, next_m, "candidato")
                tid  = post_tweet(text)
                if tid:
                    milestones[key] = next_m
                    save_milestones(milestones)
                    register_alert(text)
                    log.info("Marco arrecadacao Renan: %s", fmt_marco(next_m))
                    posted = True

            if not posted and apoio > 0:
                key2   = "renan_apoio"
                last_a = milestones.get(key2, 0)
                next_a = next((m for m in MARCOS_APOIADORES_CANDIDATO if m > last_a and apoio >= m), None)
                if next_a:
                    text = build_marco_apoio(MISSAO_CANDIDATO, apoio, next_a, "candidato")
                    tid  = post_tweet(text)
                    if tid:
                        milestones[key2] = next_a
                        save_milestones(milestones)
                        register_alert(text)
                        log.info("Marco apoiadores Renan: %d", next_a)
                        posted = True

    # ── Ultrapassagens partidos ─────────────────────────────────
    if parties and not posted:
        new_r = {p["name"]: {"pos": i+1, "val": p["valor"]} for i, p in enumerate(parties)}
        old_r = load_qa_ranking().get("parties", {})
        if old_r:
            for name, info in new_r.items():
                old_info = old_r.get(name)
                if old_info and info["pos"] < old_info["pos"]:
                    loser = next(
                        (n for n, oi in old_r.items()
                         if oi["pos"] == info["pos"] and new_r.get(n, {}).get("pos", 99) > info["pos"]),
                        None
                    )
                    if loser and loser in new_r:
                        text = build_overtake(name, loser, info["val"], new_r[loser]["val"],
                                              info["pos"], new_r[loser]["pos"], "partido")
                        tid = post_tweet(text)
                        if tid:
                            register_alert(text)
                            log.info("Ultrapassagem partidos: %s > %s", name, loser)
                            posted = True
                            break
        save_qa_ranking({**load_qa_ranking(), "parties": new_r})

    # ── Ultrapassagens candidatos ───────────────────────────────
    if candidates and not posted:
        new_r = {c["name"]: {"pos": i+1, "val": c["valor"]} for i, c in enumerate(candidates)}
        old_r = load_qa_ranking().get("candidates", {})
        if old_r:
            for name, info in new_r.items():
                old_info = old_r.get(name)
                if old_info and info["pos"] < old_info["pos"]:
                    loser = next(
                        (n for n, oi in old_r.items()
                         if oi["pos"] == info["pos"] and new_r.get(n, {}).get("pos", 99) > info["pos"]),
                        None
                    )
                    if loser and loser in new_r:
                        text = build_overtake(name, loser, info["val"], new_r[loser]["val"],
                                              info["pos"], new_r[loser]["pos"], "candidato")
                        tid = post_tweet(text)
                        if tid:
                            register_alert(text)
                            log.info("Ultrapassagem candidatos: %s > %s", name, loser)
                            posted = True
                            break
        save_qa_ranking({**load_qa_ranking(), "candidates": new_r})


def check_repost_queue():
    now = datetime.now(BRAZIL_TZ)
    if now.hour != 8 or now.minute >= 10:
        return
    queue   = load_repost_queue()
    pending = [q for q in queue if not q.get("reposted")]
    if not pending:
        return
    for item in pending:
        if can_post_alert():
            text = build_repost(item["text"])
            tid  = post_tweet(text)
            if tid:
                item["reposted"] = True
                register_alert(text)
                log.info("Repost matinal realizado.")
            break
    save_repost_queue(queue)


def run_scheduler():
    log.info("Bot iniciado — posts QueroApoiar: alternado 18h | dias pares=partidos, dias impares=candidatos")
    qa_done = {"post": None}

    while True:
        now   = datetime.now(BRAZIL_TZ)
        today = now.date().isoformat()

        if now.hour == 18 and now.minute < 5:
            if qa_done["post"] != today:
                # Alterna: dias pares = partidos, dias impares = candidatos
                if now.day % 2 == 0:
                    log.info("Dia par — postando partidos")
                    run_qa_parties_post()
                else:
                    log.info("Dia impar — postando candidatos")
                    run_qa_candidates_post()
                qa_done["post"] = today

        # Verifica marcos e ultrapassagens a cada ciclo
        check_repost_queue()
        check_marcos_and_overtakes()

        time.sleep(300)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--debug-parties":
        debug_site("https://queroapoiar.com.br/partidos")
    elif len(sys.argv) > 1 and sys.argv[1] == "--debug-candidates":
        debug_site("https://queroapoiar.com.br/campanhas/2026")
    elif len(sys.argv) > 1 and sys.argv[1] == "--test-candidates":
        run_qa_candidates_post()
    elif len(sys.argv) > 1 and sys.argv[1] == "--test-parties":
        run_qa_parties_post()
    else:
        run_scheduler()
