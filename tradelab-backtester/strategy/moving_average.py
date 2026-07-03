"""
strategy/moving_average.py
==========================

Strategia: INCROCIO DI MEDIE MOBILI SEMPLICI (SMA crossover).

Idea di fondo (molto usata come esempio didattico):
  - Calcoliamo due medie mobili del prezzo di chiusura: una "veloce" (pochi
    giorni, reagisce in fretta) e una "lenta" (molti giorni, reagisce piano).
  - GOLDEN CROSS: la veloce supera la lenta -> il trend di breve sta battendo
    quello di lungo -> segnale di ACQUISTO (stiamo dentro, "long").
  - DEATH CROSS: la veloce scende sotto la lenta -> segnale di VENDITA/FLAT
    (usciamo, torniamo liquidi).
  Default classici: veloce = 50 giorni, lenta = 200 giorni.

PERCHÉ LE MEDIE MOBILI: il prezzo giornaliero è rumoroso. La media mobile lo
"liscia" e mette in evidenza la tendenza di fondo. Confrontare due medie di
diversa lunghezza è un modo semplice per stimare se la tendenza recente è più
forte (o più debole) di quella di lungo periodo.

ATTENZIONE AL LOOK-AHEAD BIAS (il punto più importante di questo file):
  Il "look-ahead bias" è l'errore di usare, per decidere l'operazione di oggi,
  un'informazione che nella realtà avremmo conosciuto solo DOPO. È l'errore che
  gonfia i backtest e li rende bugiardi. Qui lo preveniamo in modo esplicito:
  la posizione che TENIAMO in un dato giorno dipende SOLO dal segnale calcolato
  con i dati fino al giorno PRECEDENTE (vedi la riga con .shift(1) più sotto).
"""

from __future__ import annotations

import pandas as pd


class StrategyError(Exception):
    """Sollevata quando i parametri della strategia o i dati non sono validi."""


def moving_average_crossover(
    prices: pd.DataFrame,
    fast_window: int = 50,
    slow_window: int = 200,
) -> pd.DataFrame:
    """
    Genera i segnali dell'incrocio di medie mobili.

    Parametri
    ---------
    prices : DataFrame
        Deve contenere almeno la colonna 'close' (dal loader).
    fast_window, slow_window : int
        Lunghezze (in giorni di borsa) delle due medie. Sono PARAMETRI: cambiarli
        è tutto ciò che serve per sperimentare altre combinazioni (es. 20/100).

    Ritorna
    -------
    Una COPIA del DataFrame in ingresso con quattro colonne aggiunte:
        - sma_fast : media mobile veloce
        - sma_slow : media mobile lenta
        - signal   : 0/1, lo stato DESIDERATO in base alla chiusura di OGGI
                     (1 = vorremmo essere investiti, 0 = vorremmo essere liquidi)
        - position : 0/1, lo stato che TENIAMO davvero oggi. È `signal` ritardato
                     di un giorno -> è qui che evitiamo il look-ahead bias.

    Solleva
    -------
    StrategyError : parametri incoerenti o dati insufficienti.
    """
    # --- Validazione dei parametri (errori leggibili) ---------------------
    if fast_window < 1 or slow_window < 1:
        raise StrategyError("Le finestre delle medie devono essere numeri interi positivi.")
    if fast_window >= slow_window:
        raise StrategyError(
            f"La media veloce ({fast_window}) deve essere PIÙ CORTA della lenta "
            f"({slow_window}), altrimenti l'incrocio non ha senso."
        )
    if "close" not in prices.columns:
        raise StrategyError("Il DataFrame dei prezzi non contiene la colonna 'close'.")
    if len(prices) < slow_window:
        raise StrategyError(
            f"Dati insufficienti: servono almeno {slow_window} giorni per calcolare "
            f"la media lenta, ma ne ho solo {len(prices)}. Allunga l'intervallo di date."
        )

    df = prices.copy()

    # --- Calcolo delle due medie mobili -----------------------------------
    # rolling(N).mean() = media degli ultimi N valori di chiusura, ricalcolata
    # ogni giorno. I primi N-1 valori sono NaN perché non c'è ancora una
    # finestra completa: questo "warmup" è normale e lo gestiamo sotto.
    df["sma_fast"] = df["close"].rolling(window=fast_window).mean()
    df["sma_slow"] = df["close"].rolling(window=slow_window).mean()

    # --- Segnale "grezzo": stato desiderato in base alla chiusura di OGGI --
    # Vogliamo essere long quando la veloce sta sopra la lenta.
    # Nota tecnica utile: un confronto con un NaN (es. durante il warmup, quando
    # sma_slow non esiste ancora) restituisce False. Quindi durante il warmup
    # `signal` è automaticamente 0 (liquidi): non entriamo finché non abbiamo
    # abbastanza storia per calcolare ENTRAMBE le medie. Scelta prudente e onesta.
    regime = df["sma_fast"] > df["sma_slow"]
    df["signal"] = regime.astype(int)

    # --- ANTI LOOK-AHEAD BIAS: qui e solo qui -----------------------------
    # Il segnale `signal` di oggi usa la chiusura di OGGI. Ma nella realtà la
    # chiusura di oggi la conosco solo a mercato chiuso: non posso usarla per
    # comprare "durante" oggi. Quindi la posizione che effettivamente TENIAMO
    # oggi è quella decisa IERI. .shift(1) sposta il segnale in avanti di un
    # giorno: position[t] = signal[t-1]. Così il rendimento di oggi viene
    # guadagnato con una posizione decisa PRIMA di vedere il prezzo di oggi.
    # Senza questo shift il backtest "vedrebbe il futuro" e sarebbe falsato.
    df["position"] = df["signal"].shift(1).fillna(0).astype(int)

    return df


def crossover_events(strategy_df: pd.DataFrame) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """
    Restituisce le date dei golden cross e dei death cross, utili per annotarle
    sui grafici e per capire la strategia.

    Sono i giorni in cui `signal` CAMBIA (in base alla chiusura di quel giorno):
      - golden cross: signal passa da 0 a 1 (diff = +1)
      - death  cross: signal passa da 1 a 0 (diff = -1)

    NB: l'OPERAZIONE vera avviene il giorno dopo (per via dello shift anti
    look-ahead): vedo l'incrocio a fine giornata e agisco alla seduta seguente.
    """
    change = strategy_df["signal"].diff()
    golden = strategy_df.index[change == 1]
    death = strategy_df.index[change == -1]
    return golden, death


# ---------------------------------------------------------------------------
# Demo / test rapido:  python -m strategy.moving_average
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from data.loader import load_price_data

    prices = load_price_data(source="synthetic", period_years=5)
    strat = moving_average_crossover(prices, fast_window=50, slow_window=200)

    golden, death = crossover_events(strat)
    print(f"Righe totali: {len(strat)}")
    print(f"Giorni investiti (position==1): {(strat['position'] == 1).sum()}")
    print(f"Golden cross: {len(golden)} | Death cross: {len(death)}")

    # Verifica anti look-ahead: position deve essere ESATTAMENTE signal ritardato
    # di 1 giorno. Se questa uguaglianza fallisse, avremmo un bug pericoloso.
    check = (strat["position"].iloc[1:].values == strat["signal"].shift(1).fillna(0).astype(int).iloc[1:].values).all()
    print(f"Anti look-ahead OK (position == signal ritardato): {check}")
    print("\nPrime righe con segnale attivo:")
    with_pos = strat[strat["position"] == 1]
    print(with_pos[["close", "sma_fast", "sma_slow", "signal", "position"]].head(3).to_string())
