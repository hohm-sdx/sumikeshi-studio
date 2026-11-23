# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""テストの補助（conftest.py）の、解析の結果の使い回し（cached_analyze）のテスト

解析そのものは置き換えて、呼ばれた回数だけを数える（1ページの解析には数秒かかるため）。
"""

import shutil

import conftest
import pytest
from scan import DATA
from userforms import bundled_forms, install_bundled

from sumikeshi_studio import align, formstore, i18n, imaging, ocr, pipeline

PAGE = DATA / "公開様式_2026bun_04_200dpi_グレー.png"
OTHER = DATA / "公開様式_2026bun_06_200dpi_グレー.png"


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """解析を、呼ばれたページを記録して試験用の結果を返すものに置き換え、使い回しの記録と共有のフォルダを空にする"""
    made = []

    def analyze(page, *args, **kwargs):
        made.append(page.path)
        return pipeline.PageResult(
            quarter_turns=0,
            skew=0.0,
            orientation_confident=True,
            work=None,
            boxes=[],
            form=f"試験 {len(made)}",
        )

    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(conftest, "_ANALYZE", analyze)
    monkeypatch.setattr(conftest, "_analyzed", {})
    monkeypatch.setattr(conftest, "_store", store)
    return made


def copied(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)
    return imaging.open_pages(dest)[0]


def test_same_content_in_other_place_is_analyzed_once(calls, tmp_path):
    """場所が違っても中身が同じ試験画像は、1回だけ解析する。中身が違えば解析する"""
    first = conftest.cached_analyze(copied(PAGE, tmp_path / "a.png"))
    second = conftest.cached_analyze(copied(PAGE, tmp_path / "b" / "page.png"))
    assert calls == [tmp_path / "a.png"]
    assert second.form == first.form
    conftest.cached_analyze(copied(OTHER, tmp_path / "c.png"))
    assert len(calls) == 2


def test_result_is_shared_between_workers_through_store(calls, tmp_path):
    """共有のフォルダに置いた結果は、そのプロセスの記録がなくても（ほかのワーカー）解析せずに読む"""
    page = copied(PAGE, tmp_path / "a.png")
    first = conftest.cached_analyze(page)
    conftest._analyzed.clear()
    second = conftest.cached_analyze(page)
    assert len(calls) == 1 and second.form == first.form
    assert len(list((tmp_path / "store").glob("*.pickle"))) == 1


def test_replaced_analysis_is_neither_reused_nor_kept(calls, tmp_path, monkeypatch):
    """解析が使うモジュールの関数や定数をテストが置き換えているときは、記録した結果を返さず、解析した結果も残さない"""
    page = copied(PAGE, tmp_path / "a.png")
    conftest.cached_analyze(page)
    monkeypatch.setattr(align, "upright_group", lambda *a, **k: None)
    replaced = conftest.cached_analyze(page)
    assert len(calls) == 2 and replaced.form == "試験 2"
    assert len(conftest._analyzed) == 1
    assert len(list((tmp_path / "store").glob("*.pickle"))) == 1


def test_key_follows_page_language_and_template_image(tmp_path):
    """鍵は、ページ・向き・OCR・画面の言語と、様式の中身・見本の画像の中身で変わり、様式を置いたフォルダでは
    変わらない"""
    page = imaging.open_pages(PAGE)[0]
    engine = ocr.Engine.RAPIDOCR
    base = conftest._analysis_key(page, None, engine, bundled_forms())
    assert conftest._analysis_key(page, 1, engine, bundled_forms()) != base
    i18n.set_language(i18n.EN)
    assert conftest._analysis_key(page, None, engine, bundled_forms()) != base
    i18n.set_language(i18n.JA)
    sets = []
    for name in ("a", "b"):
        install_bundled(tmp_path / name)
        choice = formstore.FolderChoice(tmp_path / name, formstore.ORIGIN_USER)
        sets.append(formstore.load(choice).active)
    assert conftest._analysis_key(
        page, None, engine, sets[0]
    ) == conftest._analysis_key(page, None, engine, sets[1])
    image = sets[1][0].image_path()
    image.write_bytes(image.read_bytes() + b"\0")
    assert conftest._analysis_key(
        page, None, engine, sets[0]
    ) != conftest._analysis_key(page, None, engine, sets[1])
