"""Export the consent-gated, pseudonymized dataset for analytics and ML.

Usage:
    COLLECTIVIZE_EXPORT_KEY=<secret> uv run python scripts/export_ml_dataset.py \\
        --staff <your-staff-id> --purpose "Train the attendance model"

Writes one JSON Lines file per table (people, interactions, memberships,
connections, events, orgs) plus a manifest into data/exports/<timestamp>/.
Output always stays under data/exports (which git ignores). Only people who
opted in to analytics are included; see backend/graph/data_access.py.

Exports live outside the database, so deleting an account can't remove a
person from files already written. Keep them short-lived.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backend.config import ConfigError, load_config
from backend.database import open_graph
from backend.graph.data_access import export_key_from_env
from backend.objects import DomainRuleError
from backend.objects.base import utc_now

EXPORT_ROOT = ROOT / "data" / "exports"
log = logging.getLogger("export")


def safe_output_dir(name: str | None, stamp: str) -> Path:
    """Resolve the output folder and refuse anything outside data/exports."""
    target = (EXPORT_ROOT / (name or stamp)).resolve()
    if EXPORT_ROOT.resolve() not in target.parents:
        raise ValueError("the output folder must be inside data/exports")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staff", required=True, help="your staff id (recorded)")
    parser.add_argument("--purpose", required=True, help="what the export is for (recorded)")
    parser.add_argument("--name", help="folder name under data/exports (default: timestamp)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    now = utc_now()
    try:
        out = safe_output_dir(args.name, now.strftime("%Y%m%dT%H%M%SZ"))
        key = export_key_from_env()
    except (ValueError, ConfigError) as error:
        log.error("%s", error)
        return 1
    if out.exists():
        log.error("%s already exists; choose another --name.", out)
        return 1

    config = load_config()
    with open_graph(config) as graph:
        try:
            dataset = graph.data_access.ml_dataset(args.staff, args.purpose, now, key)
        except DomainRuleError as error:
            log.error("%s", error)
            return 1

    out.mkdir(parents=True)
    for table, rows in dataset.tables().items():
        with (out / f"{table}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "generated_at": dataset.generated_at, "purpose": dataset.purpose,
        "environment": config.environment.value, "requested_by": args.staff,
        "counts": {table: len(rows) for table, rows in dataset.tables().items()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s: %s", out, manifest["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
