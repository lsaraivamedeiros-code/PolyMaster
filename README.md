# 🗳️ Bot X — Eleições Brasil 2026 (Polymarket)

Bot que posta automaticamente as probabilidades dos candidatos à presidência
do Brasil em 2026, com base nos dados do Polymarket.

---

## O que o bot faz

| Quando | O quê |
|---|---|
| **Todos os dias às 08:00** | Post com top-5 + variação + gráfico |
| **Todos os dias às 16:00** | Post com top-5 + variação + gráfico |
| **A qualquer hora** | Post extra se qualquer candidato do top-5 variar ≥ 1pp desde o último post |

Cada post contém:
- 🥇–5️⃣ Nome do candidato + probabilidade atual
- ▲/▼ Variação em pontos percentuais desde o post anterior
- 📈 Gráfico de linhas com histórico diário de todos os top-5

---

## Pré-requisitos

### 1. Conta no Twitter/X Developer Portal

1. Acesse [developer.twitter.com](https://developer.twitter.com/en/portal/dashboard)
2. Crie um **Project** e um **App** dentro dele
3. Mude as permissões do App para **Read and Write** (e "Upload Media")
4. Em **Keys and Tokens**, gere e anote:
   - API Key & Secret
   - Access Token & Secret (com permissão de escrita)
   - Bearer Token

> ⚠️ Para postar via API v2 com upload de mídia você precisa do plano **Basic** (US$ 100/mês) ou superior.  
> Alternativamente, use o plano **Free** mas **sem imagem** (remova o `chart_path` na chamada `run_post`).

### 2. Slug do mercado no Polymarket

1. Acesse [polymarket.com](https://polymarket.com)
2. Pesquise **"Brazil 2026 president"** ou **"eleição brasil"**
3. Abra o mercado correto
4. Copie o slug da URL: `polymarket.com/event/**SEU-SLUG-AQUI**`
5. Cole no arquivo `.env` na variável `POLYMARKET_MARKET_SLUG`

---

## Deploy em servidor Linux (Ubuntu 22.04+)

### Passo 1 — Copie os arquivos

```bash
sudo mkdir -p /opt/xbot_eleicoes
sudo cp -r . /opt/xbot_eleicoes/
sudo chown -R ubuntu:ubuntu /opt/xbot_eleicoes
```

### Passo 2 — Crie o ambiente virtual e instale dependências

```bash
cd /opt/xbot_eleicoes
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Passo 3 — Configure as variáveis de ambiente

```bash
cp .env.example .env
nano .env          # preencha com suas credenciais reais
```

### Passo 4 — Teste o bot antes de subir como serviço

```bash
source venv/bin/activate
python bot.py --post-now   # posta imediatamente uma vez para testar
```

### Passo 5 — Instale como serviço systemd (roda em background, reinicia sozinho)

```bash
sudo cp xbot_eleicoes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable xbot_eleicoes
sudo systemctl start xbot_eleicoes
```

### Verificar logs em tempo real

```bash
sudo systemctl status xbot_eleicoes
tail -f /opt/xbot_eleicoes/logs/bot.log
```

---

## Deploy sem servidor (GitHub Actions — gratuito)

Se não tiver VPS, use GitHub Actions com cron para acionar o bot duas vezes ao dia.
A verificação de alerta só funciona se rodar continuamente (VPS/Render/Railway).

### `.github/workflows/post.yml`

```yaml
name: Post Eleições 2026

on:
  schedule:
    - cron: "0 11 * * *"   # 08:00 Brasília (UTC-3)
    - cron: "0 19 * * *"   # 16:00 Brasília (UTC-3)
  workflow_dispatch:        # Permite acionar manualmente

jobs:
  post:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run bot
        env:
          TWITTER_API_KEY: ${{ secrets.TWITTER_API_KEY }}
          TWITTER_API_SECRET: ${{ secrets.TWITTER_API_SECRET }}
          TWITTER_ACCESS_TOKEN: ${{ secrets.TWITTER_ACCESS_TOKEN }}
          TWITTER_ACCESS_TOKEN_SECRET: ${{ secrets.TWITTER_ACCESS_TOKEN_SECRET }}
          TWITTER_BEARER_TOKEN: ${{ secrets.TWITTER_BEARER_TOKEN }}
          POLYMARKET_MARKET_SLUG: ${{ secrets.POLYMARKET_MARKET_SLUG }}
        run: python bot.py --post-now
```

> Configure os secrets em **Settings → Secrets and variables → Actions** no repositório GitHub.

---

## Estrutura de arquivos

```
xbot_eleicoes/
├── bot.py                  ← código principal
├── requirements.txt
├── .env.example            ← template de credenciais
├── .env                    ← suas credenciais (NÃO commitar!)
├── xbot_eleicoes.service   ← serviço systemd
├── data/
│   ├── history.json        ← histórico diário de probabilidades
│   └── last_post.json      ← estado do último post
├── charts/                 ← gráficos gerados (PNG)
└── logs/
    └── bot.log
```

---

## Exemplo de post gerado

```
🗳️ Eleição Presidencial Brasil 2026
📊 Probabilidades Polymarket — 22/05/2026 08:00

🥇 Lula: 45.2%  ▲ +1.3pp
🥈 Bolsonaro: 28.7%  ▼ -0.8pp
🥉 Tarcísio: 15.1%  ↔ 0.0pp
4️⃣ Simone Tebet: 6.4%  ▲ +0.3pp
5️⃣ Ciro Gomes: 2.8%  ▼ -0.5pp

📈 Fonte: Polymarket
🕐 🌅 Post agendado das 08:00
#Eleições2026 #Brasil #Polymarket
```

---

## Dúvidas frequentes

**O slug do mercado no Polymarket não existe ainda.**  
O mercado de eleições 2026 pode ainda não estar ativo. Monitore o Polymarket e atualize `POLYMARKET_MARKET_SLUG` quando o mercado for criado.

**Erro 403 ao postar.**  
Verifique que o app X tem permissão *Read and Write* e que os tokens foram gerados **depois** de alterar a permissão.

**O gráfico não aparece no tweet.**  
Upload de mídia requer plano Basic da API do X. Você pode desabilitar o gráfico comentando `chart_path = build_chart(...)` e passando `chart_path=None` em `run_post`.
