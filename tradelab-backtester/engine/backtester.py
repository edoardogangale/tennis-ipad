"""
engine/backtester.py
====================

Il motore di simulazione ("backtester"). Dato il segnale di posizione della
strategia (0 = liquidi, 1 = investito), ripercorre la storia giorno per giorno
e calcola come si sarebbe evoluto il capitale, applicando costi realistici e
registrando ogni operazione.

REGOLE DI SIMULAZIONE (come da specifica):
  - Si parte con un capitale iniziale in contanti (default 10.000).
  - Quando la posizione passa 0 -> 1 (segnale di acquisto): investiamo TUTTO
    il contante disponibile nell'asset, al prezzo di chiusura di quel giorno.
  - Quando passa 1 -> 0 (segnale di vendita): liquidiamo tutta la posizione e
    torniamo in contanti.
  - Ogni operazione paga un COSTO realistico (commissione + spread): niente
    backtest "puliti" che ignorano gli attriti reali del mercato.

PERCHÉ I COSTI CONTANO: nella realtà ogni compravendita ha una commissione e
paga lo "spread" (la differenza tra prezzo di acquisto e di vendita). Su una
strategia che opera spesso, questi piccoli attriti erodono i profitti. Un
backtest che li ignora promette rendimenti che non esistono.

PERCHÉ ESEGUIAMO ALLA CHIUSURA (e perché non c'è look-ahead): la strategia ha
già ritardato il segnale di un giorno (position[t] = signal[t-1]). Quindi la
decisione è stata presa alla chiusura di ieri e noi la eseguiamo alla chiusura
di oggi: un ritardo di esecuzione realistico, senza "vedere il futuro".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


class BacktestError(Exception):
    """Sollevata quando la simulazione non può partire (dati/parametri errati)."""


@dataclass
class BacktestResult:
    """
    Contenitore ordinato di tutto ciò che il backtest produce. Averlo come
    oggetto unico rende pulite le interfacce verso metriche e grafici.
    """
    equity: pd.Series          # valore del portafoglio (strategia), giorno per giorno
    strategy_df: pd.DataFrame  # prezzi + segnali + colonne di stato del motore
    trades: pd.DataFrame       # UN RIGA per operazione CHIUSA (round-trip) -> per le metriche
    operations: pd.DataFrame   # il "registro": ogni gamba BUY/SELL con data, prezzo, size, P&L
    buy_hold_equity: pd.Series # benchmark: comprato il 1° giorno e tenuto fino alla fine
    initial_capital: float
    params: dict = field(default_factory=dict)


def _leg_cost_fraction(commission: float, spread: float) -> float:
    """
    Costo per singola gamba (un acquisto OPPURE una vendita), come frazione del
    controvalore scambiato.

    Modello semplice ma onesto:
      - commissione: la paghi intera a ogni operazione (es. 0,1% = 0.001).
      - spread: è la distanza tra prezzo denaro/lettera. Ogni volta che entri o
        esci ne "attraversi" circa la metà (compri un po' più caro, vendi un po'
        più a buon mercato). Quindi per gamba conta spread/2.
    Costo per gamba = commissione + spread/2. Su un giro completo (compra+vendi)
    paghi quindi ~ 2*commissione + spread.
    """
    return commission + spread / 2.0


def run_backtest(
    strategy_df: pd.DataFrame,
    initial_capital: float = 10_000.0,
    commission: float = 0.001,   # 0,1% per operazione
    spread: float = 0.0005,      # 0,05% di spread stimato (metà per gamba)
) -> BacktestResult:
    """
    Simula la strategia e restituisce un BacktestResult.

    Il DataFrame in ingresso deve avere le colonne 'close' e 'position'
    (prodotte da strategy.moving_average.moving_average_crossover).
    """
    # --- Validazione ------------------------------------------------------
    for col in ("close", "position"):
        if col not in strategy_df.columns:
            raise BacktestError(f"Manca la colonna '{col}' necessaria alla simulazione.")
    if initial_capital <= 0:
        raise BacktestError("Il capitale iniziale deve essere positivo.")
    if len(strategy_df) == 0:
        raise BacktestError("Nessun dato da simulare.")

    leg_cost = _leg_cost_fraction(commission, spread)

    # --- Stato del portafoglio -------------------------------------------
    cash = float(initial_capital)   # contante disponibile
    shares = 0.0                    # quante unità dell'asset possediamo
    prev_position = 0               # posizione del giorno precedente (per rilevare i cambi)

    # Memoria dell'operazione aperta (per calcolare il P&L quando la chiudiamo)
    entry_price = None
    entry_date = None
    entry_capital = None            # contante impegnato al momento dell'acquisto
    entry_fee = 0.0

    equity_curve = []               # valore del portafoglio ogni giorno
    operations = []                 # registro gamba per gamba (BUY/SELL)
    trades = []                     # operazioni chiuse (round-trip) per le metriche

    # --- Ciclo giorno per giorno -----------------------------------------
    # Un ciclo esplicito è più lento di una versione "vettoriale", ma qui la
    # chiarezza vale più della velocità: si vede esattamente quando entra ed esce
    # il denaro, ed è facilissimo tenere il registro delle operazioni.
    for date, row in strategy_df.iterrows():
        price = float(row["close"])
        position = int(row["position"])

        # 1) ACQUISTO: la posizione passa da 0 a 1 -> investiamo tutto il contante.
        if position == 1 and prev_position == 0:
            fee = cash * leg_cost                 # costo dell'operazione
            invested = cash - fee                 # ciò che resta da investire davvero
            shares = invested / price             # unità comprate (frazionarie: è una simulazione)
            operations.append({
                "date": date, "type": "BUY", "price": price,
                "shares": shares, "fee": fee, "pnl": np.nan,  # il P&L si realizza solo alla vendita
                "cash_after": 0.0, "equity_after": shares * price,
            })
            # ricordiamo i dati dell'operazione aperta
            entry_price, entry_date, entry_capital, entry_fee = price, date, cash, fee
            cash = 0.0

        # 2) VENDITA: la posizione passa da 1 a 0 -> liquidiamo tutto.
        elif position == 0 and prev_position == 1:
            proceeds = shares * price             # incasso lordo dalla vendita
            fee = proceeds * leg_cost             # costo dell'operazione
            cash = proceeds - fee                 # incasso netto -> torna in contanti
            # P&L REALIZZATO del giro completo = contante finale - contante impegnato
            # all'ingresso (include già ENTRAMBI i costi, di acquisto e di vendita).
            pnl = cash - entry_capital
            trades.append({
                "entry_date": entry_date, "entry_price": entry_price,
                "exit_date": date, "exit_price": price,
                "shares": shares, "pnl": pnl,
                "return_pct": pnl / entry_capital if entry_capital else np.nan,
                "costs": entry_fee + fee,
            })
            operations.append({
                "date": date, "type": "SELL", "price": price,
                "shares": shares, "fee": fee, "pnl": pnl,
                "cash_after": cash, "equity_after": cash,
            })
            shares = 0.0
            entry_price = entry_date = entry_capital = None
            entry_fee = 0.0

        # 3) Valore del portafoglio a fine giornata = contante + valore delle azioni.
        #    (Se siamo liquidi, shares=0; se siamo investiti, cash=0.)
        equity_curve.append(cash + shares * price)
        prev_position = position

    equity = pd.Series(equity_curve, index=strategy_df.index, name="strategy_equity")

    # I DataFrame di output: se non ci sono operazioni restano vuoti ma con le
    # colonne giuste, così i moduli a valle non devono gestire casi speciali.
    trades_df = pd.DataFrame(trades, columns=[
        "entry_date", "entry_price", "exit_date", "exit_price",
        "shares", "pnl", "return_pct", "costs",
    ])
    operations_df = pd.DataFrame(operations, columns=[
        "date", "type", "price", "shares", "fee", "pnl", "cash_after", "equity_after",
    ])

    bh_equity = buy_and_hold_equity(strategy_df, initial_capital, commission, spread)

    return BacktestResult(
        equity=equity,
        strategy_df=strategy_df,
        trades=trades_df,
        operations=operations_df,
        buy_hold_equity=bh_equity,
        initial_capital=float(initial_capital),
        params={
            "commission": commission, "spread": spread,
            "leg_cost": leg_cost, "initial_capital": initial_capital,
        },
    )


def buy_and_hold_equity(
    prices: pd.DataFrame,
    initial_capital: float = 10_000.0,
    commission: float = 0.001,
    spread: float = 0.0005,
) -> pd.Series:
    """
    Il benchmark "compra e tieni": investiamo tutto il capitale il PRIMO giorno
    disponibile e non tocchiamo più nulla fino alla fine.

    È il metro di paragone più onesto: se una strategia attiva (con tutti i suoi
    costi e il suo stress) non batte questo semplice "compra e dimentica", allora
    non sta aggiungendo valore. Paghiamo un solo costo d'ingresso, perché anche
    il buy-and-holder deve comprare una volta (ma non vende mai, quindi niente
    costo d'uscita).
    """
    if "close" not in prices.columns or len(prices) == 0:
        raise BacktestError("Servono prezzi con colonna 'close' per il buy-and-hold.")

    leg_cost = _leg_cost_fraction(commission, spread)
    first_price = float(prices["close"].iloc[0])
    fee = initial_capital * leg_cost
    shares = (initial_capital - fee) / first_price
    # Il valore nel tempo è semplicemente: azioni possedute * prezzo di ogni giorno.
    equity = shares * prices["close"]
    equity.name = "buy_hold_equity"
    return equity


# ---------------------------------------------------------------------------
# Demo / test rapido:  python -m engine.backtester
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from data.loader import load_price_data
    from strategy.moving_average import moving_average_crossover

    prices = load_price_data(source="synthetic", period_years=6)
    strat = moving_average_crossover(prices, 50, 200)
    result = run_backtest(strat, initial_capital=10_000)

    print(f"Capitale iniziale: {result.initial_capital:,.2f}")
    print(f"Capitale finale (strategia):   {result.equity.iloc[-1]:,.2f}")
    print(f"Capitale finale (buy & hold):  {result.buy_hold_equity.iloc[-1]:,.2f}")
    print(f"Operazioni chiuse (round-trip): {len(result.trades)}")
    print(f"Gambe registrate (BUY/SELL):    {len(result.operations)}")

    if len(result.operations):
        print("\nRegistro operazioni (prime 6 gambe):")
        print(result.operations.head(6).to_string(index=False))
    if len(result.trades):
        print("\nOperazioni chiuse (prime 4):")
        cols = ["entry_date", "entry_price", "exit_date", "exit_price", "pnl", "return_pct", "costs"]
        print(result.trades[cols].head(4).to_string(index=False))

    # Coerenza contabile: se alla fine siamo liquidi, l'equity deve essere = cash.
    print(f"\nValore minimo equity: {result.equity.min():,.2f} (non deve mai essere negativo)")
