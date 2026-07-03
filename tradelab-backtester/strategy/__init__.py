"""
Package `strategy` — la LOGICA di trading (le regole per decidere quando stare
dentro o fuori dal mercato).

Ogni strategia riceve i prezzi puliti dal loader e restituisce, giorno per
giorno, la posizione desiderata (0 = liquidi, 1 = investito). Il motore di
simulazione non deve sapere COME nasce quel segnale: questo rende facile
aggiungere nuove strategie in futuro (roadmap) senza cambiare il resto.

    from strategy.moving_average import moving_average_crossover
"""
