# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""Windows で出力した試験用の PDF に、出力の検査をかける

出力の検査（墨消しした範囲が黒いか・元の文字のデータなどが残っていないか・各ページが画像1つだけか・文書の情報が
ないか）は、ツールの出力のたびには行わず、テスト（tests/pdfcheck.py）で
行う。テストは Linux で動かすが、使う PC は同じバージョンの PDFium の Windows 用の部品で動く。そのため、ライブラリ
（uv.lock）を変えて配布用パッケージを作ったら、Windows で試験用の画像（既定は tests/data/ の架空記入の扶養控除等
申告書）をツールで開き、何も変えずに出力した PDF を、これで確かめる。

墨消しした範囲は、試験用の画像をこの環境で、配布用パッケージの3様式（formstore.BUNDLED_DIR）で解析して求める（同じコードと
同じモデルと同じ様式で解析するので、Windows と同じ範囲になる見込み）。

使い方（リポジトリの直下で）:
    uv run python tools/check_output.py <Windows で出力した PDF> [<試験用の画像>]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import pdfcheck  # tests の検査を使う（tests を読み込む場所に足した後で読み込む）
from userforms import bundled_forms

from sumikeshi_studio import export, imaging, pipeline

SAMPLE = ROOT / "tests" / "data" / "架空記入_2027bun_01_200dpi_グレー.png"


def check(output: Path, source: Path = SAMPLE) -> list[str]:
    """source をツールで開いて何も変えずに出力した PDF（output）の、検査で見つかったものの一覧（空なら問題なし）"""
    jobs = [
        export.PageJob.of(page, pipeline.analyze(page, formset=bundled_forms()))
        for page in imaging.open_pages(source)
    ]
    return pdfcheck.problems(output, jobs)


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print(__doc__)
        return 2
    output = Path(argv[0])
    source = Path(argv[1]) if len(argv) == 2 else SAMPLE
    found = check(output, source)
    for item in found:
        print(f"問題：{item}")
    print("問題なし" if not found else f"問題が{len(found)}件ありました")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
