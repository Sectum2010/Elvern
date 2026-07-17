from __future__ import annotations

import json
from pathlib import Path

from backend.app.services.library_quality_rank_service import build_library_quality_rank
from backend.app.services.library_movie_identity_service import quality_tier_for_row


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "library_quality_rank_cases.json"


def test_backend_quality_rank_matches_frontend_golden_contract() -> None:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    for case in cases:
        assert build_library_quality_rank(case["item"]) == case["expected"], case["name"]


def test_quality_tier_uses_the_canonical_quality_rank_contract() -> None:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    for case in cases:
        assert quality_tier_for_row(case["item"]) == case["expected"]["key"], case["name"]
