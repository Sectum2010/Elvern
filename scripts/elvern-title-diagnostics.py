#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import refresh_settings
from backend.app.db import get_connection
from backend.app.services.media_title_parser import TITLE_PARSER_VERSION, parse_media_title
from backend.app.services.title_normalization import resolve_poster_match_identity


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run Elvern movie title and poster-match derivation without rewriting stored data.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Maximum rows to inspect.")
    parser.add_argument(
        "--source-kind",
        choices=("all", "local", "cloud"),
        default="all",
        help="Restrict diagnostics to one source kind.",
    )
    parser.add_argument(
        "--only-suspicious",
        action="store_true",
        help="Show only rows where the parser emitted suspicious-output diagnostics.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON object per line instead of a compact human-readable summary.",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Emit a single deterministic JSON snapshot for diffing across runs.",
    )
    return parser


def _build_record(row) -> dict[str, object]:
    parsed = parse_media_title(
        title=row["title"],
        original_filename=row["original_filename"],
        year=row["year"],
    )
    poster_identity = resolve_poster_match_identity(
        title=row["title"],
        original_filename=row["original_filename"],
        year=row["year"],
    )
    return {
        "id": int(row["id"]),
        "source_kind": str(row["source_kind"]),
        "stored_title": str(row["title"] or ""),
        "original_filename": str(row["original_filename"] or ""),
        "stored_year": row["year"],
        "display_title": str(parsed.get("display_title") or ""),
        "poster_match_identity": {
            "title": poster_identity.get("title"),
            "year": poster_identity.get("year"),
        },
        "title_source": str(parsed.get("title_source") or ""),
        "parse_confidence": str(parsed.get("parse_confidence") or ""),
        "parser_version": str(parsed.get("parser_version") or TITLE_PARSER_VERSION),
        "warnings": [str(value) for value in parsed.get("warnings") or []],
        "suspicious_output": bool(parsed.get("suspicious_output")),
        "display_title_changed": str(row["title"] or "").strip() != str(parsed.get("display_title") or "").strip(),
    }


def _build_snapshot(
    *,
    rows,
    source_kind: str,
    only_suspicious: bool,
    limit: int,
) -> dict[str, object]:
    records = [_build_record(row) for row in rows]
    if only_suspicious:
        records = [record for record in records if record["suspicious_output"]]
    suspicious_count = sum(1 for record in records if record["suspicious_output"])
    return {
        "parser_version": TITLE_PARSER_VERSION,
        "filters": {
            "source_kind": source_kind,
            "only_suspicious": only_suspicious,
            "limit": limit,
        },
        "summary": {
            "rows_checked": len(rows),
            "rows_reported": len(records),
            "suspicious_rows": suspicious_count,
        },
        "rows": records,
    }


def main() -> int:
    args = _build_parser().parse_args()
    settings = refresh_settings()

    where_sql = ""
    params: list[object] = []
    if args.source_kind != "all":
        where_sql = "WHERE COALESCE(source_kind, 'local') = ?"
        params.append(args.source_kind)

    query = f"""
        SELECT
            id,
            title,
            original_filename,
            year,
            COALESCE(source_kind, 'local') AS source_kind
        FROM media_items
        {where_sql}
        ORDER BY id ASC
        LIMIT ?
    """
    params.append(max(args.limit, 1))

    with get_connection(settings) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()

    if args.snapshot:
        snapshot = _build_snapshot(
            rows=rows,
            source_kind=args.source_kind,
            only_suspicious=bool(args.only_suspicious),
            limit=max(args.limit, 1),
        )
        print(json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True))
        return 0

    suspicious_count = 0
    for row in rows:
        record = _build_record(row)
        suspicious = bool(record["suspicious_output"])
        if suspicious:
            suspicious_count += 1
        if args.only_suspicious and not suspicious:
            continue

        if args.json:
            print(json.dumps(record, ensure_ascii=True, sort_keys=True))
            continue

        print(
            f"[{record['id']}] {record['source_kind']} suspicious={record['suspicious_output']} "
            f"stored={record['stored_title']!r} -> display={record['display_title']!r} "
            f"poster={record['poster_match_identity']['title']!r} "
            f"year={record['poster_match_identity']['year']} "
            f"source={record['title_source']} warnings={record['warnings']}"
        )

    if not args.json:
        print(
            json.dumps(
                {
                    "rows_checked": len(rows),
                    "rows_reported": suspicious_count if args.only_suspicious else len(rows),
                    "suspicious_rows": suspicious_count,
                },
                ensure_ascii=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
