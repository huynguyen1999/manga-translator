"""Benchmark runner evaluating Typesense hybrid retrieval accuracy and latency."""
import json
import math
import statistics
import urllib.request
from pathlib import Path


def metrics(runs):
    if not runs:
        return {}
    n = len(runs)

    def p95(vals):
        return sorted(vals)[math.ceil(len(vals) * 0.95) - 1] if vals else 0

    return {
        "queries": n,
        "candidateRecallAt50": sum(run["candidateRecall"] for run in runs) / n,
        "hitRateAt1": sum(run["hitAt1"] for run in runs) / n,
        "hitRateAt5": sum(run["hitAt5"] for run in runs) / n,
        "meanReciprocalRank": statistics.mean(run["reciprocalRank"] for run in runs),
        "medianLatencyMs": statistics.median(run["elapsedMs"] for run in runs),
        "p95LatencyMs": p95([run["elapsedMs"] for run in runs]),
        "latencies": {
            "embeddingMedianMs": statistics.median(run["embeddingMs"] for run in runs),
            "embeddingP95Ms": p95([run["embeddingMs"] for run in runs]),
            "retrievalMedianMs": statistics.median(run["retrievalMs"] for run in runs),
            "retrievalP95Ms": p95([run["retrievalMs"] for run in runs]),
            "totalMedianMs": statistics.median(run["elapsedMs"] for run in runs),
            "totalP95Ms": p95([run["elapsedMs"] for run in runs]),
        },
    }


def execute_query(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/search/query",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.load(response)


def run_benchmark(labels_path: Path, url: str = "http://127.0.0.1:8000") -> dict:
    labels = json.loads(labels_path.read_text())
    if not labels.get("queries") or any(not row.get("query") or not row.get("relevantGroupIds") for row in labels["queries"]):
        raise ValueError("Supply queries with nonempty query and relevantGroupIds fields.")

    typesense_runs = []

    for row in labels["queries"]:
        relevant = set(row["relevantGroupIds"])

        payload = {"query": row["query"], "groupIds": labels.get("groupIds"), "limit": 20}
        res = execute_query(url, payload)
        cands = res.get("initialResults", [])
        results = res.get("results", [])
        cand_ranks = [item["rank"] for item in cands if item["groupId"] in relevant]
        ranks = [item["rank"] for item in results if item["groupId"] in relevant]

        typesense_runs.append({
            "query": row["query"], "candidateRecall": bool(cand_ranks),
            "hitAt1": bool(ranks and min(ranks) == 1), "hitAt5": bool(ranks and min(ranks) <= 5),
            "reciprocalRank": 1 / min(ranks) if ranks else 0.0,
            "elapsedMs": res.get("elapsedMs", 0), "embeddingMs": res.get("embeddingMs", 0),
            "retrievalMs": res.get("retrievalMs", 0),
            "results": results, "initialResults": cands, "coverage": res.get("coverage"),
        })

    report = {
        "typesenseHybrid": {
            "description": "Typesense Hybrid (BGE dense vector + fuzzy lexical search)",
            "metrics": metrics(typesense_runs),
            "runs": typesense_runs,
        },
    }

    with urllib.request.urlopen(url.rstrip("/") + "/search/status", timeout=30) as response:
        status = json.load(response)
    report["runtime"] = {key: status.get(key) for key in ("engine", "device", "profile", "models", "revisions", "metrics", "memory")}
    return report
