"""
Package `engine` — il MOTORE di simulazione.

Prende i segnali della strategia (la colonna `position`, già a prova di
look-ahead) e simula giorno per giorno cosa sarebbe successo al capitale:
compra, vende, applica i costi, e tiene il registro di ogni operazione.

Non conosce i dettagli della strategia: gli basta la posizione desiderata.
Questo separa "cosa fare" (strategia) da "come si evolve il capitale" (motore).

    from engine.backtester import run_backtest, buy_and_hold_equity
"""
