# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""配布用パッケージの3様式（tools/make_templates.py が作る）の、元の公開様式のテスト"""

import hashlib
import importlib.util
import json
from pathlib import Path

from sumikeshi_studio import formstore

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "make_templates", ROOT / "tools" / "make_templates.py"
)
make_templates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_templates)


def test_templates_are_made_from_public_forms():
    """見本の元は公開様式で、記録した SHA-256 が samples/ のファイルと合う（見本のスキャンを元にしない）"""
    for form_id, _, year, public, _ in make_templates.TEMPLATES:
        path = formstore.BUNDLED_DIR / f"{form_id}.json"
        assert formstore.read_spec(path).size == (2339, 1654)
        data = json.loads(path.read_text("utf-8"))
        pdf = ROOT / "samples" / f"{public}.pdf"
        assert data["source"] == pdf.name
        assert data["source_url"] == make_templates.SOURCE_URL + pdf.name
        assert data["source_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
        assert data["year"] == year
