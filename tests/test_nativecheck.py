# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ツールの部品を Windows が読み込めるかの確認（nativecheck.py）のテスト。Windows の関数は呼ばず、読み込む関数を
置き換えて確かめる。ツールが使わないとした部品（NOT_USED）を読み込まないことは、Linux で別のプロセスを動かして
確かめる"""

import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from sumikeshi_studio import i18n, nativecheck

BLOCKED_TEXT = "アプリケーション制御ポリシーによってこのファイルがブロックされました。"


def make_python(root: Path) -> Path:
    python = root / "python"
    site = python / "Lib" / "site-packages"
    for rel in (
        "python314.dll",
        "_ctypes.pyd",
        "python.exe",  # .exe は確かめない
        "Lib/site-packages/numpy/_core/_multiarray_umath.cp314-win_amd64.pyd",
        "Lib/site-packages/numpy/random/mtrand.cp314-win_amd64.pyd",
        "Lib/site-packages/numpy.libs/libscipy_openblas64_-ed4f.dll",
        "Lib/site-packages/google/_upb/_message.pyd",
        "Lib/site-packages/numpy/__init__.py",
    ):
        p = python / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    assert site.is_dir()
    return python


def test_blocked_parts_and_report(tmp_path):
    """止められた部品（エラーの番号 4551）を、ツールが使う部品と使わない部品に分けて示す。ツールが使う部品が
    止められたら 20、使わない部品だけなら 10、なければ 0 で終わる"""
    python = make_python(tmp_path)
    assert sorted(p.name for p in nativecheck.parts(python)) == [
        "_ctypes.pyd",
        "_message.pyd",
        "_multiarray_umath.cp314-win_amd64.pyd",
        "libscipy_openblas64_-ed4f.dll",
        "mtrand.cp314-win_amd64.pyd",
        "python314.dll",
    ]
    blocked_names = {"libscipy_openblas64_-ed4f.dll", "mtrand.cp314-win_amd64.pyd"}
    loads = []

    def load(p):
        loads.append(p.name)
        if p.name == "_ctypes.pyd":
            return 126  # ほかの理由で読み込めない（ここでは扱わない）
        return nativecheck.POLICY_BLOCKED if p.name in blocked_names else 0

    blocked = nativecheck.blocked_parts(python, load)
    assert len(loads) == 6 and {p.name for p in blocked} == blocked_names
    code, text = nativecheck.report(python, blocked)
    assert code == nativecheck.USED_BLOCKED == 20
    head = (
        "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
    )
    assert text.splitlines() == [
        f"{head}ツールが使う次の部品を止めました。このままでは、ツールは起動しないか、途中で止まります。"
        + "この画面のスクリーンショットを撮っておくと、原因の調査に役立つ場合があります。",
        "・numpy：libscipy_openblas64_-ed4f.dll",
        f"{head}ツールが使わない次の部品を止めました。ツールの動きには影響しません。",
        "・numpy：mtrand.cp314-win_amd64.pyd",
    ]
    unused = [p for p in blocked if p.name.startswith("mtrand")]
    assert nativecheck.report(python, unused)[0] == nativecheck.ONLY_UNUSED == 10
    assert nativecheck.report(python, []) == (0, "")
    assert (
        nativecheck.label(python, python / "python314.dll") == "Python：python314.dll"
    )
    assert not nativecheck.used(
        python, python / "Lib/site-packages/google/_upb/_message.pyd"
    )


def test_policy_message():
    """起動の準備の失敗が、アプリケーション制御に止められたものなら、止められた部品の名前を入れて画面に出す"""
    error = f"ImportError: DLL load failed while importing _multiarray_umath: {BLOCKED_TEXT}"
    assert nativecheck.policy_message(error) == (
        "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、ツールの部品"
        "（_multiarray_umath）を止めました。配布用パッケージの ZIP を展開して setup.bat を実行すると、"
        "止められた部品の名前が出ます。"
        "この画面のスクリーンショットを撮っておくと、原因の調査に役立つ場合があります。"
    )
    assert "ツールの部品を止めました" in nativecheck.policy_message(
        f"OSError: [WinError 4551] {BLOCKED_TEXT}"
    )
    assert nativecheck.policy_message("ImportError: No module named 'numpy'") is None


def test_main_skips_outside_windows(capsys):
    """Windows ではないときは確かめない"""
    assert nativecheck.main() == 0
    assert capsys.readouterr().out == "Windows ではないため、確かめません\n"


# ---- ツールが使わないとした部品を、ツールの処理が読み込まないこと

# 別のプロセスで動かす処理：画面のモジュールを読み込み、解析の準備（OCR）、配布用パッケージの3様式でのページの解析と出力を行い、読み込んだ
# site-packages のファイル（/proc/self/maps に載ったもの）を、site-packages からのパスで1行ずつ書く
LOADED_PARTS = """
import sys, sysconfig
from pathlib import Path
import sumikeshi_studio.launcher
from sumikeshi_studio import analysis, export, formstore, imaging, pipeline, workers
workers.process_count()
analysis.prepare()
forms = tuple(formstore.read_spec(p) for p in formstore.BUNDLED_DIR.glob("*.json"))
out = Path(sys.argv[1])
for i, src in enumerate(sys.argv[2:]):
    page = imaging.open_pages(Path(src))[0]
    export.export([(page, pipeline.analyze(page, formset=forms))], out / f"{i}.pdf")
site = Path(sysconfig.get_paths()["platlib"]).resolve()
loaded = set()
for line in open("/proc/self/maps"):
    parts = line.split(maxsplit=5)
    if len(parts) == 6 and parts[5].strip().startswith(f"{site}/"):
        loaded.add(Path(parts[5].strip()).relative_to(site).as_posix())
print("\\n".join(sorted(loaded)))
"""


@pytest.mark.skipif(
    not Path("/proc/self/maps").exists(),
    reason="/proc/self/maps がない（Linux で確かめる）",
)
def test_tool_does_not_load_not_used_parts(scan, tmp_path):
    """ツールが使わないとした部品（NOT_USED）を、ツールの処理が読み込まない

    別のプロセスで、画面のモジュールを読み込み、解析の準備（OCR と見本）を行い、配布用パッケージの3様式のページと見本のないページ
    （OCR で向きを判定する）を解析・出力して、そのプロセスが読み込んだ site-packages のファイルを調べる。
    Linux で読み込まれたもので確かめる（Windows で同じかは確かめていない）。
    """
    pytest.importorskip(
        "PySide6.QtWidgets", reason="画面の部品を読み込めない", exc_type=ImportError
    )
    pages = [scan("2027bun_01", filled=True), scan("2026bun_08")]
    out = subprocess.run(
        [sys.executable, "-c", LOADED_PARTS, str(tmp_path), *map(str, pages)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    loaded = [PurePosixPath(line) for line in out.splitlines()]
    # 解析と出力の部品を読み込んだこと（調べ方が働いていること）
    assert any(p.parts[0] == "onnxruntime" for p in loaded)
    assert any(p.parts[0] == "cv2" for p in loaded)
    assert any(p.parts[0] == "pypdfium2_raw" for p in loaded)
    assert [
        str(p) for p in loaded if any(p.full_match(n) for n in nativecheck.NOT_USED)
    ] == []


def test_texts_in_english(tmp_path, monkeypatch, capsys):
    """英語の画面と英語の導入では、止められた部品を知らせる文を英語にする。導入（install.ps1）は、決めた言語を
    環境変数で渡す"""
    python = make_python(tmp_path)
    blocked = [
        python / "_ctypes.pyd",
        python / "Lib/site-packages/numpy/random/mtrand.cp314-win_amd64.pyd",
    ]
    i18n.set_language(i18n.EN)
    code, text = nativecheck.report(python, blocked)
    assert code == nativecheck.USED_BLOCKED
    assert text.splitlines() == [
        (
            "Windows Smart App Control (or your organization's application control) blocked the following components "
            "that the tool uses. As it is, the tool will not start or will stop partway. "
            "A screenshot of this window may help investigate the cause."
        ),
        "- Python: _ctypes.pyd",
        (
            "Windows Smart App Control (or your organization's application control) blocked the following components "
            "that the tool does not use. This does not affect how the tool works."
        ),
        "- numpy: mtrand.cp314-win_amd64.pyd",
    ]
    assert nativecheck.policy_message(
        f"OSError: [WinError 4551] {BLOCKED_TEXT}"
    ).startswith(
        "Windows Smart App Control (or your organization's application control) blocked a component of the tool. "
    )
    i18n.set_language(i18n.JA)
    monkeypatch.setenv(i18n.LANGUAGE_ENV, "en")
    assert nativecheck.main() == 0
    assert capsys.readouterr().out == "Not checked, because this is not Windows\n"
