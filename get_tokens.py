"""
Script de diagnóstico — rode no Railway para pegar os token_ids reais.
Cole o resultado aqui para eu gerar o gráfico com histórico verdadeiro.
"""
import requests, json

url = "https://gamma-api.polymarket.com/events?slug=brazil-presidential-election"
resp = requests.get(url, timeout=15)
data = resp.json()
markets = data[0].get("markets", [])

print(f"=== CANDIDATOS E TOKEN IDs ===")
result = []
for m in markets:
    name = m.get("groupItemTitle") or m.get("question", "?")
    prices = m.get("outcomePrices", "[]")
    if isinstance(prices, str): prices = json.loads(prices)
    prob = round(float(prices[0])*100, 1) if prices else 0
    tokens = m.get("clobTokenIds", "[]")
    if isinstance(tokens, str): tokens = json.loads(tokens)
    token_id = tokens[0] if tokens else ""
    result.append({"name": name, "prob": prob, "token_id": token_id})
    print(f'{prob:5.1f}% | {name[:40]:<40} | {token_id}')

result.sort(key=lambda x: x["prob"], reverse=True)
print("\n=== TOP 5 JSON (copie isso) ===")
print(json.dumps(result[:5], ensure_ascii=False, indent=2))

# Testa histórico do top 1
if result:
    tid = result[0]["token_id"]
    print(f"\n=== TESTANDO HISTÓRICO de {result[0]['name']} ===")
    h = requests.get(
        "https://clob.polymarket.com/prices-history",
        params={"market": tid, "interval": "max", "fidelity": 1440},
        timeout=20
    ).json()
    pts = h.get("history", [])
    print(f"Pontos históricos: {len(pts)}")
    if pts:
        from datetime import datetime
        print(f"Primeiro: {datetime.fromtimestamp(pts[0]['t']).strftime('%d/%m/%Y')} → {float(pts[0]['p'])*100:.1f}%")
        print(f"Último:   {datetime.fromtimestamp(pts[-1]['t']).strftime('%d/%m/%Y')} → {float(pts[-1]['p'])*100:.1f}%")
