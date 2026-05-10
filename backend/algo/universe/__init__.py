"""Universe snapshot — point-in-time NSE active universe.

REGIME-7. Rebuilt monthly (1st Sunday 03:00 IST); the
``pit_resolver`` reads the most recent snapshot ``<= bar_date``
to give backtests a survivorship-bias-free universe.
"""
