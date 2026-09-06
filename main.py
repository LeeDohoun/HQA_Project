"""CLI for price queries and the shared analysis server.

No arguments display help. Analysis requires an explicit command; all orders
remain in the backend PAPER TradeSignal lifecycle.
"""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence


def show_realtime_price(stock_input: str):
    from src.tools.realtime_tool import KISRealtimeTool
    from src.utils.stock_mapper import get_mapper

    if stock_input.isdigit() and len(stock_input) == 6:
        stock_code = stock_input
    else:
        stock_code = get_mapper().get_code(stock_input)
        if not stock_code:
            raise ValueError(f"Unknown stock: {stock_input}")

    tool = KISRealtimeTool()
    if not tool.is_available:
        raise ValueError("KIS price API is not configured")
    print(tool.get_quote_summary(stock_code))


def run_theme_trading_mode(
    *,
    theme: str,
    theme_key: str = "",
    candidate_limit: int = 5,
    top_n: int = 3,
    execute: bool = False,
    min_leader_score: Optional[int] = None,
    strategy_profile: str = "default",
    config_path: str = "config/watchlist.yaml",
):
    """Submit a theme preview; legacy execution arguments never enable orders."""
    if execute:
        raise ValueError("Python direct order execution has been removed; use backend TradeSignal trigger flow")

    from src.runner.analysis_scheduler import RemoteAnalysisClient

    result = RemoteAnalysisClient().submit("/runtime/multi-theme-trade", {
        "config_path": config_path, "include_theme_keys": [theme_key or theme],
        "candidate_limit": candidate_limit, "top_n": top_n,
        "min_leader_score": min_leader_score, "strategy_profile": strategy_profile,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_theme_report_trading_mode(
    *,
    report_path: str,
    execute_top_n: int = 1,
    execute: bool = False,
    config_path: str = "config/watchlist.yaml",
):
    """Preview an existing report without rerunning its LLM analysis."""
    if execute:
        raise ValueError("Python direct order execution has been removed; use backend TradeSignal trigger flow")

    from src.runner import ThemeLeaderTradingRunner

    result = ThemeLeaderTradingRunner(config_path=config_path).run_from_report(
        report_path=report_path, execute_top_n=execute_top_n, execute=False,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_multi_theme_trading_mode(
    *,
    top_n: int = 3,
    per_theme_top_n: int = 3,
    candidate_limit: int = 5,
    execute: bool = False,
    min_leader_score: Optional[int] = None,
    min_confidence: Optional[int] = None,
    max_risk_level: Optional[str] = None,
    strategy_profile: str = "default",
    config_path: str = "config/watchlist.yaml",
):
    """Submit a multi-theme analysis task to the shared server."""
    if execute:
        raise ValueError("Python direct order execution has been removed; use backend TradeSignal trigger flow")

    from src.runner.analysis_scheduler import RemoteAnalysisClient

    result = RemoteAnalysisClient().submit("/runtime/multi-theme-trade", {
        "config_path": config_path, "candidate_limit": candidate_limit,
        "per_theme_top_n": per_theme_top_n, "top_n": top_n,
        "min_leader_score": min_leader_score, "min_confidence": min_confidence,
        "max_risk_level": max_risk_level, "strategy_profile": strategy_profile,
        "buy_only": True,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="HQA price queries and shared analysis previews. Analysis may incur API costs.",
        allow_abbrev=False,
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("-p", "--price", help="Query a stock price by name or code")
    modes.add_argument("--theme-trade", help="Submit a theme analysis preview to the AI server")
    modes.add_argument("--theme-trade-report", help="Preview a saved report without rerunning analysis")
    modes.add_argument("--multi-theme-trade", action="store_true", help="Submit a multi-theme analysis preview")
    parser.add_argument("--theme-key", default="", help="Stored theme key for --theme-trade")
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=3, help="Theme result limit; per-theme limit in multi-theme mode")
    parser.add_argument("--execute-top-n", type=int, default=1, help="Preview selection limit for report or multi-theme mode; does not place orders")
    parser.add_argument("--min-leader-score", type=int)
    parser.add_argument("--min-confidence", type=int)
    parser.add_argument("--max-risk-level")
    parser.add_argument("--strategy-profile", choices=["default", "short", "long"], default="default")
    parser.add_argument("--config", default="config/watchlist.yaml")
    parser.add_argument("--preview", action="store_true", help="Explicit preview mode (all supported trading modes are previews)")
    parser.add_argument("--execute", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--help-full", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.execute:
        parser.error("Python direct order execution has been removed; use backend TradeSignal trigger flow")
    if args.help_full or not any((args.price, args.theme_trade, args.theme_trade_report, args.multi_theme_trade)):
        parser.print_help()
        return 0

    if args.price:
        show_realtime_price(args.price)
    elif args.theme_trade:
        run_theme_trading_mode(
            theme=args.theme_trade, theme_key=args.theme_key,
            candidate_limit=args.candidate_limit, top_n=args.top_n,
            min_leader_score=args.min_leader_score,
            strategy_profile=args.strategy_profile, config_path=args.config,
        )
    elif args.theme_trade_report:
        run_theme_report_trading_mode(
            report_path=args.theme_trade_report, execute_top_n=args.execute_top_n,
            config_path=args.config,
        )
    else:
        run_multi_theme_trading_mode(
            top_n=args.execute_top_n, per_theme_top_n=args.top_n,
            candidate_limit=args.candidate_limit, min_leader_score=args.min_leader_score,
            min_confidence=args.min_confidence, max_risk_level=args.max_risk_level,
            strategy_profile=args.strategy_profile, config_path=args.config,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
