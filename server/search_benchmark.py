"""Evaluate labeled queries against a running Search Lab with Typesense hybrid retrieval."""
import argparse
import json
from pathlib import Path

from server.search_benchmark_runner import run_benchmark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run_benchmark(args.labels, url=args.url)
    except ValueError as err:
        parser.error(str(err))
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    args.output.write_text(encoded) if args.output else print(encoded)


if __name__ == "__main__":
    main()
