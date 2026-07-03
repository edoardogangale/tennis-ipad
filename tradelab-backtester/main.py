"""
main.py — Punto di ingresso del backtester TradeLab.
====================================================

Mette in fila tutte le fasi:
    1) DATI       -> scarica/carica e pulisce i prezzi                (data.loader)
    2) STRATEGIA  -> genera i segnali dell'incrocio di medie          (strategy)
    3) MOTORE     -> simula i trade, applica i costi, tiene il log    (engine)
    4) METRICHE   -> calcola e spiega le performance                  (metrics)
    5) GRAFICI    -> salva i PNG in output/                           (reports)
    6) VALIDAZIONE-> confronto onesto train/test (overfitting?)       (metrics.validation)

Tutto è configurabile da riga di comando (vedi --help). Esempi:

    # Dati reali (richiede accesso di rete a Yahoo Finance):
    python main.py --ticker SPY --years 10

    # Prova OFFLINE con dati sintetici (utile se Yahoo non è raggiungibile):
    python main.py --source synthetic --years 10

    # Sperimenta altri parametri delle medie e altri costi:
    python main.py --ticker AAPL --fast 20 --slow 100 --commission 0.0005

RICORDA: strumento DIDATTICO. Nessun ordine reale, nessun consiglio finanziario.
"""

from __future__ import annotations

import argparse
import sys

from data.loader import DataError, load_price_data
from engine.backtester import BacktestError, run_backtest
from metrics.performance import compute_metrics, format_comparison_report
from metrics.validation import format_validation_report, train_test_validation
from reports.plotter import generate_reports
from strategy.moving_average import StrategyError, moving_average_crossover


def parse_args(argv=None) -> argparse.Namespace:
    """Definisce e legge tutti i parametri configurabili del backtest."""
    p = argparse.ArgumentParser(
        prog="tradelab",
        description="Backtester didattico: incrocio di medie mobili vs buy & hold.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # --- Dati ---
    p.add_argument("--ticker", default="SPY", help="Simbolo dell'asset (es. SPY, AAPL).")
    p.add_argument("--start", default=None, help="Data inizio YYYY-MM-DD (default: end - anni).")
    p.add_argument("--end", default=None, help="Data fine YYYY-MM-DD (default: oggi).")
    p.add_argument("--years", type=int, default=10, help="Anni di storico se --start non è dato.")
    p.add_argument("--source", default="yfinance", choices=["yfinance", "synthetic", "csv"],
                   help="Sorgente dati: reale (yfinance), finta per prove (synthetic), file (csv).")
    p.add_argument("--csv-path", default=None, help="Percorso del CSV quando --source csv.")
    # --- Strategia ---
    p.add_argument("--fast", type=int, default=50, help="Giorni della media mobile veloce.")
    p.add_argument("--slow", type=int, default=200, help="Giorni della media mobile lenta.")
    # --- Motore / costi ---
    p.add_argument("--capital", type=float, default=10_000.0, help="Capitale iniziale.")
    p.add_argument("--commission", type=float, default=0.001, help="Commissione per operazione (0.001 = 0,1%%).")
    p.add_argument("--spread", type=float, default=0.0005, help="Spread stimato (0.0005 = 0,05%%).")
    # --- Metriche / validazione ---
    p.add_argument("--risk-free", type=float, default=0.02, help="Tasso privo di rischio annuo (per lo Sharpe).")
    p.add_argument("--train-frac", type=float, default=0.70, help="Quota di dati per il train (resto = test).")
    # --- Output ---
    p.add_argument("--output-dir", default="output", help="Cartella dove salvare i grafici.")
    p.add_argument("--no-plots", action="store_true", help="Non generare i grafici (solo numeri).")
    return p.parse_args(argv)


def _print_header(args: argparse.Namespace) -> None:
    print("=" * 74)
    print("TRADELAB — BACKTESTER DIDATTICO".center(74))
    print("=" * 74)
    origine = {"yfinance": "dati reali (Yahoo Finance)",
               "synthetic": "dati SINTETICI (finti, solo per prova)",
               "csv": f"file CSV ({args.csv_path})"}[args.source]
    print(f"Asset: {args.ticker}   |   Sorgente: {origine}")
    print(f"Strategia: incrocio SMA {args.fast}/{args.slow}   |   "
          f"Capitale iniziale: {args.capital:,.0f}")
    print(f"Costi: commissione {args.commission*100:.3g}% + spread {args.spread*100:.3g}% "
          f"|   Risk-free: {args.risk_free*100:.2g}%")
    print("=" * 74)
    print()


def _print_operations_log(operations, out_dir: str) -> None:
    """Stampa il registro delle operazioni e lo salva anche come CSV."""
    print("\n" + "-" * 74)
    print("REGISTRO OPERAZIONI (ogni acquisto/vendita)".center(74))
    print("-" * 74)
    if len(operations) == 0:
        print("Nessuna operazione: la strategia non ha mai avuto un segnale di acquisto.")
        return
    # Formattazione leggibile del registro.
    view = operations.copy()
    view["date"] = view["date"].dt.date
    for col in ("price", "fee", "pnl", "cash_after", "equity_after"):
        view[col] = view[col].map(lambda x: f"{x:,.2f}" if x == x else "—")  # x==x scarta i NaN
    view["shares"] = view["shares"].map(lambda x: f"{x:,.4f}")
    print(view.to_string(index=False))

    import os
    csv_path = os.path.join(out_dir, "operations_log.csv")
    os.makedirs(out_dir, exist_ok=True)
    operations.to_csv(csv_path, index=False)
    print(f"\nRegistro completo salvato in: {csv_path}")


def run(args: argparse.Namespace) -> int:
    """Esegue l'intera pipeline. Ritorna il codice di uscita (0 = ok)."""
    _print_header(args)

    # --- FASE 1: DATI -----------------------------------------------------
    try:
        prices = load_price_data(
            ticker=args.ticker, start=args.start, end=args.end,
            period_years=args.years, source=args.source, csv_path=args.csv_path,
        )
    except DataError as exc:
        print(f"[ERRORE DATI] {exc}\n")
        if args.source == "yfinance":
            print("Suggerimento: se sei in un ambiente senza accesso a Yahoo Finance, "
                  "prova una simulazione offline con:\n"
                  "    python main.py --source synthetic\n")
        return 1
    print(f"Fase 1 — Dati: {len(prices)} giorni di borsa "
          f"dal {prices.index[0].date()} al {prices.index[-1].date()}.")

    # --- FASE 2 + 3: STRATEGIA E SIMULAZIONE ------------------------------
    try:
        strat = moving_average_crossover(prices, args.fast, args.slow)
        result = run_backtest(strat, args.capital, args.commission, args.spread)
    except (StrategyError, BacktestError) as exc:
        print(f"[ERRORE] {exc}")
        return 1
    print(f"Fase 2/3 — Strategia e simulazione: {len(result.trades)} operazioni chiuse.\n")

    # --- FASE 4: METRICHE -------------------------------------------------
    m_strat = compute_metrics(result.equity, result.trades, args.risk_free)
    m_bh = compute_metrics(result.buy_hold_equity, trades=None, risk_free_annual=args.risk_free)
    print(format_comparison_report(m_strat, m_bh,
                                   title=f"RISULTATI: {args.ticker} — SMA {args.fast}/{args.slow}"))

    # Registro operazioni (log richiesto dalla specifica).
    _print_operations_log(result.operations, args.output_dir)

    # --- FASE 5: GRAFICI --------------------------------------------------
    if not args.no_plots:
        print("\n" + "-" * 74)
        print("Fase 5 — Grafici salvati:")
        try:
            for path in generate_reports(result, args.output_dir, args.fast, args.slow):
                print(f"   • {path}")
        except Exception as exc:  # i grafici non devono far fallire tutto il resto
            print(f"   (impossibile generare i grafici: {exc})")

    # --- FASE 6: VALIDAZIONE TRAIN/TEST -----------------------------------
    print()
    try:
        v = train_test_validation(
            prices, args.fast, args.slow, args.capital,
            args.commission, args.spread, args.risk_free, args.train_frac,
        )
        print(format_validation_report(v))
    except (StrategyError, BacktestError, ValueError) as exc:
        print(f"[Validazione non eseguita] {exc}")

    # --- Disclaimer didattico --------------------------------------------
    print()
    print("ⓘ  Strumento puramente DIDATTICO. Analisi su dati storici, nessun ordine")
    print("   reale e nessun consiglio finanziario. I risultati passati non predicono")
    print("   quelli futuri. Serve a imparare a validare le strategie con la matematica.")
    return 0


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
