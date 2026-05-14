"""Canonical NSE index universe for FE-6 / FE-7 backfill jobs
(ASETPLTFRM-402 Phase 2).

Maps the public Yahoo Finance / spec notation to Kite Connect's
exact ``tradingsymbol``. The two name systems diverged
historically — the spec lists ``^NSEAUTO`` / ``^NSEFMCG`` /
``^NSEIT`` / ``^NSEMETAL`` etc. (Yahoo Finance ticker names),
while the daily factor library
(``backend/algo/factors/compute_job.py``::``SECTOR_INDEX_MAP``)
uses ``^CNXAUTO`` / ``^CNXFMCG`` / ``^CNXIT`` / ``^CNXMETAL``
(also Yahoo, alternate naming). Both refer to the same NSE
indices but neither matches what Kite returns.

Our authoritative reference is what Kite actually exposes in
``algo.instruments`` — typically the bare trading name like
``"NIFTY 50"``, ``"NIFTY BANK"``, ``"NIFTY AUTO"``. Phase 2
backfill writes ``ticker = Kite tradingsymbol`` verbatim so the
on-disk values match what the broker actually serves at runtime.
The daily factor pipeline keeps its Yahoo names; this surface is
the Kite-side intraday companion only.

Indices below were preflight-verified against the running
``algo.instruments`` table on 2026-05-15 — every entry has
``segment='INDICES'`` and ``exchange='NSE'``.
"""
from __future__ import annotations


# Broad-market + sectoral indices used by FE-8's RS-vs-NIFTY and
# sector-rotation features. Stored using the Kite tradingsymbol
# (segment='INDICES', exchange='NSE') so the on-disk ticker
# column matches what the on-demand backfill resolver looks up.
INDEX_UNIVERSE: list[str] = [
    "NIFTY 50",
    "NIFTY BANK",
    "NIFTY AUTO",
    "NIFTY FMCG",
    "NIFTY IT",
    "NIFTY FIN SERVICE",
    "NIFTY PHARMA",
    "NIFTY METAL",
    "NIFTY ENERGY",
    "NIFTY REALTY",
]
