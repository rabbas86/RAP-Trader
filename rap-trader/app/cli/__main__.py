"""Module entry point for ``python -m app.cli.backtest``."""

import sys

from app.cli.backtest import main

raise SystemExit(main(sys.argv[1:]))
