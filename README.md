# Alertas de voos ✈️🏝️

Vigia voos de **ida e volta** de Lisboa, Porto, Faro, Madrid e Barcelona para a **Tailândia** e para as praias do Sudeste Asiático (Vietname, Bali, Filipinas, Malásia, Sri Lanka, Maldivas). Quando aparece um voo **abaixo de 400 €**, recebes uma notificação no telemóvel com:

- o preço, as datas e a companhia aérea;
- até 5 datas para esse destino;
- dois botões: **Aviasales**, para comprar, e **Google Flights**, para confirmar o preço.

Corre sozinho no GitHub de 3 em 3 horas. Não precisas de servidor e não pagas nada.

## Como funciona

1. `voos.py` pede à API do Aviasales (Travelpayouts, gratuita) os preços mais baratos de cada origem para cada destino.
2. Só ficam as viagens que cumprem as regras do `config.json`. Por omissão:
   - ida e volta até **400 €**;
   - entre **7 e 30 dias** de viagem;
   - partida daqui a 7 dias até 11 meses;
   - no máximo 2 escalas e 30 h de viagem em cada sentido.
3. Envia um aviso por destino, com a **Tailândia primeiro** e com prioridade alta. Se for mesmo muito barato (≤ 340 €), a prioridade é máxima e o telemóvel toca mesmo em silêncio, se o permitires na app.
4. O `estado.json` guarda o que já foi avisado, por isso o mesmo voo não volta a ser avisado. Só avisa outra vez se o preço descer mais 5 % ou mais.
5. Se a pesquisa falhar toda (token errado, API em baixo), recebes um aviso ⚠️, no máximo um por dia.

> Os preços vêm de pesquisas feitas no Aviasales nos últimos dias, por isso o preço pode já ter mudado. Confirma sempre antes de comprar: é para isso que serve o botão do Google Flights.

## Configurar (uma vez, ~10 minutos)

### 1. Token da API de voos (grátis)
1. Cria conta em <https://www.travelpayouts.com>.
2. No teu perfil, procura **API token** e copia-o.

### 2. App de notificações (ntfy)
1. Instala a app **ntfy** (App Store ou Google Play).
2. Toca em **+** e subscreve um canal com um nome difícil de adivinhar, por exemplo `voos-alex-k39xq2`. Quem souber o nome do canal consegue ler os avisos, por isso não uses um nome simples.

### 3. Segredos no GitHub
No repositório: **Settings → Secrets and variables → Actions → New repository secret**

| Nome | Valor |
|---|---|
| `TRAVELPAYOUTS_TOKEN` | o token do passo 1 |
| `NTFY_TOPICO` | o nome do canal do passo 2 |

### 4. Experimentar
Em **Actions → Alertas de voos → Run workflow**:
- **teste**: deve chegar logo uma notificação "Alertas de voos ligados".
- **simular**: procura os voos e mostra no registo o que avisaria, sem enviar nada.
- **procurar**: é o modo normal, que também corre sozinho de 3 em 3 horas.

## Mudar as regras (`config.json`)

| Campo | O que faz |
|---|---|
| `preco_max` | preço máximo de ida e volta (€) |
| `dias_min` / `dias_max` | duração da viagem |
| `partida_min_dias` / `partida_max_meses` | daqui a quanto tempo pode ser a partida |
| `escalas_max`, `horas_viagem_max` | escalas e horas de viagem por sentido |
| `realerta_descida_pct` | quanto tem de descer para voltar a avisar |
| `max_avisos_por_execucao` | se houver mais, o último aviso é um resumo |
| `origens` | aeroportos de partida (código IATA: nome) |
| `destinos` | lista de destinos: `"prioridade": true` = Tailândia; `"preco_max"` num destino muda o limite só para esse; `"ativo": false` desliga-o |
| `por_mes` | `true` faz uma pesquisa por mês: encontra mais datas, mas são 12× mais pedidos |

Para acrescentar uma origem (ex.: Lyon), junta `"LYS": "Lyon"` às `origens`.

## Correr no computador

```
TRAVELPAYOUTS_TOKEN=xxx python3 voos.py --simular
python3 -m unittest discover -s tests
```

Só usa Python (3.9 ou mais recente), sem nada para instalar.
