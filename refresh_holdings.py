"""Monthly holdings updater for mf_universe.json.

WHY THIS IS A SCRIPT AND NOT A LIVE FEED (checked 16 Jul 2026):
  - api.mfapi.in ....... NAV history only; no holdings in the payload.
  - mfdata.in .......... the only free holdings API found; its origin is DOWN (Cloudflare 522).
  - api.kuvera.in ...... returns [] (gated).
  - amfiindia.com ...... reachable, but publishes NAV only - zero portfolio links. SEBI's monthly
                         portfolio-disclosure mandate is met by each AMC on its OWN site, so there
                         is no central machine-readable feed to poll.
So holdings are refreshed here, out of band, and the app stamps + ages whatever date this writes.

USAGE
  python refresh_holdings.py --check
      Probe every known source and report reachability. Changes nothing.

  python refresh_holdings.py --auto
      Try the live adapters. Writes only on a strictly-validated parse; otherwise changes nothing.
      (Works the day mfdata.in comes back - no code change needed.)

  python refresh_holdings.py --set 122639 "Amazon:8.51,ITC:7.99,Alphabet:7.08" \
      --asof "Aug 2026" --source https://amc.ppfas.com/downloads/factsheet/
      Validated manual update from a factsheet you are looking at. This works TODAY.

Run it monthly (AMCs disclose within ~10 days of month end) and the UI badge flips back to green.
"""
import argparse
import io
import json
import os
import re
import sys

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
UNIVERSE = os.path.join(BASE, "mf_universe.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
SOURCES = {
    "mfdata.in (holdings)": "https://mfdata.in/",
    "api.mfapi.in (NAV only)": "https://api.mfapi.in/mf/120716",
    "api.kuvera.in": "https://api.kuvera.in/mf/api/v4/fund_schemes/INF789F01XA0.json",
    "amfiindia.com (NAV only)": "https://www.amfiindia.com/spages/NAVAll.txt",
}
# Candidate mfdata.in shapes — unverified (site down when written). --auto validates before writing,
# so a wrong guess reports a miss instead of corrupting real data.
MFDATA_PATTERNS = [
    "https://mfdata.in/api/holdings/{code}",
    "https://mfdata.in/api/v1/holdings/{code}",
    "https://mfdata.in/api/scheme/{code}/holdings",
    "https://mfdata.in/api/v1/scheme/{code}/portfolio",
]


def load():
    with io.open(UNIVERSE, encoding="utf-8") as fh:
        return json.load(fh)


def save(data):
    with io.open(UNIVERSE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    json.load(io.open(UNIVERSE, encoding="utf-8"))  # never leave the file unparseable


def validate(holdings, as_of):
    """Reject anything that isn't plausibly a real disclosed portfolio."""
    if not holdings:
        return "no holdings parsed"
    if len(holdings) > 60:
        return f"{len(holdings)} rows looks wrong for a top-holdings list"
    total = 0.0
    for h in holdings:
        name, pct = h.get("name"), h.get("pct")
        if not name or not isinstance(name, str) or len(name) > 80:
            return f"bad holding name: {name!r}"
        if not isinstance(pct, (int, float)) or not (0 < pct <= 100):
            return f"bad percentage for {name!r}: {pct!r}"
        total += pct
    if total > 100.5:
        return f"percentages sum to {total:.1f}% (>100)"
    if not re.search(r"[A-Za-z]{3}[a-z]*\s+\d{4}", as_of or ""):
        return f"as-of must contain a month and year, got {as_of!r}"
    return None


def probe():
    print("Probing known sources:\n")
    for label, url in SOURCES.items():
        try:
            r = requests.get(url, timeout=15, headers=UA)
            verdict = "OK" if r.status_code == 200 else ("origin down (Cloudflare)" if r.status_code == 522 else "")
            print(f"  {label:28s} HTTP {r.status_code:<4} {verdict}")
        except Exception as exc:
            print(f"  {label:28s} FAIL  {type(exc).__name__}")
    print("\nHoldings are only available from mfdata.in; everything else is NAV-only.")
    print("If mfdata.in is down, use --set with an AMC factsheet (see --help).")


def fetch_mfdata(code):
    """Best-effort live holdings. Returns (holdings, source_url) or (None, None)."""
    for pattern in MFDATA_PATTERNS:
        url = pattern.format(code=code)
        try:
            r = requests.get(url, timeout=15, headers=UA)
            if r.status_code != 200:
                continue
            payload = r.json()
        except Exception:
            continue
        rows = payload.get("holdings") or payload.get("data") or payload.get("portfolio") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            continue
        parsed = []
        for row in rows[:15]:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or row.get("company") or row.get("stock") or row.get("security")
            pct = row.get("pct") or row.get("percentage") or row.get("weight") or row.get("allocation")
            try:
                pct = float(pct)
            except (TypeError, ValueError):
                continue
            if name:
                parsed.append({"name": str(name)[:80], "pct": round(pct, 2)})
        if parsed:
            return parsed, url
    return None, None


def cmd_auto(args):
    data = load()
    month = args.asof
    updated, skipped = [], []
    for fund in data["funds"]:
        code = fund["schemeCode"]
        holdings, url = fetch_mfdata(code)
        if not holdings:
            skipped.append(fund["name"][:40])
            continue
        problem = validate(holdings, month)
        if problem:
            print(f"  ! {fund['name'][:40]}: rejected ({problem})")
            skipped.append(fund["name"][:40])
            continue
        fund["holdings"], fund["holdingsAsOf"], fund["holdingsSource"] = holdings, month, url
        updated.append(fund["name"][:40])
    if updated:
        save(data)
        print(f"\nUpdated {len(updated)} fund(s) to {month}; {len(skipped)} unchanged.")
    else:
        print(f"\nNo live source returned usable holdings - nothing changed ({len(skipped)} fund(s) left as-is).")
        print("Run --check to see why, or use --set to update from an AMC factsheet.")


def cmd_set(args):
    holdings = []
    for part in args.pairs.split(","):
        if ":" not in part:
            sys.exit(f"Bad pair {part!r} - expected 'Name:pct'")
        name, pct = part.rsplit(":", 1)
        try:
            holdings.append({"name": name.strip(), "pct": round(float(pct), 2)})
        except ValueError:
            sys.exit(f"Bad percentage in {part!r}")
    problem = validate(holdings, args.asof)
    if problem:
        sys.exit(f"Refusing to write: {problem}")
    data = load()
    for fund in data["funds"]:
        if int(fund["schemeCode"]) == int(args.code):
            fund["holdings"], fund["holdingsAsOf"] = holdings, args.asof
            if args.source:
                fund["holdingsSource"] = args.source
            save(data)
            print(f"Updated {fund['name']}\n  {len(holdings)} holdings, as of {args.asof}")
            return
    sys.exit(f"Scheme {args.code} is not in mf_universe.json")


def main():
    parser = argparse.ArgumentParser(description="Refresh mutual fund holdings in mf_universe.json")
    parser.add_argument("--check", action="store_true", help="probe sources, change nothing")
    parser.add_argument("--auto", action="store_true", help="try live adapters (validated) ")
    parser.add_argument("--set", dest="code", help="scheme code to update manually")
    parser.add_argument("pairs", nargs="?", help="'Name:pct,Name:pct' when using --set")
    parser.add_argument("--asof", default=None, help="e.g. 'Aug 2026' (required for --set/--auto)")
    parser.add_argument("--source", default=None, help="source URL for --set")
    args = parser.parse_args()
    if args.check:
        return probe()
    if args.code:
        if not args.pairs or not args.asof:
            sys.exit("--set needs holdings pairs and --asof, e.g.\n  python refresh_holdings.py --set 122639 \"Amazon:8.51,ITC:7.99\" --asof \"Aug 2026\"")
        return cmd_set(args)
    if args.auto:
        if not args.asof:
            sys.exit("--auto needs --asof, e.g. --asof \"Aug 2026\"")
        return cmd_auto(args)
    parser.print_help()


if __name__ == "__main__":
    main()
