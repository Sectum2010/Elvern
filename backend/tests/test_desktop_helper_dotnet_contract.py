from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_desktop_helper_dotnet_contract_is_consistent() -> None:
    global_sdk = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]
    props_root = ET.parse(
        ROOT / "clients" / "desktop-vlc-opener" / "Directory.Build.props"
    ).getroot()
    props = {
        child.tag: (child.text or "").strip()
        for group in props_root.findall("PropertyGroup")
        for child in group
    }
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    local_ci = (ROOT / "scripts" / "elvern-ci-local.sh").read_text(
        encoding="utf-8"
    )

    assert global_sdk == {
        "version": "10.0.100",
        "rollForward": "latestFeature",
        "allowPrerelease": False,
    }
    assert props["TargetFramework"] == "net10.0"
    assert re.search(r'dotnet-version:\s*"10\.0\.x"', workflow)
    assert 'dotnet-version: "8.0.x"' not in workflow
    assert "dotnet test clients/desktop-vlc-opener/Tests/" in workflow
    assert '[[ ! "$selected_version" =~ ^10\\. ]]' in local_ci
    assert "dotnet test clients/desktop-vlc-opener/Tests/" in local_ci
