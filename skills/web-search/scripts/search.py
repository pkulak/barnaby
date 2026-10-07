#!/usr/bin/env python3

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

API_URL = "https://kagi.com/api/v1/search"
WORKFLOWS = ("search", "news", "images", "videos", "podcasts")


def bounded_int(name: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from error
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"{name} must be between {minimum} and {maximum}")
        return number

    return parse


def iso_date(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from error


def region(value: str) -> str:
    value = value.upper()
    if value == "NO_REGION":
        return "no_region"
    if not re.fullmatch(r"[A-Z]{2}", value):
        raise argparse.ArgumentTypeError("region must be a two-letter country code or no_region")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search with the official Kagi Search API.")
    parser.add_argument("query", help="search query")
    parser.add_argument("--workflow", choices=WORKFLOWS, default="search")
    parser.add_argument("--limit", type=bounded_int("limit", 1, 1024), default=10)
    parser.add_argument("--page", type=bounded_int("page", 1, 10))
    parser.add_argument(
        "--extract", type=bounded_int("extract", 1, 10), help="replace the top N snippets with page text"
    )
    parser.add_argument("--region", type=region)
    parser.add_argument("--after", type=iso_date, dest="after_date")
    parser.add_argument("--before", type=iso_date, dest="before_date")
    parser.add_argument("--raw", action="store_true", help="print the unmodified API response")
    args = parser.parse_args()

    if args.after_date and args.before_date and args.after_date > args.before_date:
        parser.error("--after must not be later than --before")

    return args


def request_payload(args: argparse.Namespace) -> dict:
    payload = {
        "query": args.query,
        "workflow": args.workflow,
        "limit": args.limit,
        "safe_search": True,
    }
    if args.page is not None:
        payload["page"] = args.page
    if args.extract is not None:
        payload["extract"] = {"count": args.extract}

    filters = {
        key: value
        for key, value in {
            "region": args.region,
            "after": args.after_date,
            "before": args.before_date,
        }.items()
        if value is not None
    }
    if filters:
        payload["filters"] = filters

    return payload


def clean(value):
    return html.unescape(value) if isinstance(value, str) else value


def normalize(payload: dict, query: str) -> dict:
    results = []
    data = payload.get("data") or {}
    if isinstance(data, dict):
        for result_type, items in data.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                props = item.get("props") or {}
                url = item.get("url")
                if isinstance(url, str) and not url.startswith(("http://", "https://")):
                    url = None
                result = {
                    "type": result_type,
                    "title": clean(item.get("title")),
                    "url": url,
                    "snippet": clean(item.get("snippet")),
                    "time": item.get("time"),
                }
                if isinstance(props, dict) and props.get("question"):
                    result["question"] = clean(props["question"])
                results.append({key: value for key, value in result.items() if value is not None})

    meta = payload.get("meta") or {}
    return {
        "query": query,
        "meta": {key: meta[key] for key in ("trace", "ms", "node") if key in meta},
        "results": results,
    }


def error_details(payload: dict) -> list:
    details = payload.get("errors") or payload.get("error") or []
    if isinstance(details, dict):
        return [details]
    return details if isinstance(details, list) else []


def report_http_error(error: urllib.error.HTTPError) -> None:
    body = error.read().decode("utf-8", "replace")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print(f"Kagi API returned HTTP {error.code}: {body[:1000]}", file=sys.stderr)
        return

    trace = (payload.get("meta") or {}).get("trace") or error.headers.get("X-Kagi-Trace")
    messages = []
    for detail in error_details(payload):
        if not isinstance(detail, dict):
            continue
        code = detail.get("code")
        message = detail.get("message")
        messages.append(": ".join(part for part in (code, message) if part))

    summary = "; ".join(messages) or f"HTTP {error.code}"
    if trace:
        summary += f" (trace {trace})"
    print(f"Kagi API error: {summary}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    key = os.environ.get("KAGI_KEY")
    if not key:
        print("KAGI_KEY is not set.", file=sys.stderr)
        return 1
    if key.startswith(("http://", "https://")):
        print("KAGI_KEY looks like a URL rather than an API key.", file=sys.stderr)
        return 1

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(request_payload(args)).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "barnaby-web-search/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60 if args.extract else 20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        report_http_error(error)
        return 1
    except urllib.error.URLError as error:
        print(f"Could not reach the Kagi API: {error.reason}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        print(f"Kagi API returned an invalid JSON response: {error}", file=sys.stderr)
        return 1

    output = payload if args.raw else normalize(payload, args.query)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
