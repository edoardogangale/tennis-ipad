"""
metrics/performance.py
======================

Calcolo delle metriche di performance standard, ognuna con la spiegazione di
COSA significa e PERCHÉ si calcola così. L'obiettivo del progetto è proprio
questo: giudicare una strategia con la matematica, non con le sensazioni.

Convenzione: usiamo 252 "periodi" per anno, perché in un anno ci sono circa
252 giorni di BORSA (non 365: i mercati sono chiusi nei weekend e nei festivi).
Questo numero serve per "annualizzare" grandezze calcolate su base giornaliera.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Struttura che raccoglie tutte le metriche calcolate
# ---------------------------------------------------------------------------
@dataclass
class PerformanceMetrics:
    initial_equity: float
    final_equity: float
    total_return: float          # rendimento totale sull'intero periodo
    cagr: float                  # rendimento annuo composto
    volatility: float            # volatilità annualizzata
    sharpe: float                # Sharpe ratio annualizzato
    max_drawdown: float          # perdita massima da un picco (numero negativo)
    max_dd_duration_days: int    # durata (giorni di calendario) del drawdown più lungo
    win_rate: float              # % di operazioni chiuse in profitto (NaN se 0 operazioni)
    profit_factor: float         # guadagni totali / perdite totali (NaN/inf nei casi limite)
    num_trades: int              # numero di operazioni chiuse
    years: float                 # anni di calendario coperti


# ---------------------------------------------------------------------------
# Funzioni "atomiche": una metrica ciascuna, così sono piccole e testabili
# ---------------------------------------------------------------------------
def daily_returns(equity: pd.Series) -> pd.Series:
    """
    Rendimenti SEMPLICI giornalieri della curva del capitale:
        r_t = equity_t / equity_{t-1} - 1
    Nota: nei giorni in cui la strategia è liquida, l'equity non cambia e il
    rendimento è 0. Trattiamo il contante come se non rendesse interessi: è una
    semplificazione consapevole (di solito trascurabile e prudente).
    """
    return equity.pct_change().dropna()


def total_return(equity: pd.Series) -> float:
    """Rendimento totale = quanto è cresciuto (o sceso) il capitale in tutto il periodo."""
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series) -> float:
    """
    CAGR = Compound Annual Growth Rate (tasso di crescita annuo composto).

    Risponde a: "a quale tasso COSTANTE all'anno sarei dovuto crescere per
    passare dal capitale iniziale a quello finale, tenendo conto della
    capitalizzazione composta?".
        CAGR = (equity_finale / equity_iniziale)^(1/anni) - 1
    È più onesto del rendimento totale perché lo "spalma" sul tempo: +100% in
    10 anni è molto diverso da +100% in 1 anno, e il CAGR lo rende evidente.
    """
    years = _years_covered(equity)
    if years <= 0:
        return float("nan")
    growth = equity.iloc[-1] / equity.iloc[0]
    # Se il capitale finale fosse <= 0 la potenza non sarebbe definita: ci
    # proteggiamo (non dovrebbe succedere, l'equity non va mai negativa).
    if growth <= 0:
        return -1.0
    return float(growth ** (1.0 / years) - 1.0)


def annualized_volatility(rets: pd.Series) -> float:
    """
    Volatilità annualizzata = quanto "ballano" i rendimenti, cioè il rischio.

    È la deviazione standard dei rendimenti giornalieri, poi ANNUALIZZATA
    moltiplicando per sqrt(252). Perché la radice quadrata? Perché la varianza
    (il quadrato della deviazione standard) cresce in modo proporzionale al
    tempo; quindi la deviazione standard cresce con la sua radice. Più è alta,
    più i risultati sono incerti e altalenanti.
    """
    if len(rets) < 2:
        return float("nan")
    return float(rets.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe_ratio(rets: pd.Series, risk_free_annual: float = 0.02) -> float:
    """
    Sharpe ratio = rendimento OLTRE il tasso privo di rischio, per unità di
    rischio. È la metrica principe per confrontare strategie diverse.

        Sharpe = media(rendimenti - risk_free) / dev.standard(rendimenti) * sqrt(252)

    Intuizione: guadagnare tanto è facile se ti prendi rischi enormi. Lo Sharpe
    premia chi guadagna tanto PER OGNI UNITÀ di rischio corso. Usiamo un tasso
    "privo di rischio" (di default 2% annuo, es. i titoli di Stato a breve)
    perché quello lo otterresti senza rischiare: ha senso contare solo il
    rendimento in ECCESSO rispetto ad esso.
      - Sharpe > 1  è considerato buono
      - Sharpe > 2  ottimo
      - Sharpe < 0  hai fatto peggio del non rischiare affatto
    """
    if len(rets) < 2:
        return float("nan")
    # Portiamo il tasso annuo a giornaliero per confrontarlo con i rendimenti daily.
    rf_daily = risk_free_annual / TRADING_DAYS_PER_YEAR
    excess = rets - rf_daily
    std = rets.std(ddof=1)
    if std == 0:
        return float("nan")  # nessuna oscillazione: lo Sharpe non è definito
    return float(excess.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))


def max_drawdown_and_duration(equity: pd.Series) -> tuple[float, int]:
    """
    Restituisce (max_drawdown, durata_del_drawdown_più_lungo_in_giorni).

    DRAWDOWN = quanto sei sceso rispetto al MASSIMO che avevi già raggiunto.
    In ogni istante:  drawdown_t = equity_t / (massimo fin qui) - 1   (<= 0)

    - MAX DRAWDOWN: il punto più profondo, cioè la peggior perdita che avresti
      sofferto comprando sul picco e tenendo fino al fondo. Misura il "dolore"
      peggiore: è spesso ciò che fa mollare gli investitori nella realtà.
    - DURATA DEL DRAWDOWN PIÙ LUNGO: il periodo più lungo passato SOTTO un
      massimo precedente prima di recuperarlo. Un conto è perdere il 30% e
      recuperare in 2 mesi, un altro è restare sott'acqua per 3 anni.
    """
    if len(equity) == 0:
        return float("nan"), 0

    running_max = equity.cummax()               # il massimo raggiunto fino a ogni giorno
    drawdown = equity / running_max - 1.0        # sempre <= 0
    max_dd = float(drawdown.min())               # il più negativo = perdita peggiore

    # Durata: la più lunga sequenza consecutiva di giorni passati SOTTO il picco.
    longest = pd.Timedelta(0)
    underwater_start = None
    below_peak = equity < running_max
    for date, is_below in below_peak.items():
        if is_below and underwater_start is None:
            underwater_start = date              # inizia un periodo "sott'acqua"
        elif not is_below and underwater_start is not None:
            longest = max(longest, date - underwater_start)  # recuperato: chiudi il conteggio
            underwater_start = None
    if underwater_start is not None:             # ancora sott'acqua alla fine dei dati
        longest = max(longest, equity.index[-1] - underwater_start)

    return max_dd, int(longest.days)


def win_rate(trades: pd.DataFrame) -> float:
    """
    Percentuale di operazioni CHIUSE in profitto.
    Attenzione: un win rate alto NON basta a dire che una strategia è buona —
    potresti vincere spesso poco e perdere di rado tanto. Va letto sempre
    insieme al profit factor qui sotto.
    """
    if trades is None or len(trades) == 0:
        return float("nan")
    wins = (trades["pnl"] > 0).sum()
    return float(wins / len(trades))


def profit_factor(trades: pd.DataFrame) -> float:
    """
    Profit factor = (somma dei guadagni) / (somma delle perdite, in valore assoluto).

        - > 1  la strategia guadagna più di quanto perde
        - = 1  pareggia
        - < 1  perde
    È complementare al win rate: dice quanto pesano i guadagni rispetto alle
    perdite, non quante volte vinci.
    """
    if trades is None or len(trades) == 0:
        return float("nan")
    gross_profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    gross_loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()   # reso positivo
    if gross_loss == 0:
        # Nessuna perdita: se c'è almeno un guadagno il profit factor è "infinito".
        return float("inf") if gross_profit > 0 else float("nan")
    return float(gross_profit / gross_loss)


# ---------------------------------------------------------------------------
# Aggregatore: calcola TUTTE le metriche in un colpo solo
# ---------------------------------------------------------------------------
def compute_metrics(
    equity: pd.Series,
    trades: pd.DataFrame | None = None,
    risk_free_annual: float = 0.02,
) -> PerformanceMetrics:
    """
    Calcola l'intero cruscotto di metriche per una curva del capitale.
    `trades` può essere None/vuoto (es. per il buy-and-hold, che non ha
    operazioni chiuse): in quel caso le metriche "da operazioni" sono NaN.
    """
    rets = daily_returns(equity)
    max_dd, dd_days = max_drawdown_and_duration(equity)
    return PerformanceMetrics(
        initial_equity=float(equity.iloc[0]),
        final_equity=float(equity.iloc[-1]),
        total_return=total_return(equity),
        cagr=cagr(equity),
        volatility=annualized_volatility(rets),
        sharpe=sharpe_ratio(rets, risk_free_annual),
        max_drawdown=max_dd,
        max_dd_duration_days=dd_days,
        win_rate=win_rate(trades) if trades is not None else float("nan"),
        profit_factor=profit_factor(trades) if trades is not None else float("nan"),
        num_trades=int(len(trades)) if trades is not None else 0,
        years=_years_covered(equity),
    )


# ---------------------------------------------------------------------------
# Formattazione leggibile con spiegazioni
# ---------------------------------------------------------------------------
def format_comparison_report(
    strategy: PerformanceMetrics,
    benchmark: PerformanceMetrics,
    title: str = "RISULTATI DEL BACKTEST",
) -> str:
    """
    Produce un report testuale che affianca Strategia e Buy & Hold, con una
    riga di spiegazione sotto ogni metrica. Restituisce una stringa (così è
    facile stamparla o salvarla su file).
    """
    def pct(x: float) -> str:
        return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:,.2f}%"

    def num(x: float, dec: int = 2) -> str:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return "n/a"
        if np.isinf(x):
            return "∞"
        return f"{x:,.{dec}f}"

    def money(x: float) -> str:
        return f"{x:,.2f}"

    lines = []
    lines.append("=" * 74)
    lines.append(title.center(74))
    lines.append("=" * 74)
    lines.append(f"Periodo coperto: {strategy.years:.2f} anni")
    lines.append("")
    header = f"{'METRICA':<26}{'STRATEGIA':>18}{'BUY & HOLD':>18}"
    lines.append(header)
    lines.append("-" * 74)

    def row(label: str, s_val: str, b_val: str, explanation: str):
        lines.append(f"{label:<26}{s_val:>18}{b_val:>18}")
        lines.append(f"  └ {explanation}")

    row("Capitale finale", money(strategy.final_equity), money(benchmark.final_equity),
        "quanto varrebbe oggi il portafoglio partendo dal capitale iniziale.")
    row("Rendimento totale", pct(strategy.total_return), pct(benchmark.total_return),
        "crescita complessiva sull'intero periodo.")
    row("CAGR (annuo composto)", pct(strategy.cagr), pct(benchmark.cagr),
        "tasso di crescita medio annuo: rende confrontabili periodi di lunghezza diversa.")
    row("Volatilità annua", pct(strategy.volatility), pct(benchmark.volatility),
        "quanto oscillano i rendimenti = il rischio. Più bassa è, più stabile.")
    row("Sharpe ratio", num(strategy.sharpe), num(benchmark.sharpe),
        "rendimento in eccesso per unità di rischio. >1 buono, >2 ottimo, <0 male.")
    row("Max drawdown", pct(strategy.max_drawdown), pct(benchmark.max_drawdown),
        "la peggior perdita da un picco: il 'dolore' massimo sopportato.")
    row("Durata max drawdown", f"{strategy.max_dd_duration_days} gg",
        f"{benchmark.max_dd_duration_days} gg",
        "il periodo più lungo passato sotto un massimo prima di recuperarlo.")
    row("Operazioni chiuse", num(strategy.num_trades, 0), num(benchmark.num_trades, 0),
        "quante compravendite complete ha fatto la strategia (il B&H ne fa 0).")
    row("Win rate", pct(strategy.win_rate), pct(benchmark.win_rate),
        "quota di operazioni chiuse in guadagno. Da leggere col profit factor.")
    row("Profit factor", num(strategy.profit_factor), num(benchmark.profit_factor),
        "guadagni totali / perdite totali. >1 = guadagna più di quanto perde.")

    lines.append("-" * 74)
    # Un verdetto sintetico, onesto: la strategia ha battuto il "compra e tieni"?
    delta = strategy.total_return - benchmark.total_return
    if np.isnan(delta):
        verdetto = "Confronto non disponibile."
    elif delta > 0:
        verdetto = (f"La strategia ha BATTUTO il buy & hold di "
                    f"{delta * 100:,.2f} punti percentuali di rendimento totale.")
    else:
        verdetto = (f"La strategia ha FATTO PEGGIO del buy & hold di "
                    f"{abs(delta) * 100:,.2f} punti percentuali. Con costi e rischio "
                    f"in più, non ha aggiunto valore su questo periodo.")
    lines.append(verdetto)
    lines.append("=" * 74)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper interni
# ---------------------------------------------------------------------------
def _years_covered(equity: pd.Series) -> float:
    """Anni di CALENDARIO tra la prima e l'ultima data (per annualizzare il CAGR)."""
    if len(equity) < 2:
        return 0.0
    delta_days = (equity.index[-1] - equity.index[0]).days
    return delta_days / 365.25   # 365.25 tiene conto degli anni bisestili


# ---------------------------------------------------------------------------
# Demo / test rapido:  python -m metrics.performance
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from data.loader import load_price_data
    from strategy.moving_average import moving_average_crossover
    from engine.backtester import run_backtest

    prices = load_price_data(source="synthetic", period_years=8)
    strat = moving_average_crossover(prices, 50, 200)
    result = run_backtest(strat, initial_capital=10_000)

    m_strat = compute_metrics(result.equity, result.trades, risk_free_annual=0.02)
    m_bh = compute_metrics(result.buy_hold_equity, trades=None, risk_free_annual=0.02)

    print(format_comparison_report(m_strat, m_bh, title="DEMO METRICHE (dati sintetici)"))
