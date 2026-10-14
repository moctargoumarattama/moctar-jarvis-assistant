"""Vérification d'une instance réelle, sans redémarrer aucun service."""
import argparse
import json
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5050")
    args = parser.parse_args()
    with httpx.Client(trust_env=False, timeout=8) as client:
        health = client.get(args.url + "/api/health")
        health.raise_for_status()
        response = client.get(args.url + "/api/snapshot")
        response.raise_for_status()
        snapshot = response.json()
        assert len(snapshot["services"]) == 4
        started = time.monotonic()
        times = []
        ids = []
        with client.stream("GET", args.url + "/api/stream") as stream:
            stream.raise_for_status()
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert stream.headers["x-accel-buffering"] == "no"
            for line in stream.iter_lines():
                if line.startswith("id: "):
                    ids.append(int(line[4:]))
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    assert "hardware" in event and "events" in event
                    times.append(round(time.monotonic() - started, 2))
                    if len(times) == 4:
                        break
        assert len(ids) == 4 and all(b > a for a, b in zip(ids, ids[1:]))
        rss = snapshot["hardware"]["process_rss_bytes"]
        print(json.dumps({"health": health.json(), "rss_mib": round(rss / 1024**2, 2) if rss else None, "event_ids": ids, "arrival_seconds": times, "intervals": [round(b - a, 2) for a, b in zip(times, times[1:])]}, indent=2))


if __name__ == "__main__":
    main()
