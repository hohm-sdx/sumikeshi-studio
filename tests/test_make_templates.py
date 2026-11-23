# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""tools/make_templates.py で作り直した配布用パッケージの3様式が、コミットされているものと同じことを確かめる

見本の画像が1〜3画素違うだけで、墨消しが欄から外れたことがあるため、スクリプトとコミットされている様式の食い違いを
テストで止める。PNG は圧縮の仕方で同じ画素でもバイト列が変わりうるので、デコードした画素を比べる。
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from sumikeshi_studio import formstore

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "make_templates", ROOT / "tools" / "make_templates.py"
)
make_templates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_templates)


@pytest.mark.parametrize("form_id", [t[0] for t in make_templates.TEMPLATES])
def test_rebuilt_template_is_same_as_committed(form_id):
    built, small = make_templates.build(form_id)
    folder = formstore.BUNDLED_DIR
    committed = (folder / f"{form_id}.json").read_text("utf-8")
    assert make_templates.spec_text(built) == committed
    image = cv2.imread(str(folder / built["image"]), cv2.IMREAD_UNCHANGED)
    assert image is not None and image.ndim == 2
    assert image.shape == small.shape
    assert np.array_equal(image, small), int((image != small).sum())


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
