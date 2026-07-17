from __future__ import annotations

import json
from pathlib import Path

from backend.app.services.library_quality_rank_service import build_library_quality_rank


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "library_quality_rank_cases.json"


def test_backend_quality_rank_matches_frontend_golden_contract() -> None:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    for case in cases:
        assert build_library_quality_rank(case["item"]) == case["expected"], case["name"]
