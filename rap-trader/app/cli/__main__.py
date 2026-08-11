"""RAP-Trader command-line dispatch."""

import sys

from app.cli.backtest import main as backtest_main
from app.cli.committee import main as committee_main
from app.cli.portfolio import main as portfolio_main
from app.cli.risk import main as risk_main


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "portfolio":
        return portfolio_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "risk":
        return risk_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "committee":
        return committee_main(sys.argv[2:])
    return backtest_main(sys.argv[1:])


raise SystemExit(main())
