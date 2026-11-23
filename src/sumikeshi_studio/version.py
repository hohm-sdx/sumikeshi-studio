# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ツールのバージョンと、配布用パッケージを作った日時の文

異常終了の記録の起動の行、様式を保存したツールのバージョン（様式の .json の saved_with）、
起動の画面の上のバージョンに使う。
"""

import sys
from pathlib import Path

from . import __version__
from .i18n import tr

# 配布用パッケージの、バージョンと作った日時を書いたファイル（tools/make_dist.py が作る）。前の配布用パッケージの
# 「バージョン.txt」は読まない（新しい配布用パッケージには version.txt が入る）
VERSION_FILE = "version.txt"
MADE_PREFIX = "Built: "  # version.txt の、作った日時の行の始まり


def _version_file() -> str | None:
    """配布用パッケージの version.txt の中身（python フォルダの隣。開発用の環境ではないので None）"""
    try:
        return (Path(sys.base_prefix).parent / VERSION_FILE).read_text("utf-8-sig")
    except OSError:
        return None


def made_at() -> str | None:
    """配布用パッケージを作った日時（例：2026-11-08 08:10）。開発用の環境や、書いていなければ None"""
    text = _version_file() or ""
    line = next((s for s in text.splitlines() if s.startswith(MADE_PREFIX)), "")
    return line.removeprefix(MADE_PREFIX) or None


def version_text() -> str:
    """ツールのバージョンと、配布用パッケージの version.txt の作った日時。異常終了の記録と、様式の .json の
    saved_with に書くので英語にする"""
    text = _version_file()
    if text is None:
        return f"{__version__} (no {VERSION_FILE})"
    made = made_at()
    return (
        f"{__version__} (built {made})"
        if made
        else f"{__version__} (no build time in {VERSION_FILE})"
    )


def version_label() -> str:
    """起動の画面の上に出すバージョン（配布用パッケージなら作った日時も）"""
    made = made_at()
    if made:
        return tr("バージョン {version}（{made} 作成）", version=__version__, made=made)
    return tr("バージョン {version}（開発用の環境）", version=__version__)
