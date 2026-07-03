# TradeLab — Backtester didattico

Uno strumento **didattico** in Python per imparare a **validare strategie di
trading sui dati storici con la matematica**, non con le sensazioni.

Scarica i prezzi di un asset, applica una strategia a **incrocio di medie
mobili**, simula l'andamento del capitale con **costi realistici**, calcola le
**metriche di performance** standard e le confronta con il semplice **buy &
hold**. Il tutto spiegato riga per riga.

> ⚠️ **Solo a scopo educativo.** Nessuna connessione a broker reali, nessun
> ordine vero, nessun consiglio finanziario. I risultati passati non predicono
> quelli futuri. Serve a capire *come si validano* le strategie.

---

## Cosa fa (le 6 fasi)

1. **Dati** — scarica i prezzi giornalieri da Yahoo Finance (`yfinance`), calcola
   i **rendimenti logaritmici**, pulisce dati mancanti e festivi.
2. **Strategia** — incrocio di medie mobili semplici **SMA 50 / 200** (parametri
   modificabili). *Golden cross* → compra, *death cross* → vendi. Con
   prevenzione esplicita del **look-ahead bias**.
3. **Motore** — simula i trade giorno per giorno con **commissione + spread**,
   tiene il **registro di ogni operazione** (data, tipo, prezzo, size, P&L).
4. **Metriche** — CAGR, volatilità annua, **Sharpe ratio**, **max drawdown** e
   sua durata, **win rate**, **profit factor**, numero operazioni, e confronto
   diretto con il buy & hold. Ogni metrica è spiegata nell'output.
5. **Grafici** — curva del capitale (strategia vs buy & hold), drawdown nel
   tempo, punti di entrata/uscita, prezzo con medie e incroci. Salvati in
   `output/` come PNG.
6. **Validazione onesta** — divisione **train/test (70/30)** e confronto delle
   metriche tra le due parti, con avviso se divergono (sintomo di overfitting /
   dipendenza dal periodo).

---

## Struttura del progetto

```
tradelab-backtester/
├── data/
│   └── loader.py          # scarica e pulisce i dati (yfinance / csv / synthetic)
├── strategy/
│   └── moving_average.py  # logica dell'incrocio di medie, anti look-ahead
├── engine/
│   └── backtester.py      # simula i trade, applica i costi, registro operazioni
├── metrics/
│   ├── performance.py     # calcolo e spiegazione delle metriche
│   └── validation.py      # validazione onesta train/test
├── reports/
│   └── plotter.py         # genera i grafici PNG
├── output/                # qui finiscono i grafici e i log (generati)
├── main.py                # entry point: orchestra tutte le fasi
├── requirements.txt
└── README.md
```

I moduli comunicano con **interfacce chiare** (il loader restituisce sempre lo
stesso schema di DataFrame; la strategia produce una colonna `position`; il
motore restituisce un `BacktestResult`). Questo rende facile, in futuro,
aggiungere nuove strategie o nuove sorgenti dati senza toccare il resto.

---

## Installazione

Serve **Python 3.10+**. Consigliato un ambiente virtuale:

```bash
cd tradelab-backtester
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Esecuzione

### Esempio base (dati reali)

```bash
python main.py --ticker SPY --years 10
```

### Esempio offline (dati sintetici, non serve la rete)

```bash
python main.py --source synthetic --years 10
```

### Sperimentare altri parametri

```bash
# Medie più corte e commissioni più basse su un'altra azione
python main.py --ticker AAPL --fast 20 --slow 100 --commission 0.0005

# Capitale iniziale diverso e altra divisione train/test
python main.py --ticker MSFT --capital 25000 --train-frac 0.6
```

### Tutte le opzioni

```bash
python main.py --help
```

| Opzione | Default | Significato |
|---|---|---|
| `--ticker` | `SPY` | Simbolo dell'asset |
| `--start` / `--end` | (auto) | Intervallo date `YYYY-MM-DD` |
| `--years` | `10` | Anni di storico se `--start` non è dato |
| `--source` | `yfinance` | `yfinance` (reale), `synthetic` (finto), `csv` (file) |
| `--csv-path` | — | Percorso del CSV quando `--source csv` |
| `--fast` / `--slow` | `50` / `200` | Giorni delle due medie mobili |
| `--capital` | `10000` | Capitale iniziale |
| `--commission` | `0.001` | Commissione per operazione (0,1%) |
| `--spread` | `0.0005` | Spread stimato (0,05%) |
| `--risk-free` | `0.02` | Tasso privo di rischio annuo (per lo Sharpe) |
| `--train-frac` | `0.70` | Quota di dati per il train |
| `--output-dir` | `output` | Cartella dei grafici |
| `--no-plots` | off | Salta la generazione dei grafici |

---

## ⚠️ Nota sull'accesso di rete a Yahoo Finance

`yfinance` scarica i dati dagli host `query1.finance.yahoo.com` e
`query2.finance.yahoo.com`. In **alcuni ambienti** (per esempio sessioni cloud
con una *allowlist* di rete restrittiva) questi host sono bloccati e vedrai un
errore tipo `403 Host not in allowlist`. In quel caso hai tre strade:

1. **Esegui il progetto sul tuo computer**, dove `yfinance` funziona senza
   modifiche.
2. **Aggiungi gli host di Yahoo alla allowlist** dell'ambiente, se ne gestisci
   la configurazione di rete.
3. **Usa la sorgente offline** per esercitarti sulla logica:
   `python main.py --source synthetic`. I dati sintetici sono **finti** (una
   simulazione realistica) e servono solo a far girare la pipeline, non a trarre
   conclusioni sui mercati reali.

---

## Concetti chiave (per chi impara)

- **Rendimenti logaritmici**: `ln(P_t / P_{t-1})`. Sono *additivi nel tempo*
  (la composizione diventa una somma) e statisticamente più "educati" dei
  rendimenti semplici. Vedi `data/loader.py`.
- **Look-ahead bias**: l'errore di usare per l'operazione di oggi
  un'informazione conosciuta solo dopo. Lo evitiamo con `position =
  signal.shift(1)`: la posizione di oggi è decisa con i dati fino a **ieri**.
  Vedi `strategy/moving_average.py`.
- **Costi realistici**: ogni operazione paga `commissione + spread/2` per gamba.
  Un backtest che ignora i costi promette rendimenti che non esistono. Vedi
  `engine/backtester.py`.
- **Sharpe ratio**: rendimento *in eccesso* rispetto al tasso privo di rischio,
  per unità di rischio. `>1` buono, `>2` ottimo, `<0` peggio del non rischiare.
- **Max drawdown**: la peggior perdita da un massimo precedente — spesso è ciò
  che fa mollare gli investitori nella realtà.
- **Validazione train/test**: se la strategia va bene nella prima parte della
  storia ma male nella seconda, diffida. Vedi `metrics/validation.py`.

---

## Output generato

Dopo un'esecuzione trovi in `output/`:

- `backtest_equity.png` — capitale strategia vs buy & hold + drawdown + entrate/uscite
- `price_and_signals.png` — prezzo, medie mobili e incroci (golden/death cross)
- `operations_log.csv` — il registro completo di ogni operazione

---

## Roadmap (idee per il futuro)

Il codice è pensato per essere esteso senza riscritture:

1. Altre strategie oltre all'incrocio di medie (basta produrre una colonna
   `position`: il motore e le metriche restano identici).
2. Paper trading con dati quasi in tempo reale (nuova sorgente nel `loader`).
3. Dashboard interattiva sopra lo stesso motore di calcolo.

---

## Licenza / disclaimer

Progetto didattico. Nessuna garanzia. Non è consulenza finanziaria. Usa i
risultati per **imparare il metodo**, non per investire denaro reale.
