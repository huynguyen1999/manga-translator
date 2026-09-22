"""Evaluate labeled queries against a running Search Lab without modifying its index."""
import argparse
import json
import math
import statistics
import urllib.request
from pathlib import Path


def metrics(runs):
    return {
        "queries": len(runs),
        "hitRateAt5": sum(run["hitAt5"] for run in runs) / len(runs),
        "meanReciprocalRank": statistics.mean(run["reciprocalRank"] for run in runs),
        "medianLatencyMs": statistics.median(run["elapsedMs"] for run in runs),
        "p95LatencyMs": sorted(run["elapsedMs"] for run in runs)[math.ceil(len(runs) * .95) - 1],
        "meanPageRecall": statistics.mean([run["pageRecall"] for run in runs if run["pageRecall"] is not None])
            if any(run["pageRecall"] is not None for run in runs) else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    labels = json.loads(args.labels.read_text())
    if not labels.get("queries") or any(not row.get("query") or not row.get("relevantGroupIds") for row in labels["queries"]):
        parser.error("Supply queries with nonempty query and relevantGroupIds fields.")
    report = {}
    for mode in ("summary", "image", "combined"):
        runs = []
        for row in labels["queries"]:
            request = urllib.request.Request(args.url.rstrip("/") + "/search/query", data=json.dumps({
                "query": row["query"], "mode": mode, "groupIds": labels.get("groupIds"), "limit": 20,
            }).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.load(response)
            ranks = [item["rank"] for item in result["results"] if item["groupId"] in row["relevantGroupIds"]]
            relevant_pages = set(row.get("relevantPageIds", []))
            found_pages = {page["pageId"] for item in result["results"][:5] for page in item["pages"]}
            runs.append({"query": row["query"], "hitAt5": bool(ranks and min(ranks) <= 5),
                "reciprocalRank": 1 / min(ranks) if ranks else 0,
                "pageRecall": len(found_pages & relevant_pages) / len(relevant_pages) if relevant_pages and mode != "summary" else None,
                "elapsedMs": result["elapsedMs"], "results": result["results"], "coverage": result["coverage"]})
        report[mode] = {"metrics": metrics(runs), "runs": runs}
    with urllib.request.urlopen(args.url.rstrip("/") + "/search/status", timeout=30) as response:
        status = json.load(response)
    report["runtime"] = {key: status.get(key) for key in ("device", "profile", "revisions", "metrics", "memory")}
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded)


if __name__ == "__main__":
    main()
