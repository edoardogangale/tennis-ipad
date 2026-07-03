"""
data/loader.py
==============

Responsabilità di questo modulo: procurarsi i prezzi storici di un asset e
restituirli in un formato "pulito" e standard che tutti gli altri moduli
(strategia, motore, metriche, grafici) possono usare senza sorprese.

IDEA CHIAVE (utile anche per la roadmap futura): il resto del programma NON
deve sapere DA DOVE arrivano i dati. Qui dentro isoliamo la sorgente:
    - "yfinance"  -> dati reali scaricati da Yahoo Finance (default)
    - "csv"       -> un file salvato in precedenza (cache offline)
    - "synthetic" -> dati FINTI generati al volo, per provare tutto offline
Domani potremo aggiungere "from_broker_api" o "from_database" senza toccare
gli altri moduli. Questa è la "separazione delle responsabilità".

CONTRATTO (lo schema del DataFrame restituito, su cui contano gli altri moduli):
    - indice: DatetimeIndex giornaliero, ordinato, senza duplicati e senza
      fuso orario. Contiene SOLO i giorni di borsa: weekend e festivi non
      esistono proprio come righe (il mercato era chiuso).
    - colonne OHLCV (minuscole, sempre uguali): open, high, low, close, volume
    - colonna calcolata: log_return  (rendimento logaritmico giornaliero)
"""

from __future__ import annotations

import warnings
from datetime import datetime

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Eccezione dedicata
# ---------------------------------------------------------------------------
# Usare un'eccezione "parlante" rende la gestione errori leggibile: chi chiama
# il loader può distinguere "non sono riuscito a ottenere dati validi"
# (DataError) da un normale bug di programmazione (es. TypeError). Così in
# main.py potremo mostrare un messaggio chiaro all'utente invece di un crash.
class DataError(Exception):
    """Sollevata quando non riusciamo a ottenere dati storici validi."""


# Le uniche colonne che ci interessano, nell'ordine in cui le vogliamo.
# Per la strategia di base serve solo 'close', ma teniamo tutto l'OHLCV per
# poter aggiungere in futuro strategie che usano massimi/minimi/volume.
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Funzione pubblica principale: il "punto di ingresso" del modulo
# ---------------------------------------------------------------------------
def load_price_data(
    ticker: str = "SPY",
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    period_years: int = 10,
    source: str = "yfinance",
    csv_path: str | None = None,
) -> pd.DataFrame:
    """
    Restituisce i prezzi storici PULITI e con i rendimenti già calcolati.

    Parametri
    ---------
    ticker : str
        Simbolo dell'asset (es. "SPY", "AAPL"). Usato solo dalla sorgente
        yfinance e nei messaggi d'errore.
    start, end : str | datetime | None
        Estremi dell'intervallo (inclusi). Se None vengono dedotti:
        end = oggi, start = end - `period_years` anni.
    period_years : int
        Quanti anni di storico vogliamo quando start non è specificato.
    source : {"yfinance", "csv", "synthetic"}
        Da dove prendere i dati. yfinance = reali; csv = file salvato;
        synthetic = dati finti per provare offline.
    csv_path : str | None
        Percorso del CSV quando source="csv".

    Ritorna
    -------
    pandas.DataFrame conforme al CONTRATTO descritto in cima al file.

    Solleva
    -------
    DataError : se le date non sono valide, la sorgente è sconosciuta o non
        otteniamo dati utilizzabili (ticker errato, intervallo vuoto, rete
        bloccata, file mancante...).
    """
    # 1) Risolviamo l'intervallo di date (con validazione: date sensate?).
    start_ts, end_ts = _resolve_date_range(start, end, period_years)

    # 2) Recuperiamo i dati GREZZI dalla sorgente scelta.
    if source == "yfinance":
        raw = _from_yfinance(ticker, start_ts, end_ts)
    elif source == "csv":
        if not csv_path:
            raise DataError("Con source='csv' devi passare anche csv_path.")
        raw = _from_csv(csv_path)
    elif source == "synthetic":
        raw = generate_synthetic_ohlcv(start_ts, end_ts)
    else:
        raise DataError(
            f"Sorgente sconosciuta: {source!r}. "
            f"Valori ammessi: 'yfinance', 'csv', 'synthetic'."
        )

    # 3) Normalizziamo e puliamo (colonne, indice, dati mancanti).
    clean = _normalize_ohlcv(raw)

    # 4) Ritagliamo all'intervallo richiesto (per csv/synthetic potrebbe
    #    contenere più dati del necessario; per yfinance è già a posto).
    clean = clean.loc[(clean.index >= start_ts) & (clean.index <= end_ts)]

    # 5) Controlliamo di avere davvero qualcosa con cui lavorare.
    _validate_not_empty(clean, ticker)

    # 6) Aggiungiamo i rendimenti logaritmici.
    clean = add_log_returns(clean)

    return clean


# ---------------------------------------------------------------------------
# Gestione dell'intervallo di date
# ---------------------------------------------------------------------------
def _resolve_date_range(start, end, period_years: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Trasforma start/end (che possono essere None, stringhe o date) in due
    Timestamp validi, applicando i default e controllando che abbiano senso.
    """
    try:
        end_ts = pd.Timestamp(end) if end is not None else pd.Timestamp.today().normalize()
        if start is not None:
            start_ts = pd.Timestamp(start)
        else:
            # pd.DateOffset(years=...) sottrae esattamente N anni di calendario.
            start_ts = end_ts - pd.DateOffset(years=period_years)
    except (ValueError, TypeError) as exc:
        # to-Timestamp fallisce su stringhe non interpretabili ("ieri", "2020-13-99"...)
        raise DataError(
            f"Date non valide (start={start!r}, end={end!r}): {exc}"
        ) from exc

    if start_ts >= end_ts:
        raise DataError(
            f"Intervallo vuoto o invertito: la data di inizio "
            f"({start_ts.date()}) deve precedere quella di fine ({end_ts.date()})."
        )
    return start_ts, end_ts


# ---------------------------------------------------------------------------
# Sorgenti dati
# ---------------------------------------------------------------------------
def _from_yfinance(ticker: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    """
    Scarica i dati reali da Yahoo Finance.

    Nota tecnica: importiamo yfinance QUI dentro (non in cima al file) così il
    progetto resta caricabile anche se yfinance non fosse installato o se si
    usano solo le sorgenti 'csv'/'synthetic'.
    """
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - dipende dall'ambiente
        raise DataError(
            "yfinance non è installato. Esegui: pip install -r requirements.txt"
        ) from exc

    # yfinance stampa messaggi molto rumorosi quando la rete fallisce: li
    # silenziamo, tanto l'esito lo gestiamo noi con un DataError leggibile.
    import logging
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    # auto_adjust=True -> usiamo i prezzi AGGIUSTATI per dividendi e split.
    # Perché è importante in un backtest: nel giorno dello stacco del dividendo
    # il prezzo "salta" giù di colpo; senza aggiustamento questo apparirebbe
    # come una perdita finta. Stesso discorso per gli split azionari. I prezzi
    # aggiustati eliminano questi salti artificiali e danno rendimenti corretti.
    df = yf.download(
        ticker,
        start=start_ts,
        end=end_ts + pd.Timedelta(days=1),  # +1 perché 'end' di yfinance è ESCLUSIVO
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if df is None or len(df) == 0:
        # Caso tipico: ticker inesistente -> yfinance ritorna un DataFrame vuoto.
        raise DataError(
            f"yfinance non ha restituito dati per '{ticker}'. Possibili cause: "
            f"il ticker non esiste, l'intervallo di date è vuoto, oppure (come in "
            f"questo ambiente) gli host di Yahoo Finance non sono nella allowlist "
            f"di rete. Vedi le note nel README."
        )
    return df


def _from_csv(csv_path: str) -> pd.DataFrame:
    """
    Legge i dati da un CSV salvato in precedenza (utile come cache offline o
    per condividere un dataset esatto e rendere il backtest riproducibile).
    Si assume che la prima colonna sia la data (l'indice).
    """
    try:
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    except FileNotFoundError as exc:
        raise DataError(f"File CSV non trovato: {csv_path}") from exc
    except Exception as exc:  # CSV malformato, ecc.
        raise DataError(f"Impossibile leggere il CSV {csv_path}: {exc}") from exc
    return df


def generate_synthetic_ohlcv(
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    seed: int = 42,
    start_price: float = 100.0,
) -> pd.DataFrame:
    """
    Genera prezzi FINTI ma realistici, per provare tutta la pipeline OFFLINE
    (quando Yahoo Finance non è raggiungibile). ATTENZIONE: NON sono dati reali
    e NON vanno usati per trarre conclusioni — servono solo a verificare che il
    codice giri end-to-end.

    Modello usato: "moto browniano geometrico" (GBM). È il modello classico con
    cui si descrivono i prezzi azionari: il LOGARITMO del prezzo fa una
    passeggiata casuale con una piccola tendenza (drift) più rumore (volatilità).
    In formula, il rendimento log giornaliero è:  r_t = drift + rumore_normale.
    Il prezzo è poi  P_t = P_0 * exp(somma dei r_t).

    Trucco didattico: alterniamo "regimi" di drift positivo e negativo (fasi
    toro/orso) così le medie mobili si incrociano davvero e la strategia ha
    qualcosa da fare. Con seed fisso i dati sono riproducibili.
    """
    rng = np.random.default_rng(seed)

    # bdate_range = solo giorni feriali (lun-ven), imitando il calendario di borsa.
    # Non modelliamo i singoli festivi: ai fini di una demo non cambia nulla.
    dates = pd.bdate_range(start=start_ts, end=end_ts)
    n = len(dates)
    if n < 2:
        raise DataError("Intervallo troppo corto per generare dati sintetici.")

    # Drift = tendenza di fondo LEGGERMENTE POSITIVA (come i mercati azionari nel
    # lungo periodo) + un'alternanza di "regimi" toro/orso che fa incrociare le
    # medie mobili. Così la serie sale nel tempo (come farebbe un indice reale)
    # ma con fasi su e giù, che è proprio ciò che serve per provare la strategia.
    base_drift = 0.0004        # ~ +10% annuo di tendenza di fondo
    regime_amplitude = 0.0008  # oscillazione toro/orso attorno alla tendenza
    drift = np.empty(n)
    regime_len = max(60, n // 8)  # ~8 fasi alternate sull'intero periodo
    sign = 1.0
    for i in range(0, n, regime_len):
        drift[i : i + regime_len] = base_drift + sign * regime_amplitude
        sign *= -1.0

    daily_vol = 0.01  # ~1% al giorno -> ~16% annuo (sqrt(252)*1%), realistico
    daily_log_return = drift + rng.normal(0.0, daily_vol, size=n)

    # Dal rendimento log al prezzo: P_t = P_0 * exp(cumsum dei rendimenti log).
    close = start_price * np.exp(np.cumsum(daily_log_return))
    close = pd.Series(close, index=dates)

    # Costruiamo un OHLC plausibile attorno alla chiusura (solo estetico).
    open_ = close.shift(1).fillna(start_price)
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.005, size=n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.005, size=n))
    volume = rng.integers(1_000_000, 5_000_000, size=n)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )
    df.index.name = "date"
    return df


# ---------------------------------------------------------------------------
# Pulizia / normalizzazione
# ---------------------------------------------------------------------------
def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Porta un DataFrame "grezzo" (da qualsiasi sorgente) al nostro CONTRATTO:
    colonne minuscole OHLCV, indice di date ordinato e pulito, niente NaN sui
    prezzi di chiusura.
    """
    df = df.copy()

    # 1) yfinance, per un singolo ticker, può restituire colonne "a due livelli"
    #    tipo ('Close', 'SPY'). Teniamo solo il nome del campo ('Close').
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 2) Nomi colonna uniformi: minuscolo, spazi -> underscore.
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    # 'adj_close' non serve: con auto_adjust=True la colonna 'close' è già aggiustata.
    df = df.drop(columns=[c for c in ["adj_close"] if c in df.columns])

    # 3) Indice = date vere, senza fuso orario, ordinate, senza duplicati.
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        # Lavoriamo a livello di "giorno": il fuso orario non ci interessa.
        idx = idx.tz_localize(None)
    df.index = idx
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index.name = "date"

    # 4) Teniamo solo le colonne OHLCV che esistono davvero.
    keep = [c for c in OHLCV_COLUMNS if c in df.columns]
    if "close" not in keep:
        raise DataError("I dati non contengono una colonna 'close': impossibile procedere.")
    df = df[keep]

    # 5) PULIZIA dei dati mancanti.
    #    - Una riga senza prezzo di chiusura è inutilizzabile: la eliminiamo.
    #    - NON facciamo forward-fill dei prezzi: "inventare" un prezzo creerebbe
    #      un rendimento finto. I festivi, comunque, non sono righe (il mercato
    #      era chiuso): non c'è nulla da riempire.
    n_before = len(df)
    df = df.dropna(subset=["close"])
    n_dropped = n_before - len(df)
    if n_dropped:
        warnings.warn(
            f"Pulizia dati: rimosse {n_dropped} righe prive di prezzo di chiusura.",
            stacklevel=2,
        )

    # Il volume mancante non è critico per la strategia: lo mettiamo a 0.
    if "volume" in df.columns:
        df["volume"] = df["volume"].fillna(0)

    return df


def add_log_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggiunge la colonna 'log_return' = rendimento logaritmico giornaliero.

    Formula:  r_t = ln(P_t / P_{t-1}) = ln(P_t) - ln(P_{t-1})

    Perché logaritmico e non semplice ((P_t / P_{t-1}) - 1)?
      1) È ADDITIVO nel tempo: la somma dei log-rendimenti di N giorni è uguale
         al log-rendimento dell'intero periodo. Così la composizione (che con i
         rendimenti semplici è una catena di moltiplicazioni) diventa una somma
         — molto più comoda per annualizzare e per i calcoli statistici.
      2) È più SIMMETRICO: con i rendimenti semplici un -50% e un +50% non si
         annullano; con i log sono trattati in modo più coerente. Inoltre i
         log-rendimenti sono spesso più vicini a una distribuzione normale,
         ipotesi su cui si appoggiano molte metriche (es. lo Sharpe ratio).
    """
    df = df.copy()
    # shift(1) prende il prezzo del giorno PRECEDENTE: questo è già un primo
    # accorgimento contro il "guardare nel futuro" — il rendimento di oggi usa
    # la chiusura di ieri, non quella di domani.
    df["log_return"] = np.log(df["close"] / df["close"].shift(1))
    # La prima riga non ha un giorno precedente, quindi log_return = NaN. È
    # corretto: chi calcola statistiche dovrà fare .dropna() su questa colonna.
    return df


# ---------------------------------------------------------------------------
# Validazione finale
# ---------------------------------------------------------------------------
def _validate_not_empty(df: pd.DataFrame, ticker: str) -> None:
    """Controlla che dopo la pulizia rimangano dati sufficienti."""
    if df is None or len(df) == 0:
        raise DataError(
            f"Nessun dato utilizzabile per '{ticker}' nell'intervallo richiesto "
            f"(dopo la pulizia il dataset è vuoto)."
        )
    if len(df) < 30:
        # Non è un errore bloccante, ma avvisiamo: con pochissime righe le medie
        # mobili lunghe (es. 200 giorni) non potranno nemmeno essere calcolate.
        warnings.warn(
            f"Solo {len(df)} righe disponibili: potrebbero non bastare per medie "
            f"mobili lunghe (es. SMA a 200 giorni).",
            stacklevel=2,
        )


# ---------------------------------------------------------------------------
# Demo / test rapido del modulo da riga di comando:  python -m data.loader
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pd.set_option("display.width", 120)

    print("=" * 70)
    print("TEST 1 — Dati SINTETICI (funziona sempre, anche offline)")
    print("=" * 70)
    df_syn = load_price_data(source="synthetic", period_years=3)
    print(df_syn.head(3).to_string())
    print("...")
    print(df_syn.tail(3).to_string())
    print(
        f"\nRighe: {len(df_syn)} | "
        f"NaN attesi in log_return (solo la 1a riga): {int(df_syn['log_return'].isna().sum())} | "
        f"Colonne: {list(df_syn.columns)}"
    )

    print("\n" + "=" * 70)
    print("TEST 2 — Dati REALI da yfinance (richiede accesso di rete a Yahoo)")
    print("=" * 70)
    try:
        df_real = load_price_data("SPY", period_years=1, source="yfinance")
        print(df_real.tail(3).to_string())
        print(f"\nRighe reali scaricate: {len(df_real)}")
    except DataError as exc:
        print("Atteso in ambienti senza accesso a Yahoo Finance:")
        print(f"  -> {exc}")
