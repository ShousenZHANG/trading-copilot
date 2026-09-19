"""Deterministic offline backtesting for the ETF sleeve.

Stdlib only by design. The CI matrix job installs zero third-party packages and
imports every module here, so an import of numpy, pandas or bt at module scope
breaks three Python versions at once. See
docs/adr/0006-backtest-scope-and-proxy-evidence.md for why `bt` is a
cross-check oracle rather than a dependency.
"""
