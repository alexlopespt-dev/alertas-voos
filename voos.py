#!/usr/bin/env python3
"""Alertas de voos baratos: Península Ibérica -> Tailândia e praias do Sudeste Asiático.

Vai buscar os preços à API de dados do Aviasales (Travelpayouts, gratuita) e envia
um aviso para o telemóvel pelo ntfy quando há ida e volta abaixo do preço máximo.

Uso:
  python voos.py            procura e avisa (o normal, corre no GitHub Actions)
  python voos.py --simular  procura e mostra o que avisaria, sem enviar nem gravar
  python voos.py --teste    só envia uma notificação de teste

Variáveis de ambiente:
  TRAVELPAYOUTS_TOKEN  token da API (travelpayouts.com -> Perfil -> API token)
  NTFY_TOPICO          nome do canal no ntfy (o mesmo que subscreves na app)
  NTFY_SERVIDOR        opcional, por omissão https://ntfy.sh
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(AQUI, "config.json")
ESTADO = os.path.join(AQUI, "estado.json")

API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
BANDEIRAS = {
    "Tailândia": "🇹🇭", "Vietname": "🇻🇳", "Indonésia": "🇮🇩", "Filipinas": "🇵🇭",
    "Malásia": "🇲🇾", "Sri Lanka": "🇱🇰", "Maldivas": "🇲🇻", "Camboja": "🇰🇭",
}


# ---------------------------------------------------------------- utilitários

def ler_json(caminho, omissao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return omissao


def gravar_json(caminho, dados):
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, caminho)


def dia(texto):
    """'2026-11-12T10:35:00+00:00' ou '2026-11-12' -> date (ou None)."""
    if not texto:
        return None
    try:
        return date.fromisoformat(str(texto)[:10])
    except ValueError:
        return None


def data_curta(d):
    return f"{d.day} {MESES[d.month - 1]}"


def intervalo(ida, volta):
    if ida.year == volta.year and ida.month == volta.month:
        return f"{ida.day}–{volta.day} {MESES[ida.month - 1]}"
    return f"{data_curta(ida)} – {data_curta(volta)}"


def escalas_txt(n):
    return "direto" if n == 0 else ("1 escala" if n == 1 else f"{n} escalas")


def euros(p):
    return f"{int(round(p))} €"


# ---------------------------------------------------------------- API de preços

def pedir(params, token, tentativas=3):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-Access-Token": token,
        "Accept": "application/json",
        "User-Agent": "alertas-voos/1.0",
    })
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                corpo = json.loads(r.read().decode("utf-8"))
            if not corpo.get("success", True):
                raise RuntimeError(corpo.get("error") or "resposta sem sucesso")
            return corpo.get("data") or []
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(f"token recusado ({e.code}) — confirma o TRAVELPAYOUTS_TOKEN")
            if e.code == 429 or e.code >= 500:
                time.sleep(5 * (i + 1))
                continue
            raise RuntimeError(f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError) as e:
            if i == tentativas - 1:
                raise RuntimeError(f"sem ligação: {e}")
            time.sleep(3 * (i + 1))
    raise RuntimeError("demasiados pedidos (429) ou servidor em baixo")


def meses_a_pesquisar(cfg, hoje):
    if not cfg.get("por_mes"):
        return [None]
    out, a, m = [], hoje.year, hoje.month
    for _ in range(int(cfg.get("partida_max_meses", 11)) + 1):
        out.append(f"{a:04d}-{m:02d}")
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return out


def procurar(cfg, token, hoje, pausa=0.3, log=print):
    """Devolve (bilhetes, erros). Cada bilhete é o dicionário da API + 'o'/'d' pedidos."""
    bilhetes, erros, pedidos = [], [], 0
    for o in cfg["origens"]:
        for dest in cfg["destinos"]:
            if dest.get("ativo") is False:
                continue
            n_rota = 0
            for mes in meses_a_pesquisar(cfg, hoje):
                params = {
                    "origin": o, "destination": dest["iata"],
                    "currency": cfg.get("moeda", "eur"),
                    "one_way": "false", "direct": "false", "unique": "false",
                    "sorting": "price", "limit": 1000, "page": 1,
                }
                if mes:
                    params["departure_at"] = mes
                try:
                    if pedidos:
                        time.sleep(pausa)
                    pedidos += 1
                    dados = pedir(params, token)
                except RuntimeError as e:
                    erros.append(f"{o}→{dest['iata']}{' ' + mes if mes else ''}: {e}")
                    if "token recusado" in str(e):
                        return bilhetes, erros
                    continue
                for b in dados:
                    b = dict(b)
                    b["_o"], b["_d"] = o, dest["iata"]
                    bilhetes.append(b)
                n_rota += len(dados)
            log(f"  {o}→{dest['iata']}: {n_rota} preços")
    return bilhetes, erros


# ---------------------------------------------------------------- filtro

def preco_max_de(cfg, dest):
    return float(dest.get("preco_max", cfg.get("preco_max", 400)))


def filtrar(bilhetes, cfg, hoje):
    """Só ida e volta dentro das regras; devolve ofertas normalizadas, a mais barata por datas."""
    destinos = {d["iata"]: d for d in cfg["destinos"]}
    p_min = hoje + timedelta(days=int(cfg.get("partida_min_dias", 7)))
    p_max = hoje + timedelta(days=int(cfg.get("partida_max_meses", 11)) * 31)
    esc_max = int(cfg.get("escalas_max", 2))
    dur_max = float(cfg.get("horas_viagem_max", 0) or 0) * 60
    melhores = {}
    for b in bilhetes:
        dest = destinos.get(b.get("_d"))
        if not dest:
            continue
        try:
            preco = float(b.get("price"))
        except (TypeError, ValueError):
            continue
        ida, volta = dia(b.get("departure_at")), dia(b.get("return_at"))
        if not ida or not volta or preco <= 0:
            continue  # sem volta = só ida
        if preco > preco_max_de(cfg, dest):
            continue
        dias = (volta - ida).days
        if dias < int(cfg.get("dias_min", 7)) or dias > int(cfg.get("dias_max", 30)):
            continue
        if ida < p_min or ida > p_max:
            continue
        esc = max(int(b.get("transfers") or 0), int(b.get("return_transfers") or 0))
        if esc > esc_max:
            continue
        if dur_max:
            ida_min = b.get("duration_to")
            volta_min = b.get("duration_back")
            if (ida_min and ida_min > dur_max) or (volta_min and volta_min > dur_max):
                continue
        o = b["_o"]
        chave = f"{o}-{dest['iata']}-{ida.isoformat()}-{volta.isoformat()}"
        link = b.get("link") or ""
        oferta = {
            "chave": chave, "o": o, "d": dest["iata"], "preco": preco,
            "ida": ida.isoformat(), "volta": volta.isoformat(), "dias": dias,
            "escalas": esc, "cia": b.get("airline") or "",
            "link": ("https://www.aviasales.com" + link) if link.startswith("/") else link,
        }
        if chave not in melhores or preco < melhores[chave]["preco"]:
            melhores[chave] = oferta
    return sorted(melhores.values(), key=lambda x: x["preco"])


def novas(ofertas, estado, cfg):
    """Só as que ainda não foram avisadas ou que desceram pelo menos X%."""
    pct = float(cfg.get("realerta_descida_pct", 5)) / 100
    vistos = estado.get("avisados", {})
    out = []
    for f in ofertas:
        antes = vistos.get(f["chave"])
        if antes is None or f["preco"] <= antes["preco"] * (1 - pct):
            if antes is not None:
                f = dict(f, antes=antes["preco"])
            out.append(f)
    return out


def limpar_estado(estado, hoje):
    av = estado.get("avisados", {})
    estado["avisados"] = {k: v for k, v in av.items() if (dia(v.get("ida")) or hoje) >= hoje}
    return estado


def mais_baratos(bilhetes, cfg, hoje, n=8):
    """Os n mais baratos com as mesmas regras mas sem limite de preço (só para o registo)."""
    sem_limite = dict(cfg, preco_max=10 ** 9,
                      destinos=[{k: v for k, v in d.items() if k != "preco_max"} for d in cfg["destinos"]])
    return filtrar(bilhetes, sem_limite, hoje)[:n]


# ---------------------------------------------------------------- mensagens

def resumo(f, cfg):
    destinos = {d["iata"]: d for d in cfg["destinos"]}
    ida, volta = dia(f["ida"]), dia(f["volta"])
    return (f"{euros(f['preco'])} · {cfg['origens'].get(f['o'], f['o'])} → "
            f"{destinos[f['d']]['nome']} · {intervalo(ida, volta)} ({f['dias']} dias) · "
            f"{escalas_txt(f['escalas'])}" + (f" · {f['cia']}" if f["cia"] else ""))


def link_google(f):
    q = f"Flights to {f['d']} from {f['o']} on {f['ida']} through {f['volta']}"
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode(
        {"q": q, "curr": "EUR", "hl": "pt-PT"})


def mensagens(lista, cfg, maximo=None):
    """Agrupa por destino: um aviso por destino com as melhores datas. Tailândia primeiro."""
    destinos = {d["iata"]: d for d in cfg["destinos"]}
    nomes_o = cfg["origens"]
    grupos = {}
    for f in lista:
        grupos.setdefault(f["d"], []).append(f)
    ordem = sorted(grupos, key=lambda k: (not destinos[k].get("prioridade"),
                                          min(x["preco"] for x in grupos[k])))
    msgs = []
    for k in ordem:
        fs = sorted(grupos[k], key=lambda x: x["preco"])
        dest, b = destinos[k], fs[0]
        band = BANDEIRAS.get(dest.get("pais"), "✈️")
        titulo = f"{band} {dest['nome']} desde {euros(b['preco'])} ida e volta"
        linhas = []
        for f in fs[:5]:
            ida, volta = dia(f["ida"]), dia(f["volta"])
            t = (f"{nomes_o.get(f['o'], f['o'])} · {intervalo(ida, volta)} ({f['dias']} dias) · "
                 f"{euros(f['preco'])} · {escalas_txt(f['escalas'])}")
            if f["cia"]:
                t += f" · {f['cia']}"
            if f.get("antes"):
                t += f" (era {euros(f['antes'])})"
            linhas.append(t)
        if len(fs) > 5:
            linhas.append(f"+ {len(fs) - 5} outras datas")
        linhas.append("Preço de pesquisas recentes: confirma antes de comprar.")
        acoes = []
        if b["link"]:
            acoes.append({"action": "view", "label": "Aviasales", "url": b["link"], "clear": False})
        acoes.append({"action": "view", "label": "Google Flights", "url": link_google(b), "clear": False})
        msgs.append({
            "title": titulo,
            "message": "\n".join(linhas),
            "tags": ["airplane"],
            "priority": 5 if dest.get("prioridade") and b["preco"] <= preco_max_de(cfg, dest) * 0.85
            else (4 if dest.get("prioridade") else 3),
            "click": b["link"] or link_google(b),
            "actions": acoes,
        })
    if maximo and len(msgs) > maximo:
        resto = msgs[maximo - 1:]
        msgs = msgs[:maximo - 1] + [{
            "title": f"✈️ Mais {len(resto)} destinos abaixo do preço",
            "message": "\n".join(m["title"] for m in resto),
            "tags": ["airplane"], "priority": 3,
        }]
    return msgs


# ---------------------------------------------------------------- ntfy

def enviar(msg, topico, servidor=None):
    servidor = (servidor or "https://ntfy.sh").rstrip("/")
    corpo = dict(msg, topic=topico)
    req = urllib.request.Request(
        servidor + "/", data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "alertas-voos/1.0"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status >= 300:
            raise RuntimeError(f"ntfy respondeu {r.status}")


# ---------------------------------------------------------------- principal

def correr(cfg, estado, token, topico, servidor=None, hoje=None, simular=False,
           buscar=procurar, mandar=enviar, log=print):
    hoje = hoje or date.today()
    limpar_estado(estado, hoje)
    log(f"A procurar {len(cfg['origens'])} origens × {len(cfg['destinos'])} destinos…")
    bilhetes, erros = buscar(cfg, token, hoje, log=log)
    ofertas = filtrar(bilhetes, cfg, hoje)
    lista = novas(ofertas, estado, cfg)
    log(f"{len(bilhetes)} preços recebidos, {len(ofertas)} abaixo do limite, {len(lista)} novos.")
    for e in erros[:10]:
        log("  erro: " + e)
    log("Mais baratos agora (mesmo acima do limite):")
    for f in mais_baratos(bilhetes, cfg, hoje):
        log("  " + resumo(f, cfg))

    msgs = mensagens(lista, cfg, int(cfg.get("max_avisos_por_execucao", 6)))
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Tudo falhou (token errado, API em baixo): avisa no máximo uma vez por dia.
    if erros and not bilhetes:
        ult = estado.get("ultimo_erro_avisado", "")
        if ult[:10] != agora[:10]:
            msgs.append({"title": "⚠️ Alertas de voos sem dados",
                         "message": "A pesquisa de preços falhou: " + erros[0],
                         "tags": ["warning"], "priority": 3})
            if not simular:
                estado["ultimo_erro_avisado"] = agora

    for m in msgs:
        log(f"\n>>> {m['title']}\n{m['message']}")
        if not simular:
            mandar(m, topico, servidor)

    if not simular:
        for f in lista:
            estado.setdefault("avisados", {})[f["chave"]] = {
                "preco": f["preco"], "ida": f["ida"], "em": agora}
        estado["ultima_execucao"] = agora
        estado["ultimo_resumo"] = {"precos": len(bilhetes), "abaixo": len(ofertas),
                                   "novos": len(lista), "erros": len(erros)}
    return msgs, erros, bilhetes


def main(argv):
    cfg = ler_json(CONFIG, None)
    if not cfg:
        sys.exit("Falta o config.json")
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
    topico = os.environ.get("NTFY_TOPICO", "").strip()
    servidor = os.environ.get("NTFY_SERVIDOR", "").strip() or None
    simular = "--simular" in argv

    if "--teste" in argv:
        if not topico:
            sys.exit("Falta o NTFY_TOPICO")
        enviar({"title": "✈️ Alertas de voos ligados",
                "message": f"Vais receber avisos de ida e volta abaixo de {euros(cfg['preco_max'])}.",
                "tags": ["white_check_mark"]}, topico, servidor)
        print("Notificação de teste enviada.")
        return 0

    if not token:
        sys.exit("Falta o TRAVELPAYOUTS_TOKEN")
    if not topico and not simular:
        sys.exit("Falta o NTFY_TOPICO")

    estado = ler_json(ESTADO, {})
    msgs, erros, bilhetes = correr(cfg, estado, token, topico, servidor, simular=simular)
    if not simular:
        gravar_json(ESTADO, estado)
    if erros and not bilhetes:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
