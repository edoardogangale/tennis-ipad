"""
Package `data` — tutto ciò che riguarda il PROCURARSI i prezzi storici.

Per usarne le funzioni importa esplicitamente dal modulo, così è sempre chiaro
da dove arriva ogni cosa:

    from data.loader import load_price_data

Tenere questo file (anche se quasi vuoto) rende `data` un package Python vero e
proprio e isola la sorgente dati dal resto del programma: domani potremo
aggiungere nuove sorgenti senza toccare strategia, motore e metriche.
"""
