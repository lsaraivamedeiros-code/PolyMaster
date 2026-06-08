"""
Bot X - QueroApoiar Rankings
Posts diarios alternados + marcos + ultrapassagens
"""

import os, json, time, random, logging, requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
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

DATA_DIR   = Path(__file__).parent / "data"
LOGS_DIR   = Path(__file__).parent / "logs"
CHARTS_DIR = Path(__file__).parent / "charts"

for d in [DATA_DIR, LOGS_DIR, CHARTS_DIR]:
    d.mkdir(exist_ok=True)

DAILY_POST_FILE   = DATA_DIR / "daily_post.json"
MILESTONES_FILE   = DATA_DIR / "milestones.json"
LAST_ALERT_FILE   = DATA_DIR / "last_alert.json"
ALERT_COUNT_FILE  = DATA_DIR / "alert_count.json"
RANKING_FILE      = DATA_DIR / "last_ranking.json"
REPOST_QUEUE_FILE = DATA_DIR / "repost_queue.json"

MEDAL = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]

QA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

MISSAO_PARTIDO   = "Missão"
MISSAO_CANDIDATO = "Renan Santos"
MIN_ALERT_SECS   = 60 * 60       # 60 minutos
MAX_ALERTS_DAY   = 4

MARCOS_ARRECAD_PARTIDO = [
    50_000, 100_000, 150_000, 200_000, 250_000, 300_000, 350_000, 400_000,
    450_000, 500_000, 600_000, 700_000, 750_000, 800_000, 900_000,
    1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000, 1_500_000,
    1_750_000, 2_000_000, 2_500_000, 3_000_000, 4_000_000, 5_000_000,
    7_500_000, 10_000_000, 15_000_000, 20_000_000,
]
MARCOS_ARRECAD_CAND = [
    25_000, 50_000, 75_000, 100_000, 125_000, 150_000, 175_000, 200_000,
    250_000, 300_000, 350_000, 400_000, 450_000, 500_000, 600_000, 700_000,
    750_000, 800_000, 900_000, 1_000_000, 1_250_000, 1_500_000, 2_000_000,
    2_500_000, 3_000_000, 5_000_000,
]
MARCOS_APOIO_PARTIDO = [
    500, 1_000, 1_500, 2_000, 3_000, 4_000, 5_000, 6_000, 7_000, 8_000,
    9_000, 10_000, 12_000, 15_000, 20_000, 25_000, 30_000, 40_000, 50_000,
]
MARCOS_APOIO_CAND = [
    250, 500, 750, 1_000, 1_500, 2_000, 2_500, 3_000, 4_000, 5_000,
    6_000, 7_000, 8_000, 10_000, 12_000, 15_000, 20_000, 25_000,
]

LEADER_PHRASES = [
    "segue liderando com folga!",
    "ainda na frente — e não para de crescer.",
    "mantém a liderança.",
    "continua no topo.",
    "lidera com destaque.",
    "é o mais apoiado.",
    "segura o 1º lugar.",
    "segue disparado na liderança.",
    "não abre mão do topo.",
]
MARCO_PHRASES = [
    "acaba de ultrapassar",
    "acabou de cruzar a marca de",
    "superou a barreira de",
    "atingiu o marco de",
    "chegou em",
]
APOIO_PHRASES = [
    "conquistou seu",
    "chegou a",
    "atingiu a marca de",
    "alcançou",
]
OVER_PHRASES = [
    "ultrapassou",
    "passou à frente de",
    "superou",
    "ficou à frente de",
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

def load_daily():         return load_json(DAILY_POST_FILE) or {}
def save_daily(d):        save_json(DAILY_POST_FILE, d)
def load_milestones():    return load_json(MILESTONES_FILE) or {}
def save_milestones(d):   save_json(MILESTONES_FILE, d)
def load_last_alert():    return load_json(LAST_ALERT_FILE) or {}
def save_last_alert(d):   save_json(LAST_ALERT_FILE, d)
def load_alert_count():   return load_json(ALERT_COUNT_FILE) or {}
def save_alert_count(d):  save_json(ALERT_COUNT_FILE, d)
def load_ranking():       return load_json(RANKING_FILE) or {}
def save_ranking(d):      save_json(RANKING_FILE, d)
def load_repost_queue():  return load_json(REPOST_QUEUE_FILE) or []
def save_repost_queue(d): save_json(REPOST_QUEUE_FILE, d)


def fmt_valor(v):
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def fmt_marco(v):
    if v >= 1_000_000:
        n = v / 1_000_000
        s = f"{n:.2f}".rstrip("0").rstrip(",").replace(".", ",")
        return f"R$ {s} milhão" if n != int(n) else f"R$ {int(n)} milhão"
    if v >= 1_000:
        return f"R$ {int(v/1_000)} mil"
    return fmt_valor(v)

def fmt_delta(delta):
    if delta is None or delta == 0: return "↔ sem variação"
    if delta > 0: return f"▲ +{fmt_valor(delta)}"
    return f"▼ -{fmt_valor(abs(delta))}"

def fmt_delta_apoio(delta):
    if delta is None or delta == 0: return "↔ sem variação"
    if delta > 0: return f"▲ +{int(delta):,} apoiadores".replace(",",".")
    return f"▼ -{int(abs(delta)):,} apoiadores".replace(",",".")

def parse_apoiadores(apoio_str):
    import re
    nums = re.findall(r"[0-9]+", str(apoio_str).replace(".", ""))
    return int(nums[-1]) if nums else 0

def can_post_alert():
    today = datetime.now(BRAZIL_TZ).date().isoformat()
    cnt   = load_alert_count()
    if cnt.get("date") != today:
        return True
    if cnt.get("count", 0) >= MAX_ALERTS_DAY:
        log.info("Limite de %d alertas/dia atingido.", MAX_ALERTS_DAY)
        return False
    last = load_last_alert()
    if not last.get("timestamp"):
        return True
    elapsed = (datetime.now(BRAZIL_TZ) - datetime.fromisoformat(last["timestamp"])).total_seconds()
    if elapsed < MIN_ALERT_SECS:
        log.info("Aguardando intervalo de 60min. Faltam %.0f min.", (MIN_ALERT_SECS - elapsed)/60)
        return False
    return True

def register_alert(text):
    now   = datetime.now(BRAZIL_TZ)
    today = now.date().isoformat()
    save_last_alert({"timestamp": now.isoformat()})
    cnt = load_alert_count()
    if cnt.get("date") != today:
        cnt = {"date": today, "count": 0}
    cnt["count"] = cnt.get("count", 0) + 1
    save_alert_count(cnt)
    if 0 <= now.hour < 6:
        queue = load_repost_queue()
        queue.append({"text": text, "queued_at": now.isoformat(), "reposted": False})
        save_repost_queue(queue)
        log.info("Post enfileirado para repost matinal.")


# ─────────────────────────────────────────────
# SCRAPING via API
# ─────────────────────────────────────────────

def scrape_parties():
    import re
    try:
        resp = requests.get(
            "https://api.queroapoiar.com.br/api/stats/partidos/exec",
            headers=QA_HEADERS, timeout=15
        )
        resp.raise_for_status()
        raw = resp.json().get("partidos") or []
        parties = []
        for item in raw:
            nome  = item.get("nome", "")
            total = float(item.get("total") or 0)
            camp  = item.get("campanhas") or 0
            apoio = item.get("apoiadores") or 0
            slug  = {
                "Missão":"missao","Novo":"novo","PSOL":"psol","Psol":"psol",
                "PCdoB":"pcdob","PT":"pt","PSD":"psd","PL":"pl",
                "Podemos":"podemos","UP":"up","PP":"pp","Solidariedade":"solidariedade",
                "PSB":"psb","PCB":"pcb","Mobiliza":"mobiliza","PDT":"pdt",
                "MDB":"mdb","DC":"dc","Republicanos":"republicanos","PSDB":"psdb",
                "PV":"pv","Rede":"rede","Agir":"agir","PCO":"pco",
                "União Brasil":"uniao-brasil","Democrata":"democrata",
                "Avante":"avante","PRD":"prd","Cidadania":"cidadania",
            }.get(nome, nome.lower().replace(" ","-"))
            parties.append({
                "name": nome, "valor": total,
                "valor_str": fmt_valor(total),
                "img_url": f"https://queroapoiar.com.br/assets/partidos/{slug}.webp",
                "apoio": apoio, "campanhas": camp,
            })
        parties.sort(key=lambda x: x["valor"], reverse=True)
        log.info("Partidos: %d", len(parties))
        return parties[:8] if parties else None
    except Exception as e:
        log.exception("Erro partidos: %s", e); return None


def scrape_candidates():
    try:
        for url in [
            "https://api.queroapoiar.com.br/api/stats/campanhas/exec?ano=2026",
            "https://api.queroapoiar.com.br/api/stats/candidatos/exec",
            "https://api.queroapoiar.com.br/api/stats/campanhas/exec",
        ]:
            try:
                resp = requests.get(url, headers=QA_HEADERS, timeout=15)
                if resp.status_code != 200: continue
                data  = resp.json()
                items = data.get("candidatos") or data.get("campanhas") or data.get("data") or []
                if not items: continue
                cands = []
                for item in items:
                    cargo = str(item.get("cargo","")).lower()
                    if cargo and "president" not in cargo and "presid" not in cargo:
                        continue
                    nome  = item.get("nome") or item.get("name") or ""
                    total = float(item.get("total") or item.get("arrecadado") or 0)
                    apoio = int(item.get("apoiadores") or 0)
                    img   = item.get("foto") or item.get("image") or item.get("avatar") or ""
                    if img and img.startswith("/"): img = "https://queroapoiar.com.br" + img
                    cands.append({"name": nome, "valor": total,
                                  "valor_str": fmt_valor(total),
                                  "img_url": img, "apoio": apoio})
                if cands:
                    cands.sort(key=lambda x: x["valor"], reverse=True)
                    log.info("Candidatos: %d", len(cands))
                    return cands[:8]
            except: continue
        return None
    except Exception as e:
        log.exception("Erro candidatos: %s", e); return None


# ─────────────────────────────────────────────
# DOWNLOAD DE IMAGENS
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
    except Exception as e:
        log.warning("Img fail %s: %s", url[:50], e)
        return None


# ─────────────────────────────────────────────
# GRÁFICOS
# ─────────────────────────────────────────────

BORDA_CORES = {1: "#FFD700", 2: "#C0C0C0", 3: "#CD7F32"}

def build_grid_chart(items, title):
    """Grade 2x2 com borda dourada/prata/bronze para top 3."""
    fig, axes = plt.subplots(2, 2, figsize=(9, 9))
    fig.patch.set_facecolor("#0D1117")
    plt.subplots_adjust(hspace=0.12, wspace=0.10, top=0.90, bottom=0.04, left=0.03, right=0.97)
    fig.text(0.5, 0.95, title, ha="center", color="white",
             fontsize=13, fontweight="bold", transform=fig.transFigure)
    fig.text(0.5, 0.91,
             f"QueroApoiar • {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y')}",
             ha="center", color="#555", fontsize=9, transform=fig.transFigure)

    for idx in range(4):
        row, col = divmod(idx, 2)
        ax = axes[row][col]
        ax.set_facecolor("#161B22")
        ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")

        pos   = idx + 1
        borda = BORDA_CORES.get(pos, "#30363D")
        lw    = 2.0 if pos == 1 else (1.2 if pos <= 3 else 0.5)
        rect  = mpatches.FancyBboxPatch((0.03,0.03), 0.94, 0.94,
                    boxstyle="round,pad=0.02", linewidth=lw,
                    edgecolor=borda, facecolor="#161B22",
                    transform=ax.transAxes, zorder=0)
        ax.add_patch(rect)

        if idx >= len(items): continue
        item = items[idx]

        # Coroa no 1º
        crown = "👑 " if pos == 1 else ""
        cor_pos = BORDA_CORES.get(pos, "#8899A6")
        ax.text(0.5, 0.92, f"{crown}{pos}º", transform=ax.transAxes,
                color=cor_pos, fontsize=11, fontweight="bold",
                va="top", ha="center")

        # Foto/logo
        img_arr = download_img(item["img_url"]) if item.get("img_url") else None
        if img_arr is not None:
            ib = OffsetImage(img_arr, zoom=0.52)
            ab = AnnotationBbox(ib, (0.5, 0.57), frameon=False,
                                xycoords="axes fraction")
            ax.add_artist(ab)
        else:
            circ = plt.Circle((0.5,0.57), 0.20, color="#2D3741",
                               transform=ax.transAxes, zorder=2)
            ax.add_patch(circ)
            ax.text(0.5, 0.57, item["name"][0].upper(), transform=ax.transAxes,
                    color="#8899A6", fontsize=22, fontweight="bold",
                    ha="center", va="center", zorder=3)

        # Nome
        nome = item["name"]
        if len(nome) > 14: nome = nome[:12]+"…"
        ax.text(0.5, 0.31, nome, transform=ax.transAxes,
                color="white", fontsize=9.5, fontweight="bold",
                ha="center", va="center")

        # Valor
        ax.text(0.5, 0.18, item["valor_str"], transform=ax.transAxes,
                color="#00BA7C", fontsize=8.5, fontweight="bold",
                ha="center", va="center")

        # Apoiadores
        apoio = item.get("apoio") or item.get("campanhas")
        if apoio:
            apoio_txt = f"{int(apoio):,} apoiadores".replace(",",".") if isinstance(apoio, (int,float)) else str(apoio)
            if len(apoio_txt) > 22: apoio_txt = apoio_txt[:20]+"…"
            ax.text(0.5, 0.07, apoio_txt, transform=ax.transAxes,
                    color="#8899A6", fontsize=7.5, ha="center", va="center")

    path = CHARTS_DIR / f"grid_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); log.info("Grid chart: %s", path)
    return path


def build_bar_chart(items, title):
    """Gráfico de colunas verticais para top 4."""
    top4   = items[:4]
    fig, ax = plt.subplots(figsize=(9, 6))
    fig.patch.set_facecolor("#0D1117")
    ax.set_facecolor("#0D1117")

    cores  = ["#FFD700", "#C0C0C0", "#CD7F32", "#5B9BD5"]
    nomes  = [c["name"].split()[0] if len(c["name"])>10 else c["name"] for c in top4]
    vals   = [c["valor"] for c in top4]
    x      = np.arange(len(top4))
    bars   = ax.bar(x, vals, color=cores[:len(top4)], width=0.55,
                    edgecolor="none", zorder=3)

    for bar, c in zip(bars, top4):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(vals)*0.02,
                c["valor_str"], ha="center", va="bottom",
                color="white", fontsize=8, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(nomes, color="#CCCCCC", fontsize=10)
    ax.tick_params(axis="y", colors="#555", labelsize=8, length=0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, _: f"R${v/1000:.0f}k" if v < 1_000_000 else f"R${v/1_000_000:.1f}M"))
    ax.grid(axis="y", color="#1E1E2E", linewidth=0.8, zorder=0)
    ax.grid(axis="x", visible=False)
    ax.set_axisbelow(True)
    for spine in ax.spines.values(): spine.set_visible(False)

    # Coroa no 1º
    ax.text(x[0], vals[0] + max(vals)*0.08, "👑",
            ha="center", va="bottom", fontsize=14)

    ax.set_title(title, color="white", fontsize=13, fontweight="bold", pad=12)
    fig.text(0.99, 0.01,
             f"QueroApoiar • {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y')}",
             ha="right", va="bottom", color="#333", fontsize=7, transform=fig.transFigure)
    plt.tight_layout()
    path = CHARTS_DIR / f"bars_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); log.info("Bar chart: %s", path)
    return path


def build_single_card_chart(item, pos=1, subtitle=""):
    """Card único destacado para marcos e ultrapassagens."""
    fig, ax = plt.subplots(figsize=(5, 5))
    fig.patch.set_facecolor("#0D1117")
    ax.set_facecolor("#0D1117")
    ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")

    borda = BORDA_CORES.get(pos, "#30363D")
    lw    = 2.5 if pos == 1 else 1.5
    rect  = mpatches.FancyBboxPatch((0.05,0.05), 0.90, 0.90,
                boxstyle="round,pad=0.03", linewidth=lw,
                edgecolor=borda, facecolor="#161B22",
                transform=ax.transAxes, zorder=0)
    ax.add_patch(rect)

    crown = "👑 " if pos == 1 else ""
    cor   = BORDA_CORES.get(pos, "#8899A6")
    ax.text(0.5, 0.91, f"{crown}{pos}º lugar", transform=ax.transAxes,
            color=cor, fontsize=13, fontweight="bold",
            ha="center", va="top")

    img_arr = download_img(item["img_url"], size=(110,110)) if item.get("img_url") else None
    if img_arr is not None:
        ib = OffsetImage(img_arr, zoom=0.65)
        ab = AnnotationBbox(ib, (0.5,0.58), frameon=False, xycoords="axes fraction")
        ax.add_artist(ab)
    else:
        circ = plt.Circle((0.5,0.58), 0.25, color="#2D3741", transform=ax.transAxes, zorder=2)
        ax.add_patch(circ)
        ax.text(0.5, 0.58, item["name"][0].upper(), transform=ax.transAxes,
                color="#8899A6", fontsize=28, fontweight="bold",
                ha="center", va="center", zorder=3)

    ax.text(0.5, 0.31, item["name"], transform=ax.transAxes,
            color="white", fontsize=11, fontweight="bold",
            ha="center", va="center")
    ax.text(0.5, 0.19, item["valor_str"], transform=ax.transAxes,
            color="#00BA7C", fontsize=10, fontweight="bold",
            ha="center", va="center")
    if subtitle:
        ax.text(0.5, 0.09, subtitle, transform=ax.transAxes,
                color="#8899A6", fontsize=8.5, ha="center", va="center")

    path = CHARTS_DIR / f"card_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); return path


def build_overtake_chart(winner, loser, wpos, lpos):
    """Dois cards lado a lado para ultrapassagem."""
    fig, axes = plt.subplots(1, 2, figsize=(9, 5))
    fig.patch.set_facecolor("#0D1117")
    plt.subplots_adjust(wspace=0.08, left=0.03, right=0.97, top=0.88, bottom=0.06)

    for ax, item, pos, arrow in [
        (axes[0], winner, wpos, "▲"),
        (axes[1], loser,  lpos, "▼"),
    ]:
        ax.set_facecolor("#0D1117")
        ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")
        borda = BORDA_CORES.get(pos, "#30363D")
        lw    = 2.0 if pos <= 3 else 0.5
        rect  = mpatches.FancyBboxPatch((0.04,0.04), 0.92, 0.92,
                    boxstyle="round,pad=0.02", linewidth=lw,
                    edgecolor=borda, facecolor="#161B22",
                    transform=ax.transAxes, zorder=0)
        ax.add_patch(rect)

        cor = BORDA_CORES.get(pos, "#8899A6")
        crown = "👑 " if pos == 1 else ""
        ax.text(0.5, 0.93, f"{crown}{pos}º {arrow}", transform=ax.transAxes,
                color=cor, fontsize=11, fontweight="bold", ha="center", va="top")

        img_arr = download_img(item["img_url"]) if item.get("img_url") else None
        if img_arr is not None:
            ib = OffsetImage(img_arr, zoom=0.50)
            ab = AnnotationBbox(ib, (0.5,0.57), frameon=False, xycoords="axes fraction")
            ax.add_artist(ab)
        else:
            circ = plt.Circle((0.5,0.57), 0.22, color="#2D3741",
                               transform=ax.transAxes, zorder=2)
            ax.add_patch(circ)
            ax.text(0.5, 0.57, item["name"][0].upper(), transform=ax.transAxes,
                    color="#8899A6", fontsize=20, fontweight="bold",
                    ha="center", va="center", zorder=3)

        nome = item["name"]
        if len(nome) > 14: nome = nome[:12]+"…"
        ax.text(0.5, 0.31, nome, transform=ax.transAxes,
                color="white", fontsize=9, fontweight="bold",
                ha="center", va="center")
        cor_val = "#00BA7C" if arrow == "▲" else "#F4212E"
        ax.text(0.5, 0.18, item["valor_str"], transform=ax.transAxes,
                color=cor_val, fontsize=8.5, fontweight="bold",
                ha="center", va="center")

    fig.text(0.5, 0.95, "Mudança no Ranking — QueroApoiar",
             ha="center", color="white", fontsize=12, fontweight="bold")
    fig.text(0.5, 0.01,
             f"QueroApoiar • {datetime.now(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M')}",
             ha="center", color="#333", fontsize=7)
    path = CHARTS_DIR / f"overtake_{datetime.now(BRAZIL_TZ).strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0D1117")
    plt.close(); return path


# ─────────────────────────────────────────────
# TEXTOS
# ─────────────────────────────────────────────

def delta_str_valor(new_val, old_val):
    if old_val is None: return ""
    d = new_val - old_val
    if abs(d) < 0.01: return "\n   ↔ sem variação"
    sign = "▲ +" if d > 0 else "▼ "
    return f"\n   {sign}{fmt_valor(abs(d))} desde o último post"

def delta_str_apoio(new_a, old_a):
    if old_a is None: return ""
    d = new_a - old_a
    if d == 0: return "\n   ↔ sem variação de apoiadores"
    sign = "▲ +" if d > 0 else "▼ "
    return f"\n   {sign}{int(abs(d)):,} apoiadores desde o último post".replace(",",".")


def build_daily_tweet(items, kind, last_data):
    now_str = datetime.now(BRAZIL_TZ).strftime("%d/%m/%Y")
    label   = "Candidatos" if kind == "candidates" else "Partidos"
    emoji   = "💰" if kind == "candidates" else "🏛️"
    top4    = items[:4]
    leader  = top4[0]

    old_map = {d["name"]: d for d in (last_data or [])}
    old_l   = old_map.get(leader["name"], {})
    d_val   = delta_str_valor(leader["valor"], old_l.get("valor"))

    lines = [
        f"{emoji} Ranking de Arrecadação — {label}",
        f"QueroApoiar — {now_str}",
        "",
        f"🥇 {leader['name']} {random.choice(LEADER_PHRASES)}",
        f"    {leader['valor_str']}{d_val}",
        "",
    ]
    for i, item in enumerate(top4[1:], 1):
        old = old_map.get(item["name"], {})
        dv  = delta_str_valor(item["valor"], old.get("valor"))
        lines.append(f"{MEDAL[i]} {item['name']:<16} | {item['valor_str']}{dv}")

    return "\n".join(lines)


def build_marco_tweet(name, valor, marco, apoio, kind, old_valor=None, old_apoio=None, marco_type="arrecad"):
    emoji = "🚀" if kind == "partido" else "⭐"
    ph    = random.choice(MARCO_PHRASES if marco_type=="arrecad" else APOIO_PHRASES)
    tag   = name

    if marco_type == "arrecad":
        dv = delta_str_valor(valor, old_valor)
        lines = [
            f"{emoji} MARCO HISTÓRICO — {tag}!",
            "",
            f"{tag} {ph}",
            f"{fmt_marco(marco)} em arrecadação!",
            "",
            f"💰 Total: {fmt_valor(valor)}{dv}",
            "",
            f"👥 {int(apoio):,} apoiadores".replace(",","."),
        ]
    else:
        da = delta_str_apoio(apoio, old_apoio)
        num = f"{int(marco):,}".replace(",",".")
        lines = [
            f"🎉 MARCO DE APOIADORES — {tag}!",
            "",
            f"{tag} {ph}",
            f"{num} apoiadores!",
            "",
            f"👥 Total: {int(apoio):,} apoiadores".replace(",",".")+da,
            "",
            f"💰 {fmt_valor(valor)} arrecadados",
        ]
    return "\n".join(lines)


def build_overtake_tweet(winner, loser, wval, lval, wpos, lpos, kind, old_wval=None, old_lval=None):
    header = "🔄 VIRADA NO RANKING — Candidatos!" if kind=="candidates" else "📊 MUDANÇA NO RANKING — Partidos!"
    ph     = random.choice(OVER_PHRASES)
    mw     = MEDAL[wpos-1] if wpos <= 8 else f"{wpos}º"
    ml     = MEDAL[lpos-1] if lpos <= 8 else f"{lpos}º"
    dw     = delta_str_valor(wval, old_wval)
    dl     = delta_str_valor(lval, old_lval)
    diff   = abs(wval - lval)
    lines  = [
        header, "",
        f"{winner} {ph} {loser}!", "",
        f"{mw} {winner:<16} | {fmt_valor(wval)} ▲{dw}",
        f"{ml} {loser:<16} | {fmt_valor(lval)} ▼{dl}",
        "",
        f"Diferença atual: {fmt_valor(diff)}",
    ]
    return "\n".join(lines)


def build_repost_tweet(original):
    return "🌅 Bom dia! Caso tenha perdido:\n\n" + original


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
    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_SECRET,
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
            log.warning("Tentativa %d/%d: %s", attempt, retries, e)
            if attempt < retries: time.sleep(retry_delay)
    return None


# ─────────────────────────────────────────────
# POSTS PRINCIPAIS
# ─────────────────────────────────────────────

def run_daily_post(kind):
    log.info("Post diário: %s", kind)
    items = scrape_candidates() if kind == "candidates" else scrape_parties()
    if not items: log.error("Sem dados."); return

    daily    = load_daily()
    last_key = f"last_{kind}"
    last_data = daily.get(last_key, [])

    text  = build_daily_tweet(items, kind, last_data)

    # Alterna grid e barras
    use_grid = daily.get("chart_toggle", True)
    title = "Ranking Candidatos — QueroApoiar" if kind == "candidates" else "Ranking Partidos — QueroApoiar"
    chart = build_grid_chart(items[:4], title) if use_grid else build_bar_chart(items[:4], title)

    tid = post_tweet(text, chart)
    if tid:
        daily[last_key] = [{"name": c["name"], "valor": c["valor"], "apoio": c.get("apoio",0)} for c in items]
        daily["chart_toggle"] = not use_grid
        daily[f"posted_{kind}_today"] = datetime.now(BRAZIL_TZ).date().isoformat()
        save_daily(daily)
        # Atualiza ranking
        rk = load_ranking()
        rk[kind] = {c["name"]: {"pos": i+1, "val": c["valor"], "apoio": c.get("apoio",0)} for i,c in enumerate(items)}
        save_ranking(rk)
        log.info("Post diário %s OK.", kind)


# ─────────────────────────────────────────────
# MARCOS E ULTRAPASSAGENS
# ─────────────────────────────────────────────

def check_alerts():
    if not can_post_alert(): return

    ms     = load_milestones()
    rk     = load_ranking()
    posted = False

    parties    = scrape_parties()
    candidates = scrape_candidates()

    # ── Marcos Missão partido ───────────────────────────────────
    if parties and not posted:
        mp = next((p for p in parties if p["name"] == MISSAO_PARTIDO), None)
        if mp:
            val   = mp["valor"]
            apoio = mp.get("apoio", 0)
            old_val   = ms.get("missao_p_val")
            old_apoio = ms.get("missao_p_apoio_val")

            key = "missao_p_arrecad"
            lm  = ms.get(key, 0)
            nm  = next((m for m in MARCOS_ARRECAD_PARTIDO if m > lm and val >= m), None)
            if nm:
                text  = build_marco_tweet(MISSAO_PARTIDO, val, nm, apoio, "partido", old_val, None, "arrecad")
                apoio_txt = f"{int(apoio):,} apoiadores".replace(",",".")
                chart = build_single_card_chart(mp, 1, apoio_txt)
                tid   = post_tweet(text, chart)
                if tid:
                    ms[key] = nm; ms["missao_p_val"] = val
                    save_milestones(ms); register_alert(text); posted = True

            if not posted and apoio:
                key2 = "missao_p_apoio"
                la   = ms.get(key2, 0)
                na   = next((m for m in MARCOS_APOIO_PARTIDO if m > la and apoio >= m), None)
                if na:
                    text  = build_marco_tweet(MISSAO_PARTIDO, val, na, apoio, "partido", old_val, old_apoio, "apoio")
                    chart = build_single_card_chart(mp, 1, fmt_valor(val))
                    tid   = post_tweet(text, chart)
                    if tid:
                        ms[key2] = na; ms["missao_p_apoio_val"] = apoio
                        save_milestones(ms); register_alert(text); posted = True

    # ── Marcos Renan Santos ─────────────────────────────────────
    if candidates and not posted:
        rc = next((c for c in candidates if c["name"] == MISSAO_CANDIDATO), None)
        if rc:
            val   = rc["valor"]
            apoio = rc.get("apoio", 0)
            old_val   = ms.get("renan_val")
            old_apoio = ms.get("renan_apoio_val")

            key = "renan_arrecad"
            lm  = ms.get(key, 0)
            nm  = next((m for m in MARCOS_ARRECAD_CAND if m > lm and val >= m), None)
            if nm:
                text  = build_marco_tweet(MISSAO_CANDIDATO, val, nm, apoio, "candidato", old_val, None, "arrecad")
                apoio_txt = f"{int(apoio):,} apoiadores".replace(",",".")
                chart = build_single_card_chart(rc, 1, apoio_txt)
                tid   = post_tweet(text, chart)
                if tid:
                    ms[key] = nm; ms["renan_val"] = val
                    save_milestones(ms); register_alert(text); posted = True

            if not posted and apoio:
                key2 = "renan_apoio"
                la   = ms.get(key2, 0)
                na   = next((m for m in MARCOS_APOIO_CAND if m > la and apoio >= m), None)
                if na:
                    text  = build_marco_tweet(MISSAO_CANDIDATO, val, na, apoio, "candidato", old_val, old_apoio, "apoio")
                    chart = build_single_card_chart(rc, 1, fmt_valor(val))
                    tid   = post_tweet(text, chart)
                    if tid:
                        ms[key2] = na; ms["renan_apoio_val"] = apoio
                        save_milestones(ms); register_alert(text); posted = True

    # ── Ultrapassagens partidos (top 4) ─────────────────────────
    if parties and not posted:
        new_r = {p["name"]: {"pos": i+1, "val": p["valor"], "apoio": p.get("apoio",0)} for i,p in enumerate(parties)}
        old_r = rk.get("parties", {})
        if old_r:
            for name, info in new_r.items():
                if info["pos"] > 4: continue
                old_info = old_r.get(name)
                if old_info and info["pos"] < old_info["pos"] and old_info["pos"] <= 4:
                    loser = next(
                        (n for n,oi in old_r.items()
                         if oi["pos"] == info["pos"] and new_r.get(n,{}).get("pos",99) > info["pos"]),
                        None)
                    if loser and loser in new_r and new_r[loser]["pos"] <= 4:
                        w_item = next((p for p in parties if p["name"]==name), None)
                        l_item = next((p for p in parties if p["name"]==loser), None)
                        if w_item and l_item:
                            old_w = old_r.get(name,{}).get("val")
                            old_l = old_r.get(loser,{}).get("val")
                            text  = build_overtake_tweet(name, loser, info["val"], new_r[loser]["val"],
                                                         info["pos"], new_r[loser]["pos"],
                                                         "parties", old_w, old_l)
                            chart = build_overtake_chart(w_item, l_item, info["pos"], new_r[loser]["pos"])
                            tid   = post_tweet(text, chart)
                            if tid:
                                register_alert(text); posted = True; break
        rk["parties"] = new_r; save_ranking(rk)

    # ── Ultrapassagens candidatos (top 4) ───────────────────────
    if candidates and not posted:
        new_r = {c["name"]: {"pos": i+1, "val": c["valor"], "apoio": c.get("apoio",0)} for i,c in enumerate(candidates)}
        old_r = rk.get("candidates", {})
        if old_r:
            for name, info in new_r.items():
                if info["pos"] > 4: continue
                old_info = old_r.get(name)
                if old_info and info["pos"] < old_info["pos"] and old_info["pos"] <= 4:
                    loser = next(
                        (n for n,oi in old_r.items()
                         if oi["pos"] == info["pos"] and new_r.get(n,{}).get("pos",99) > info["pos"]),
                        None)
                    if loser and loser in new_r and new_r[loser]["pos"] <= 4:
                        w_item = next((c for c in candidates if c["name"]==name), None)
                        l_item = next((c for c in candidates if c["name"]==loser), None)
                        if w_item and l_item:
                            old_w = old_r.get(name,{}).get("val")
                            old_l = old_r.get(loser,{}).get("val")
                            text  = build_overtake_tweet(name, loser, info["val"], new_r[loser]["val"],
                                                         info["pos"], new_r[loser]["pos"],
                                                         "candidates", old_w, old_l)
                            chart = build_overtake_chart(w_item, l_item, info["pos"], new_r[loser]["pos"])
                            tid   = post_tweet(text, chart)
                            if tid:
                                register_alert(text); posted = True; break
        rk["candidates"] = new_r; save_ranking(rk)


def check_repost_queue():
    now = datetime.now(BRAZIL_TZ)
    if now.hour != 8 or now.minute >= 10: return
    queue   = load_repost_queue()
    pending = [q for q in queue if not q.get("reposted")]
    if not pending: return
    for item in pending:
        if can_post_alert():
            text = build_repost_tweet(item["text"])
            tid  = post_tweet(text)
            if tid:
                item["reposted"] = True
                register_alert(text)
                log.info("Repost matinal OK.")
        break
    save_repost_queue(queue)


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────


def bootstrap_milestones():
    """
    Na inicialização, preenche milestones.json com os valores atuais
    sem postar — evita posts de marcos já ultrapassados após reinício.
    """
    ms = load_milestones()
    changed = False

    parties    = scrape_parties()
    candidates = scrape_candidates()

    if parties:
        mp = next((p for p in parties if p["name"] == MISSAO_PARTIDO), None)
        if mp:
            val   = mp["valor"]
            apoio = mp.get("apoio", 0)

            # Arrecadação: marca o maior marco já ultrapassado
            key = "missao_p_arrecad"
            current_mark = ms.get(key, 0)
            highest = max((m for m in MARCOS_ARRECAD_PARTIDO if val >= m), default=0)
            if highest > current_mark:
                ms[key] = highest
                ms["missao_p_val"] = val
                log.info("Bootstrap marco partido arrecad: %s", fmt_marco(highest))
                changed = True

            # Apoiadores
            if apoio:
                key2 = "missao_p_apoio"
                current_apoio = ms.get(key2, 0)
                highest_a = max((m for m in MARCOS_APOIO_PARTIDO if apoio >= m), default=0)
                if highest_a > current_apoio:
                    ms[key2] = highest_a
                    ms["missao_p_apoio_val"] = apoio
                    log.info("Bootstrap marco partido apoio: %d", highest_a)
                    changed = True

    if candidates:
        rc = next((c for c in candidates if c["name"] == MISSAO_CANDIDATO), None)
        if rc:
            val   = rc["valor"]
            apoio = rc.get("apoio", 0)

            key = "renan_arrecad"
            current_mark = ms.get(key, 0)
            highest = max((m for m in MARCOS_ARRECAD_CAND if val >= m), default=0)
            if highest > current_mark:
                ms[key] = highest
                ms["renan_val"] = val
                log.info("Bootstrap marco candidato arrecad: %s", fmt_marco(highest))
                changed = True

            if apoio:
                key2 = "renan_apoio"
                current_apoio = ms.get(key2, 0)
                highest_a = max((m for m in MARCOS_APOIO_CAND if apoio >= m), default=0)
                if highest_a > current_apoio:
                    ms[key2] = highest_a
                    ms["renan_apoio_val"] = apoio
                    log.info("Bootstrap marco candidato apoio: %d", highest_a)
                    changed = True

    if changed:
        save_milestones(ms)
        log.info("Bootstrap de marcos concluído.")
    else:
        log.info("Bootstrap: marcos já atualizados.")

def run_scheduler():
    log.info("Bot iniciado — post diario 18h alternado | alertas a qualquer hora")

    # Preenche marcos já ultrapassados sem postar (evita spam após reinício)
    bootstrap_milestones()

    while True:
        now   = datetime.now(BRAZIL_TZ)
        today = now.date().isoformat()
        daily = load_daily()

        # Post diário às 18h — alterna candidatos/partidos por dia
        if now.hour == 18 and now.minute < 5:
            kind = "candidates" if now.day % 2 != 0 else "parties"
            if daily.get(f"posted_{kind}_today") != today:
                run_daily_post(kind)

        # Repost matinal às 8h
        check_repost_queue()

        # Marcos e ultrapassagens — qualquer hora
        check_alerts()

        time.sleep(300)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--test-candidates":   run_daily_post("candidates")
    elif arg == "--test-parties":    run_daily_post("parties")
    elif arg == "--test-alerts":     check_alerts()
    elif arg == "--debug-parties":
        from bs4 import BeautifulSoup
        r = requests.get("https://queroapoiar.com.br/partidos", headers=QA_HEADERS)
        soup = BeautifulSoup(r.text, "lxml")
        classes = set()
        for t in soup.find_all(True):
            for c in t.get("class",[]): classes.add(c)
        print("\n".join(sorted(classes)[:80]))
        print(r.text[:3000])
    else: run_scheduler()
