"""RAP-Trader command-line dispatch."""

import sys

from app.cli.backtest import main as backtest_main
from app.cli.portfolio import main as portfolio_main


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "portfolio":
        return portfolio_main(sys.argv[2:])
    return backtest_main(sys.argv[1:])


raise SystemExit(main())
