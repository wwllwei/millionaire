#!/usr/bin/env python3
"""Download US-listed company tickers and exchanges from the SEC.

Source: https://www.sec.gov/files/company_tickers_exchange.json
(The plain company_tickers.json has no exchange field, so this file is used.)

Usage:
    python get_us_tickers.py                  # writes tickers.csv
    python get_us_tickers.py -o my_list.csv   # custom output file
    python get_us_tickers.py -e Nasdaq NYSE   # filter by exchange

IMPORTANT: The SEC requires a descriptive User-Agent with contact info.
Edit USER_AGENT below with your own name/email or requests may be blocked (403).
"""

import argparse
import csv
import json
import urllib.request

URL = "https://www.sec.gov/files/company_tickers_exchange.json"
USER_AGENT = "Your Name your.email@example.com"  # <-- change this


def fetch_tickers():
    """Return a list of dicts: {'ticker': ..., 'exchange': ...}."""
    req = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)

    # Format: {"fields": ["cik", "name", "ticker", "exchange"], "data": [[...], ...]}
    fields = payload["fields"]
    rows = [dict(zip(fields, row)) for row in payload["data"]]

    return [
        {"ticker": r["ticker"], "exchange": r.get("exchange") or ""}
        for r in rows
    ]


def main():
    parser = argparse.ArgumentParser(description="Get US tickers and exchanges from SEC.")
    parser.add_argument("-o", "--output", default="tickers.csv", help="output CSV path")
    parser.add_argument(
        "-e", "--exchange", nargs="+",
        help="only keep these exchanges (e.g. Nasdaq NYSE CBOE OTC)",
    )
    args = parser.parse_args()

    tickers = fetch_tickers()

    if args.exchange:
        wanted = {x.lower() for x in args.exchange}
        tickers = [t for t in tickers if t["exchange"].lower() in wanted]

    tickers.sort(key=lambda t: t["ticker"])

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "exchange"])
        writer.writeheader()
        writer.writerows(tickers)

    print(f"Saved {len(tickers)} tickers to {args.output}")


if __name__ == "__main__":
    main()
