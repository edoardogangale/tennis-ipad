"""
reports/plotter.py
==================

Genera i grafici del backtest e li salva come PNG nella cartella output/.

Grafici prodotti:
  1) backtest_equity.png
       - in alto: curva del capitale della STRATEGIA vs BUY & HOLD (stessa scala),
         con i punti di ENTRATA (triangolo su, verde) e USCITA (triangolo giù, rosso)
       - in basso: il DRAWDOWN della strategia nel tempo (quanto sotto il massimo)
  2) price_and_signals.png
       - il prezzo con le due medie mobili e i golden/death cross evidenziati

Nota tecnica: usiamo il backend "Agg" di matplotlib, che disegna su file senza
bisogno di uno schermo. È indispensabile per girare su un server / in questo
ambiente remoto, dove non c'è una finestra grafica.
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")  # backend "senza finestra": salva su file. Va impostato PRIMA di pyplot.

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from engine.backtester import BacktestResult
from strategy.moving_average import crossover_events

# Palette scelta per essere leggibile e distinguibile anche da chi ha difficoltà
# con i colori: blu vs arancione è la coppia più sicura in questi casi.
COLOR_STRATEGY = "#2563eb"   # blu  -> strategia
COLOR_BENCH = "#ea7317"      # arancione -> buy & hold
COLOR_DRAWDOWN = "#d62728"   # rosso -> drawdown / uscite
COLOR_ENTRY = "#2ca02c"      # verde -> entrate
COLOR_FAST = "#ea7317"       # media veloce
COLOR_SLOW = "#9467bd"       # media lenta
COLOR_PRICE = "#444444"      # prezzo


def _ensure_dir(out_dir: str) -> None:
    """Crea la cartella di output se non esiste (così l'utente non deve pensarci)."""
    os.makedirs(out_dir, exist_ok=True)


def _format_money_axis(ax) -> None:
    """Mette i separatori delle migliaia sull'asse Y (es. 12,500) per leggibilità."""
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))


def plot_equity_and_drawdown(
    result: BacktestResult,
    out_dir: str = "output",
    filename: str = "backtest_equity.png",
) -> str:
    """
    Disegna la curva del capitale (strategia vs buy & hold) con i punti di
    entrata/uscita, e sotto il drawdown della strategia. Ritorna il percorso del PNG.
    """
    _ensure_dir(out_dir)
    equity = result.equity
    bench = result.buy_hold_equity

    # Due pannelli impilati che condividono l'asse del tempo. Quello dell'equity
    # è più alto (rapporto 3:1) perché è il grafico principale.
    fig, (ax_eq, ax_dd) = plt.subplots(
        2, 1, figsize=(12, 8), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    # --- Pannello 1: curve del capitale -----------------------------------
    ax_eq.plot(equity.index, equity.values, color=COLOR_STRATEGY, linewidth=1.8,
               label=f"Strategia (finale {equity.iloc[-1]:,.0f})")
    ax_eq.plot(bench.index, bench.values, color=COLOR_BENCH, linewidth=1.6,
               linestyle="--", label=f"Buy & Hold (finale {bench.iloc[-1]:,.0f})")

    # Punti di entrata (BUY) e uscita (SELL), presi dal registro operazioni e
    # posizionati sul valore della curva della strategia in quelle date.
    ops = result.operations
    if len(ops):
        buys = ops[ops["type"] == "BUY"]
        sells = ops[ops["type"] == "SELL"]
        if len(buys):
            ax_eq.scatter(buys["date"], equity.reindex(buys["date"]).values,
                          marker="^", s=90, color=COLOR_ENTRY, zorder=5,
                          edgecolors="white", linewidths=0.6, label="Entrata (BUY)")
        if len(sells):
            ax_eq.scatter(sells["date"], equity.reindex(sells["date"]).values,
                          marker="v", s=90, color=COLOR_DRAWDOWN, zorder=5,
                          edgecolors="white", linewidths=0.6, label="Uscita (SELL)")

    ax_eq.set_title("Curva del capitale: Strategia vs Buy & Hold", fontsize=13, fontweight="bold")
    ax_eq.set_ylabel("Capitale")
    ax_eq.legend(loc="upper left", framealpha=0.9)
    ax_eq.grid(True, alpha=0.3)
    _format_money_axis(ax_eq)

    # --- Pannello 2: drawdown della strategia -----------------------------
    # drawdown_t = equity_t / (massimo raggiunto fin qui) - 1  (sempre <= 0).
    running_max = equity.cummax()
    drawdown = (equity / running_max - 1.0) * 100.0  # in percentuale
    ax_dd.fill_between(drawdown.index, drawdown.values, 0.0,
                       color=COLOR_DRAWDOWN, alpha=0.35)
    ax_dd.plot(drawdown.index, drawdown.values, color=COLOR_DRAWDOWN, linewidth=0.8)
    ax_dd.set_title("Drawdown della strategia (quanto sotto il massimo precedente)",
                    fontsize=11)
    ax_dd.set_ylabel("Drawdown %")
    ax_dd.set_xlabel("Data")
    ax_dd.grid(True, alpha=0.3)

    fig.autofmt_xdate()  # ruota le date così non si sovrappongono
    fig.tight_layout()
    path = os.path.join(out_dir, filename)
    fig.savefig(path, dpi=110)
    plt.close(fig)  # liberiamo la memoria: importante se si generano molti grafici
    return path


def plot_price_and_signals(
    strategy_df: pd.DataFrame,
    out_dir: str = "output",
    filename: str = "price_and_signals.png",
    fast_window: int = 50,
    slow_window: int = 200,
) -> str:
    """
    Disegna il prezzo con le due medie mobili e segna i golden/death cross.
    Serve a "vedere" da dove nascono i segnali della strategia. Ritorna il path.
    """
    _ensure_dir(out_dir)
    golden, death = crossover_events(strategy_df)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(strategy_df.index, strategy_df["close"], color=COLOR_PRICE,
            linewidth=1.0, alpha=0.8, label="Prezzo (close)")
    ax.plot(strategy_df.index, strategy_df["sma_fast"], color=COLOR_FAST,
            linewidth=1.4, label=f"SMA veloce ({fast_window})")
    ax.plot(strategy_df.index, strategy_df["sma_slow"], color=COLOR_SLOW,
            linewidth=1.4, label=f"SMA lenta ({slow_window})")

    # I cross si posizionano sul prezzo del giorno in cui avvengono.
    if len(golden):
        ax.scatter(golden, strategy_df.loc[golden, "close"], marker="^", s=110,
                   color=COLOR_ENTRY, zorder=5, edgecolors="white", linewidths=0.6,
                   label="Golden cross (compra)")
    if len(death):
        ax.scatter(death, strategy_df.loc[death, "close"], marker="v", s=110,
                   color=COLOR_DRAWDOWN, zorder=5, edgecolors="white", linewidths=0.6,
                   label="Death cross (vendi)")

    ax.set_title("Prezzo, medie mobili e incroci (segnali della strategia)",
                 fontsize=13, fontweight="bold")
    ax.set_ylabel("Prezzo")
    ax.set_xlabel("Data")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.grid(True, alpha=0.3)

    fig.autofmt_xdate()
    fig.tight_layout()
    path = os.path.join(out_dir, filename)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def generate_reports(
    result: BacktestResult,
    out_dir: str = "output",
    fast_window: int = 50,
    slow_window: int = 200,
) -> list[str]:
    """
    Genera TUTTI i grafici in un colpo solo e restituisce la lista dei file
    salvati (comodo per stamparli in main.py).
    """
    paths = [
        plot_equity_and_drawdown(result, out_dir),
        plot_price_and_signals(result.strategy_df, out_dir,
                               fast_window=fast_window, slow_window=slow_window),
    ]
    return paths


# ---------------------------------------------------------------------------
# Demo / test rapido:  python -m reports.plotter
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from data.loader import load_price_data
    from strategy.moving_average import moving_average_crossover
    from engine.backtester import run_backtest

    prices = load_price_data(source="synthetic", period_years=8)
    strat = moving_average_crossover(prices, 50, 200)
    result = run_backtest(strat, initial_capital=10_000)

    saved = generate_reports(result, out_dir="output", fast_window=50, slow_window=200)
    print("Grafici salvati:")
    for p in saved:
        size = os.path.getsize(p)
        print(f"  - {p}  ({size:,} byte)")
