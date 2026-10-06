import json
import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import voos  # noqa: E402


HOJE = date(2026, 10, 6)
CFG = voos.ler_json(voos.CONFIG, None)


def bilhete(o="LIS", d="BKK", preco=350, ida="2026-11-10", volta="2026-11-24", **kw):
    b = {"_o": o, "_d": d, "price": preco, "airline": "EK", "transfers": 1, "return_transfers": 1,
         "departure_at": ida + "T10:00:00+00:00", "return_at": volta + "T21:00:00+07:00",
         "duration_to": 900, "duration_back": 960,
         "link": f"/search/{o}1011{d}24111?t=x"}
    b.update(kw)
    return b


class Filtro(unittest.TestCase):
    def test_aceita_ida_e_volta_barata(self):
        r = voos.filtrar([bilhete()], CFG, HOJE)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["chave"], "LIS-BKK-2026-11-10-2026-11-24")
        self.assertEqual(r[0]["dias"], 14)
        self.assertTrue(r[0]["link"].startswith("https://www.aviasales.com/search/"))

    def test_regras(self):
        casos = {
            "acima do preço": bilhete(preco=401),
            "só ida": bilhete(return_at=None),
            "viagem curta": bilhete(volta="2026-11-13"),
            "viagem longa": bilhete(volta="2026-12-31"),
            "parte já amanhã": bilhete(ida="2026-10-07", volta="2026-10-20"),
            "muito longe": bilhete(ida="2027-12-01", volta="2027-12-15"),
            "3 escalas": bilhete(transfers=3),
            "volta com 3 escalas": bilhete(return_transfers=3),
            "viagem de 40 h": bilhete(duration_to=40 * 60),
            "destino fora da lista": bilhete(d="JFK"),
            "preço inválido": bilhete(preco=None),
        }
        for nome, b in casos.items():
            with self.subTest(nome):
                self.assertEqual(voos.filtrar([b], CFG, HOJE), [])

    def test_limite_exato_e_sem_duracao(self):
        r = voos.filtrar([bilhete(preco=400, duration_to=None, duration_back=None)], CFG, HOJE)
        self.assertEqual(len(r), 1)

    def test_fica_o_mais_barato_das_mesmas_datas(self):
        r = voos.filtrar([bilhete(preco=380), bilhete(preco=330), bilhete(preco=390)], CFG, HOJE)
        self.assertEqual([x["preco"] for x in r], [330])

    def test_preco_max_por_destino(self):
        cfg = json.loads(json.dumps(CFG))
        for d in cfg["destinos"]:
            if d["iata"] == "MLE":
                d["preco_max"] = 500
        self.assertEqual(len(voos.filtrar([bilhete(d="MLE", preco=480)], cfg, HOJE)), 1)
        self.assertEqual(voos.filtrar([bilhete(d="BKK", preco=480)], cfg, HOJE), [])


class Repeticoes(unittest.TestCase):
    def test_nao_repete_e_avisa_se_descer(self):
        estado = {}
        f = voos.filtrar([bilhete(preco=350)], CFG, HOJE)
        self.assertEqual(len(voos.novas(f, estado, CFG)), 1)
        estado["avisados"] = {f[0]["chave"]: {"preco": 350, "ida": "2026-11-10"}}
        self.assertEqual(voos.novas(f, estado, CFG), [])
        # desce 2%: não avisa; desce 6%: avisa e diz o preço anterior
        self.assertEqual(voos.novas(voos.filtrar([bilhete(preco=343)], CFG, HOJE), estado, CFG), [])
        n = voos.novas(voos.filtrar([bilhete(preco=329)], CFG, HOJE), estado, CFG)
        self.assertEqual(n[0]["antes"], 350)

    def test_limpa_viagens_passadas(self):
        estado = {"avisados": {"a": {"preco": 1, "ida": "2026-10-01"},
                               "b": {"preco": 1, "ida": "2026-12-01"}}}
        voos.limpar_estado(estado, HOJE)
        self.assertEqual(list(estado["avisados"]), ["b"])


class Mensagens(unittest.TestCase):
    def test_agrupa_por_destino_tailandia_primeiro(self):
        f = voos.filtrar([bilhete(d="DPS", preco=300), bilhete(d="HKT", preco=390),
                          bilhete(o="MAD", d="HKT", preco=360, ida="2026-11-12", volta="2026-11-26")],
                         CFG, HOJE)
        m = voos.mensagens(f, CFG)
        self.assertEqual(len(m), 2)
        self.assertTrue(m[0]["title"].startswith("🇹🇭 Phuket desde 360 €"))
        self.assertIn("Madrid · 12–26 nov (14 dias) · 360 € · 1 escala · Emirates", m[0]["message"])
        self.assertIn("Lisboa · 10–24 nov", m[0]["message"])
        self.assertEqual(m[0]["priority"], 4)
        self.assertEqual(m[1]["priority"], 3)
        self.assertEqual([a["label"] for a in m[0]["actions"]], ["Aviasales", "Google Flights", "Skyscanner"])
        self.assertIn("google.com/travel/flights", m[0]["actions"][1]["url"])
        # os outros sites vão no texto, com a rota e as datas do mais barato
        self.assertIn("Comparar (Madrid, 12–26 nov):", m[0]["message"])
        self.assertIn("Trip.com: https://pt.trip.com/flights/showfarefirst?dcity=mad&acity=hkt"
                      "&ddate=2026-11-12&rdate=2026-11-26", m[0]["message"])
        self.assertIn("Kayak: https://www.kayak.pt/flights/MAD-HKT/2026-11-12/2026-11-26", m[0]["message"])
        self.assertIn("Momondo: https://www.momondo.pt/flight-search/MAD-HKT/2026-11-12/2026-11-26",
                      m[0]["message"])
        self.assertLess(len(json.dumps(m[0]).encode()), 4000)  # limite da ntfy: 4096 bytes

    def test_links_de_todos_os_sites(self):
        f = voos.filtrar([bilhete(o="LIS", d="BKK", ida="2027-01-05", volta="2027-01-20")], CFG, HOJE)[0]
        lk = voos.links(f)
        self.assertEqual(list(lk), ["Aviasales", "Google Flights", "Skyscanner", "Trip.com", "Kayak", "Momondo"])
        self.assertEqual(lk["Skyscanner"], "https://www.skyscanner.pt/transporte/voos/lis/bkk/270105/270120/"
                                           "?adultsv2=1&cabinclass=economy&rtn=1")
        self.assertIn("triptype=rt", lk["Trip.com"])
        for u in lk.values():
            self.assertTrue(u.startswith("https://"), u)
        # sem link do Aviasales: Google Flights passa a ser o primeiro botão
        m = voos.mensagens([dict(f, link="")], CFG)[0]
        self.assertEqual([a["label"] for a in m["actions"]], ["Google Flights", "Skyscanner", "Trip.com"])
        self.assertIn("Kayak: ", m["message"])

    def test_muito_barato_na_tailandia_e_urgente(self):
        m = voos.mensagens(voos.filtrar([bilhete(preco=320)], CFG, HOJE), CFG)
        self.assertEqual(m[0]["priority"], 5)

    def test_datas_em_meses_diferentes(self):
        f = voos.filtrar([bilhete(ida="2026-11-25", volta="2026-12-09")], CFG, HOJE)
        self.assertIn("25 nov – 9 dez", voos.mensagens(f, CFG)[0]["message"])

    def test_resumo_quando_ha_muitos(self):
        destinos = ["BKK", "HKT", "KBV", "USM", "DAD", "DPS", "CEB", "LGK"]
        f = voos.filtrar([bilhete(d=d, preco=300 + i) for i, d in enumerate(destinos)], CFG, HOJE)
        m = voos.mensagens(f, CFG, maximo=6)
        self.assertEqual(len(m), 6)
        self.assertIn("Mais 3 destinos", m[-1]["title"])

    def test_json_do_ntfy(self):
        enviado = {}

        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def falso(req, timeout):
            enviado["url"] = req.full_url
            enviado["corpo"] = json.loads(req.data.decode("utf-8"))
            return Resp()

        with mock.patch("urllib.request.urlopen", falso):
            voos.enviar({"title": "🇹🇭 Teste ção", "message": "olá"}, "meu-canal")
        self.assertEqual(enviado["url"], "https://ntfy.sh/")
        self.assertEqual(enviado["corpo"]["topic"], "meu-canal")
        self.assertEqual(enviado["corpo"]["title"], "🇹🇭 Teste ção")


class Execucao(unittest.TestCase):
    def correr(self, estado, bilhetes, erros=(), simular=False):
        enviados = []
        voos.correr(CFG, estado, "tok", "canal", hoje=HOJE, simular=simular,
                    buscar=lambda c, t, h, log: (list(bilhetes), list(erros)),
                    mandar=lambda m, t, s: enviados.append(m), log=lambda *a: None,
                    companhias=lambda log: None)
        return enviados

    def test_fluxo_completo(self):
        estado = {}
        b = [bilhete(preco=350), bilhete(d="DPS", preco=500)]
        self.assertEqual(len(self.correr(estado, b)), 1)
        self.assertIn("LIS-BKK-2026-11-10-2026-11-24", estado["avisados"])
        self.assertEqual(self.correr(estado, b), [])          # segunda vez: nada
        self.assertEqual(len(self.correr(estado, [bilhete(preco=300)])), 1)  # desceu

    def test_simular_nao_grava(self):
        estado = {}
        self.assertEqual(len(self.correr(estado, [bilhete()], simular=True)), 0)
        self.assertEqual(estado.get("avisados", {}), {})

    def test_falha_total_avisa_uma_vez_por_dia(self):
        estado = {}
        m = self.correr(estado, [], erros=["LIS→BKK: token recusado (401)"])
        self.assertEqual(len(m), 1)
        self.assertIn("sem dados", m[0]["title"])
        self.assertEqual(self.correr(estado, [], erros=["x"]), [])

    def test_ntfy_em_baixo_nao_marca_como_avisado(self):
        estado = {}

        def falha(m, t, s):
            raise RuntimeError("ntfy em baixo")

        with self.assertRaises(RuntimeError):
            voos.correr(CFG, estado, "tok", "canal", hoje=HOJE,
                        buscar=lambda c, t, h, log: ([bilhete()], []), mandar=falha,
                        log=lambda *a: None, companhias=lambda log: None)
        self.assertEqual(estado.get("avisados", {}), {})


class Registo(unittest.TestCase):
    def test_mais_baratos_ignora_o_limite_mas_mantem_as_regras(self):
        b = [bilhete(preco=520), bilhete(d="HKT", preco=450), bilhete(preco=300, volta="2026-11-12"),
             bilhete(d="MLE", preco=480)]
        cfg = json.loads(json.dumps(CFG))
        for d in cfg["destinos"]:
            if d["iata"] == "MLE":
                d["preco_max"] = 100
        r = voos.mais_baratos(b, cfg, HOJE)
        self.assertEqual([x["preco"] for x in r], [450, 480, 520])
        self.assertEqual(voos.resumo(r[0], cfg),
                         "450 € · Lisboa → Phuket · 10–24 nov (14 dias) · 1 escala · Emirates")
        self.assertEqual(voos.filtrar(b, cfg, HOJE), [])


class Companhias(unittest.TestCase):
    def setUp(self):
        self.antes = dict(voos.COMPANHIAS)

    def tearDown(self):
        voos.COMPANHIAS.clear()
        voos.COMPANHIAS.update(self.antes)

    def resp(self, corpo):
        class R:
            def read(s): return json.dumps(corpo).encode()
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return lambda req, timeout: R()

    def test_lista_do_travelpayouts(self):
        lista = [{"code": "XX", "name": "Outro nome", "name_translations": {"en": "Xpto Air"}},
                 {"code": "HU", "name_translations": {"en": "Hainan Airlines Holding Co"}},
                 {"code": None, "name": "sem código"}, "lixo"]
        with mock.patch("urllib.request.urlopen", self.resp(lista)):
            voos.carregar_companhias(log=lambda *a: None)
        self.assertEqual(voos.nome_cia("XX"), "Xpto Air")
        self.assertEqual(voos.nome_cia("HU"), "Hainan Airlines")  # a de reserva ganha (mais curta)
        self.assertEqual(voos.nome_cia("ZZ"), "ZZ")                # desconhecida: fica o código

    def test_sem_rede_usa_a_reserva(self):
        msgs = []

        def falha(req, timeout):
            raise OSError("sem rede")

        with mock.patch("urllib.request.urlopen", falha):
            voos.carregar_companhias(log=msgs.append)
        self.assertIn("reserva", msgs[0])
        self.assertEqual(voos.nome_cia("3U"), "Sichuan Airlines")

    def test_so_companhias_conhecidas_nao_e_lista_vazia(self):
        msgs = []
        with mock.patch("urllib.request.urlopen", self.resp([{"code": "EK", "name": "Emirates Group"}])):
            voos.carregar_companhias(log=msgs.append)
        self.assertEqual(msgs, [])
        self.assertEqual(voos.nome_cia("EK"), "Emirates")

    def test_formato_inesperado(self):
        msgs = []
        with mock.patch("urllib.request.urlopen", self.resp({"erro": 1})):
            voos.carregar_companhias(log=msgs.append)
        self.assertIn("reserva", msgs[0])


class Exemplo(unittest.TestCase):
    def test_envia_o_mais_barato_da_tailandia_sem_gravar(self):
        enviados = []
        b = [bilhete(d="DPS", preco=420), bilhete(d="BKK", preco=470), bilhete(d="BKK", preco=455,
             ida="2026-12-01", volta="2026-12-15"), bilhete(d="HKT", preco=490)]
        rc = voos.exemplo(CFG, "tok", "canal", hoje=HOJE, buscar=lambda c, t, h, log: (b, []),
                          mandar=lambda m, t, s: enviados.append(m), log=lambda *a: None,
                          companhias=lambda log: None)
        self.assertEqual(rc, 0)
        self.assertEqual(len(enviados), 1)
        self.assertTrue(enviados[0]["title"].startswith("(Exemplo) 🇹🇭 Banguecoque desde 455 €"))
        self.assertIn("470 €", enviados[0]["message"])
        self.assertEqual(enviados[0]["priority"], 3)

    def test_sem_precos(self):
        rc = voos.exemplo(CFG, "tok", "canal", hoje=HOJE, buscar=lambda c, t, h, log: ([], ["x"]),
                          mandar=lambda *a: self.fail("não devia enviar"), log=lambda *a: None)
        self.assertEqual(rc, 1)


class Api(unittest.TestCase):
    def test_pedidos_e_parametros(self):
        pedidos = []

        def falso(params, token, tentativas=3):
            pedidos.append(params)
            return [{"price": 1}] if params["destination"] == "BKK" else []

        with mock.patch.object(voos, "pedir", falso):
            b, e = voos.procurar(CFG, "tok", HOJE, pausa=0, log=lambda *a: None)
        n = len(CFG["origens"]) * len(CFG["destinos"])
        self.assertEqual(len(pedidos), n)
        self.assertEqual(len(b), len(CFG["origens"]))
        self.assertEqual(b[0]["_o"], "LIS")
        self.assertEqual(pedidos[0]["one_way"], "false")
        self.assertNotIn("departure_at", pedidos[0])

    def test_por_mes(self):
        cfg = dict(CFG, por_mes=True, partida_max_meses=3)
        self.assertEqual(voos.meses_a_pesquisar(cfg, date(2026, 11, 6)),
                         ["2026-11", "2026-12", "2027-01", "2027-02"])

    def test_token_errado_para_logo(self):
        def falso(params, token, tentativas=3):
            raise RuntimeError("token recusado (401) — confirma o TRAVELPAYOUTS_TOKEN")

        with mock.patch.object(voos, "pedir", falso):
            b, e = voos.procurar(CFG, "tok", HOJE, pausa=0, log=lambda *a: None)
        self.assertEqual((b, len(e)), ([], 1))

    def test_resposta_http(self):
        class Resp:
            def __init__(self, corpo): self.corpo = corpo
            def read(self): return json.dumps(self.corpo).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        vistos = []

        def falso(req, timeout):
            vistos.append(req)
            return Resp({"success": True, "data": [{"price": 333}], "currency": "eur"})

        with mock.patch("urllib.request.urlopen", falso):
            r = voos.pedir({"origin": "LIS", "destination": "BKK"}, "abc")
        self.assertEqual(r, [{"price": 333}])
        self.assertEqual(vistos[0].get_header("X-access-token"), "abc")
        self.assertIn("origin=LIS", vistos[0].full_url)

        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout: Resp({"success": False, "error": "mau"})):
            with self.assertRaises(RuntimeError):
                voos.pedir({}, "abc")


if __name__ == "__main__":
    unittest.main()
