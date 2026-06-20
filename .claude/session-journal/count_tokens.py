#!/usr/bin/env python3
"""Sum the token usage of a Claude Code session from its transcript JSONL.

Read-only. Stdlib only (no venv needed): run with `python3 count_tokens.py`.

Why this script exists
----------------------
`/cost` output never reaches Claude's context, so the journal's token figures
have to be reconstructed from the session transcript. The transcript is the
newest `*.jsonl` under
`~/.claude/projects/<cwd-encoded>/` (Claude Code writes one file per session,
named by session id).

The non-obvious gotcha
----------------------
An assistant turn that makes several tool calls is written as *several* JSONL
lines that all share one `message.id` / `requestId` and all repeat the SAME
`usage` block (usage is reported per API response, not per content block).
Summing every line therefore multiplies a turn's cost by its tool-call count.
The correct total sums each API call's usage exactly ONCE. We dedup by
`requestId` (falling back to `message.id`), which collapses those repeats.

Usage
-----
    python3 count_tokens.py                 # newest transcript for this project
    python3 count_tokens.py <path.jsonl>    # a specific session (e.g. to backfill)
    python3 count_tokens.py --dir <dir>     # override the transcript directory
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

# Usage fields we report. Total is their sum: input is uncached prompt tokens,
# cache_read/cache_write are the prompt-cache halves, output is generated tokens.
FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def transcript_dir(cwd: Path) -> Path:
    """Claude Code encodes the project path by replacing '/' and '.' with '-'."""
    encoded = re.sub(r"[/.]", "-", str(cwd))
    return Path.home() / ".claude" / "projects" / encoded


def newest_transcript(directory: Path) -> Path:
    files = sorted(
        directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not files:
        sys.exit(f"No *.jsonl transcripts under {directory}")
    return files[0]


def tally(path: Path) -> dict:
    agg = Counter()
    models = Counter()
    seen: set[str] = set()  # requestId / message.id already counted
    first_ts = last_ts = None
    calls = 0
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = obj.get("timestamp")
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message", {})
            usage = msg.get("usage")
            if not usage:
                continue
            key = obj.get("requestId") or msg.get("id")
            if key is not None and key in seen:
                continue  # repeat line of an already-counted API call
            if key is not None:
                seen.add(key)
            calls += 1
            for field in FIELDS:
                agg[field] += usage.get(field, 0)
            if model := msg.get("model"):
                models[model] += 1
    return {
        "agg": agg,
        "total": sum(agg[f] for f in FIELDS),
        "calls": calls,
        "models": models,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", nargs="?", help="path to a session .jsonl (default: newest for this project)")
    ap.add_argument("--dir", help="transcript directory to search (default: derived from cwd)")
    args = ap.parse_args()

    if args.transcript:
        path = Path(args.transcript).expanduser()
        if not path.exists():
            sys.exit(f"No such transcript: {path}")
    else:
        directory = Path(args.dir).expanduser() if args.dir else transcript_dir(Path.cwd())
        if not directory.exists():
            sys.exit(f"Transcript directory not found: {directory}\nPass one with --dir.")
        path = newest_transcript(directory)

    r = tally(path)
    agg = r["agg"]
    model = r["models"].most_common(1)[0][0] if r["models"] else "unknown"

    print(f"transcript : {path.name}")
    print(f"model      : {model}")
    print(f"api calls  : {r['calls']} (deduped; identical usage repeats collapsed)")
    if r["first_ts"]:
        print(f"span       : {r['first_ts']}  ->  {r['last_ts']}")
    print()
    print(f"  input (uncached)  {agg['input_tokens']:>14,}")
    print(f"  output            {agg['output_tokens']:>14,}")
    print(f"  cache write       {agg['cache_creation_input_tokens']:>14,}")
    print(f"  cache read        {agg['cache_read_input_tokens']:>14,}")
    print(f"  {'TOTAL':<16}  {r['total']:>14,}")
    print()
    # Tab-separated row matching the INDEX.md column order, ready to paste/adapt:
    print("INDEX row (input | output | cache_read | cache_write | total):")
    print(
        f"  {agg['input_tokens']:,} | {agg['output_tokens']:,} | "
        f"{agg['cache_read_input_tokens']:,} | {agg['cache_creation_input_tokens']:,} | "
        f"{r['total']:,}"
    )


if __name__ == "__main__":
    main()
