"""
metrics/validation.py
=====================

FASE 6 — Validazione onesta con divisione TRAIN / TEST.

Perché serve: un backtest calcolato su TUTTI i dati insieme può illuderti. La
domanda vera è: "la strategia funziona anche su un pezzo di storia che non ho
guardato quando l'ho pensata?". Per rispondere dividiamo il tempo in due:
  - TRAIN: il primo 70% del periodo (la parte "in campione")
  - TEST : l'ultimo 30% (la parte "fuori campione")
e confrontiamo le metriche sulle due parti. Se sono MOLTO diverse, è un
campanello d'allarme: la strategia potrebbe funzionare solo per fortuna o solo
in un certo regime di mercato (overfitting / dipendenza dal periodo).

NOTA DI ONESTÀ INTELLETTUALE (importante):
  L'incrocio di medie 50/200 NON ha parametri "adattati" ai dati: 50 e 200 sono
  fissati a priori, non ottimizzati sul train. Quindi qui non c'è overfitting nel
  senso classico (quello nasce quando SCEGLI i parametri per far bella figura sul
  train). Un divario train/test, per questa strategia, segnala soprattutto che il
  risultato dipende dal PERIODO/mercato. Ma la macchina di validazione è già
  pronta: quando in futuro aggiungerai l'ottimizzazione dei parametri (roadmap),
  questo è ESATTAMENTE lo strumento che ti salverà dall'auto-inganno.

Metodo di calcolo: eseguiamo UNA simulazione sull'intero periodo (così non
sprechiamo i 200 giorni di warmup due volte) e poi "tagliamo" la curva del
capitale e le operazioni alla data di split, calcolando le metriche su ciascun
tratto. Le metriche di rendimento/rischio usano rapporti e quindi non dipendono
dal livello assoluto del capitale nel punto di taglio.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.backtester import run_backtest
from metrics.performance import PerformanceMetrics, compute_metrics, metrics_table
from strategy.moving_average import moving_average_crossover


@dataclass
class ValidationResult:
    split_date: pd.Timestamp
    train_frac: float
    strategy_train: PerformanceMetrics
    strategy_test: PerformanceMetrics
    benchmark_train: PerformanceMetrics
    benchmark_test: PerformanceMetrics
    warnings: list[str]


def time_split(prices: pd.DataFrame, train_frac: float = 0.70) -> pd.Timestamp:
    """
    Restituisce la DATA di taglio: i giorni prima appartengono al train, quelli
    da lì in poi al test. Divisione rigorosamente TEMPORALE (mai mescolare il
    futuro col passato in un backtest!).
    """
    if not 0.0 < train_frac < 1.0:
        raise ValueError("train_frac deve stare tra 0 e 1 (es. 0.70 = 70% train).")
    if len(prices) < 10:
        raise ValueError("Servono più dati per una divisione train/test sensata.")
    split_idx = int(len(prices) * train_frac)
    return prices.index[split_idx]


def train_test_validation(
    prices: pd.DataFrame,
    fast_window: int = 50,
    slow_window: int = 200,
    initial_capital: float = 10_000.0,
    commission: float = 0.001,
    spread: float = 0.0005,
    risk_free_annual: float = 0.02,
    train_frac: float = 0.70,
) -> ValidationResult:
    """
    Esegue la strategia sull'intero periodo e calcola le metriche separatamente
    per il tratto TRAIN e per il tratto TEST, poi valuta la loro coerenza.
    """
    split_date = time_split(prices, train_frac)

    # Una sola simulazione completa, poi tagliamo i risultati alla data di split.
    strat = moving_average_crossover(prices, fast_window, slow_window)
    result = run_backtest(strat, initial_capital, commission, spread)

    def slice_before(series: pd.Series) -> pd.Series:
        return series.loc[series.index < split_date]

    def slice_after(series: pd.Series) -> pd.Series:
        return series.loc[series.index >= split_date]

    # Le operazioni le assegniamo alla finestra in cui sono state CHIUSE (exit).
    trades = result.trades
    train_trades = trades[trades["exit_date"] < split_date] if len(trades) else trades
    test_trades = trades[trades["exit_date"] >= split_date] if len(trades) else trades

    strat_train = compute_metrics(slice_before(result.equity), train_trades, risk_free_annual)
    strat_test = compute_metrics(slice_after(result.equity), test_trades, risk_free_annual)
    bh_train = compute_metrics(slice_before(result.buy_hold_equity), None, risk_free_annual)
    bh_test = compute_metrics(slice_after(result.buy_hold_equity), None, risk_free_annual)

    warnings = _consistency_warnings(strat_train, strat_test)

    return ValidationResult(
        split_date=split_date,
        train_frac=train_frac,
        strategy_train=strat_train,
        strategy_test=strat_test,
        benchmark_train=bh_train,
        benchmark_test=bh_test,
        warnings=warnings,
    )


def _consistency_warnings(train: PerformanceMetrics, test: PerformanceMetrics) -> list[str]:
    """
    Confronta train e test con regole SEMPLICI ed ESPLICITE e produce una lista
    di avvertimenti. Le soglie sono volutamente trasparenti: puoi cambiarle.
    """
    warnings: list[str] = []

    # 1) Il rendimento annuo cambia SEGNO tra train e test?
    #    (guadagnavi e poi perdi, o viceversa: massima incoerenza)
    if _finite(train.cagr) and _finite(test.cagr) and train.cagr > 0 > test.cagr:
        warnings.append(
            f"Il CAGR passa da POSITIVO nel train ({train.cagr*100:+.1f}%) a "
            f"NEGATIVO nel test ({test.cagr*100:+.1f}%)."
        )

    # 2) Lo Sharpe crolla di oltre 1.0 punti passando al test?
    if _finite(train.sharpe) and _finite(test.sharpe) and (train.sharpe - test.sharpe) > 1.0:
        warnings.append(
            f"Lo Sharpe ratio crolla da {train.sharpe:.2f} (train) a "
            f"{test.sharpe:.2f} (test): la qualità del rendimento peggiora molto."
        )

    # 3) Il drawdown massimo peggiora di oltre 15 punti percentuali nel test?
    if _finite(train.max_drawdown) and _finite(test.max_drawdown) and \
            (test.max_drawdown < train.max_drawdown - 0.15):
        warnings.append(
            f"Il max drawdown peggiora da {train.max_drawdown*100:.1f}% (train) a "
            f"{test.max_drawdown*100:.1f}% (test): molto più rischioso fuori campione."
        )

    return warnings


def format_validation_report(v: ValidationResult) -> str:
    """
    Report testuale della validazione: tabella TRAIN vs TEST per la strategia e
    verdetto finale sull'eventuale overfitting / dipendenza dal periodo.
    """
    lines = []
    lines.append("#" * 74)
    lines.append("FASE 6 — VALIDAZIONE ONESTA (TRAIN / TEST)".center(74))
    lines.append("#" * 74)
    lines.append(
        f"Divisione temporale: primi {v.train_frac*100:.0f}% = TRAIN, "
        f"ultimi {(1-v.train_frac)*100:.0f}% = TEST"
    )
    lines.append(f"Data di taglio: {v.split_date.date()}")
    lines.append("")

    # Tabella con le due colonne TRAIN e TEST (metriche della sola strategia).
    lines.append(metrics_table(
        v.strategy_train, v.strategy_test,
        left_label="TRAIN", right_label="TEST",
        title="STRATEGIA: TRAIN vs TEST",
    ))
    lines.append("")

    # Verdetto sull'overfitting / coerenza.
    lines.append("-" * 74)
    if v.warnings:
        lines.append("⚠️  ATTENZIONE — i risultati TRAIN e TEST divergono in modo marcato:")
        for w in v.warnings:
            lines.append(f"     • {w}")
        lines.append("")
        lines.append("   Interpretazione: la strategia si è comportata molto diversamente")
        lines.append("   fuori campione. Diffida dei risultati brillanti sul solo train.")
    else:
        lines.append("✓  Risultati TRAIN e TEST coerenti: nessun segnale forte di")
        lines.append("   dipendenza dal periodo secondo le nostre soglie. Buon segno,")
        lines.append("   ma ricorda che 'coerente' non significa 'garantito nel futuro'.")
    lines.append("#" * 74)
    return "\n".join(lines)


def _finite(x: float) -> bool:
    """True se x è un numero utilizzabile (non NaN, non infinito)."""
    return x is not None and not (isinstance(x, float) and (np.isnan(x) or np.isinf(x)))


# ---------------------------------------------------------------------------
# Demo / test rapido:  python -m metrics.validation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from data.loader import load_price_data

    prices = load_price_data(source="synthetic", period_years=10)
    v = train_test_validation(prices, fast_window=50, slow_window=200)
    print(format_validation_report(v))
