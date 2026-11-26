# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""テストで共通に使う試験画像と解析の結果

試験画像の作成と解析（1ページ数秒）は、同じ指定ならテスト全体で1回だけ行う。解析の結果は、pytest-xdist の
ワーカーのあいだでも、1回の実行の中で使い回す（cached_analyze）。
解析の結果はテストの中で書き換える（解除など）ため、使うたびに複製して渡す。
"""

import copy
import dataclasses
import hashlib
import inspect
import os
import pickle
import sys
import tempfile
import threading
import types
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import netguard
import pytest
from PIL import Image
from scan import SAMPLE_SCAN, make_scan, page_image
from userforms import bundled_forms, install_bundled

from sumikeshi_studio import (
    align,
    bend,
    boxes,
    formcheck,
    forms,
    formstore,
    frames,
    i18n,
    imaging,
    ocr,
    orientation,
    paper,
    pipeline,
    rowshift,
    rulings,
    titles,
    variants,
)

try:
    import fcntl
except ImportError:  # Windows：ワーカーのあいだでは共有せず、プロセスの中だけで使い回す
    fcntl = None

# テストのあいだ、インターネットへの通信を禁じる（netguard.py）。conftest.py は pytest-xdist の各ワーカーでも
# 読み込まれるので、どのワーカーでも効く
netguard.install()
# onnxruntime の利用状況の記録と送信を、テストのプロセスと、そこから起動するプロセスでも止める（ツールは
# ocr._engine で止めるが、テストが onnxruntime を先に読み込んでも止まるように）
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

# 見本のスキャンを使うテストは、見本がなければ飛ばす
needs_sample_scan = pytest.mark.skipif(
    not SAMPLE_SCAN.exists(),
    reason="見本のスキャン（samples/fictional_entries/sample_カラー原色.pdf）がない",
)


@pytest.fixture(autouse=True)
def editor_step_errors():
    """様式の編集の画面を操作するテスト（gui.drive_editor）の steps で起きた例外を、テストの後に出す（Qt から呼ばれる
    処理の中の例外は、そのままではテストを失敗にしないため）"""
    yield
    gui = sys.modules.get("gui")
    if gui is not None and gui.STEP_ERRORS:
        errors = list(gui.STEP_ERRORS)
        gui.STEP_ERRORS.clear()
        raise errors[0]


@pytest.fixture(autouse=True)
def no_network_attempts():
    """テストの中でインターネットへ通信しようとしたら、例外を受け止めて続けるコードがあっても、そのテストを失敗にする"""
    yield
    tried = netguard.pop_attempts()
    if tried:
        pytest.fail(
            "テストの中でインターネットへ通信しようとしました：" + "、".join(tried),
            pytrace=False,
        )


# テストでは、作業ログと異常終了の記録を、利用者の場所に書かない。本物の解析のプロセスなど、テストが起動する
# 子のプロセスも、この環境変数を引き継ぐ（確認画面のテストは、テストごとの場所に変える）
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sumikeshi-studio-test-")
# 一度に1つだけ起動するためのローカルサーバーの名前も、テストの中だけのものにする（使っているツールに知らせないため）
os.environ["SUMIKESHI_STUDIO_INSTANCE_NAME"] = f"sumikeshi-studio-test-{os.getpid()}"
# 画面の言語は日本語にする（テストは日本語の文を確かめる。この環境の言語や設定によらないように。子のプロセスも
# 引き継ぐ）
os.environ[i18n.LANGUAGE_ENV] = i18n.JA

_MAKE_SCAN = inspect.signature(make_scan)
_ANALYZE = pipeline.analyze  # 確認画面のテストで置き換える前の解析
_analyzed: dict[
    str, pipeline.PageResult
] = {}  # 鍵（_analysis_key）→ 結果（このプロセスで読んだもの）
# ワーカーのあいだで解析の結果を共有するフォルダ（1回の実行で1つ。_analysis_store が決める）
_store: Path | None = None
# 解析が使うモジュールの関数・クラス・定数の、テストが置き換える前の値。置き換えたテストの解析の結果は、ほかの
# テストの結果と同じとは限らないので、使い回さない（同じ中身の画像を、位置合わせを置き換えて解析し直すテストがある）
_ANALYSIS_MODULES = (
    align,
    bend,
    boxes,
    formcheck,
    forms,
    formstore,
    frames,
    imaging,
    ocr,
    orientation,
    paper,
    pipeline,
    rowshift,
    rulings,
    titles,
    variants,
)
_VALUES = (int, float, str, bytes, tuple, frozenset)


def _watched(value) -> bool:
    return (callable(value) or isinstance(value, _VALUES)) and not isinstance(
        value, types.ModuleType
    )


_ORIGINAL = [
    (m, {k: v for k, v in vars(m).items() if not k.startswith("__") and _watched(v)})
    for m in _ANALYSIS_MODULES
]
_digests: dict[tuple, str] = {}  # (ファイルの場所, 大きさ, 更新の時刻) → 中身の SHA-256


def _spec_key(form: str, spec: dict) -> tuple:
    """make_scan の指定を、省いた値も含めてそろえた形にする"""
    bound = _MAKE_SCAN.bind_partial(form, None, **spec)
    bound.apply_defaults()
    return tuple((k, v) for k, v in bound.arguments.items() if k != "out")


def _without_folder(spec):
    return dataclasses.replace(spec, folder="")


def _digest(path: Path) -> str:
    """ファイルの中身の SHA-256（大きさと更新の時刻が同じ間は、求め直さない）"""
    st = os.stat(path)
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _digests:
        _digests[key] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return _digests[key]


def _analysis_key(page, quarter_turns, engine, formset) -> str:
    """解析の結果を使い回す鍵：試験画像の中身・ページ・向き・OCR・画面の言語（解析の注記はその言語の文）と、様式の
    組。様式は、置いた様式のフォルダを除いた中身と、見本の画像の中身で見分ける（画面のテストと回帰テストは、
    テストやプロセスごとのフォルダに様式を置くため）。どのプロセスでも同じ値になる形で作る"""
    h = hashlib.sha256()
    h.update(_digest(page.path).encode())
    h.update(repr((page.index, quarter_turns, str(engine), i18n.language())).encode())
    for s in formset:
        h.update(repr(_without_folder(s)).encode())
        h.update(_digest(s.image_path()).encode())
    return h.hexdigest()


def _analysis_replaced() -> bool:
    """解析が使うモジュールの関数・クラス・定数を、テストが置き換えているか（pipeline.analyze は除く。画面の
    テストは、使い回すために cached_analyze に置き換える）"""
    for module, original in _ORIGINAL:
        now = vars(module)
        for name, value in original.items():
            if now.get(name) is not value and not (
                module is pipeline and name == "analyze"
            ):
                return True
    return False


def _shared(key: str, compute):
    """ワーカーのあいだで共有した結果（なければ compute で求めて置く）。鍵ごとのロックを取るので、同じ鍵の解析は
    1回の実行で1回だけ行う"""
    if _store is None or fcntl is None:
        return compute()
    path = _store / f"{key}.pickle"
    with open(_store / f"{key}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            return pickle.loads(path.read_bytes())
        res = compute()
        part = path.with_suffix(f".{os.getpid()}.part")
        part.write_bytes(pickle.dumps(res))
        os.replace(part, path)
        return res


def cached_analyze(
    page: imaging.SourcePage,
    quarter_turns: int | None = None,
    engine: ocr.Engine = ocr.Engine.RAPIDOCR,
    formset=None,
) -> pipeline.PageResult:
    """pipeline.analyze と同じ（formset を省くと、配布用パッケージの3様式）。同じ中身の試験画像・ページ・指定・様式の
    2回目からは、記録した結果の複製を返す（_analysis_key。ワーカーのあいだでも共有する）

    結果の使った様式と類似した様式は、渡した様式の組のものに置き換える。メモリの上のページと、解析が使うモジュールを
    テストが置き換えているとき（_analysis_replaced）は、使い回さずに解析する。
    """
    if formset is None:
        formset = bundled_forms()
    if isinstance(page, imaging.MemoryPage) or _analysis_replaced():
        return _ANALYZE(page, quarter_turns, engine, formset=formset)
    key = _analysis_key(page, quarter_turns, engine, formset)
    if key not in _analyzed:
        _analyzed[key] = _shared(
            key, lambda: _ANALYZE(page, quarter_turns, engine, formset=formset)
        )
    res = copy.deepcopy(_analyzed[key])
    given = {_without_folder(s): s for s in formset}
    res.used = tuple(given[_without_folder(s)] for s in res.used)
    res.similar = tuple(given[_without_folder(s)] for s in res.similar)
    return res


@pytest.fixture(scope="session", autouse=True)
def _analysis_store(tmp_path_factory):
    """解析の結果を共有するフォルダを決める。pytest-xdist のワーカーは、1回の実行で同じ親のフォルダの下にそれぞれの
    一時フォルダを持つので、その親の下に置く（ワーカーなしで流すときは、その実行の一時フォルダの下）"""
    global _store
    base = tmp_path_factory.getbasetemp()
    root = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    _store = root / "analysis-cache"
    _store.mkdir(exist_ok=True)
    yield
    _store = None


@pytest.fixture(autouse=True)
def _japanese():
    """テストごとに画面の言語を日本語に戻す（英語にしたテストの後のテストに、英語を残さない）"""
    i18n.set_language(i18n.JA)


@pytest.fixture(scope="session")
def scan(tmp_path_factory):
    """scan(form, **make_scan の指定) で試験画像のPDFを返す。同じ指定なら同じファイル"""
    made: dict[tuple, object] = {}

    def get(form: str, **spec):
        key = _spec_key(form, spec)
        if key not in made:
            out = tmp_path_factory.mktemp(form) / "scan.pdf"
            made[key] = make_scan(form, out, **spec)
        return made[key]

    return get


@pytest.fixture(scope="session")
def analyzed(scan):
    """analyzed(form, engine=..., **make_scan の指定) で、試験画像の1ページ目と解析の結果を返す

    結果は複製なので、テストの中で書き換えてよい。
    """

    def get(form: str, engine: ocr.Engine = ocr.Engine.RAPIDOCR, **spec):
        page = imaging.open_pages(scan(form, **spec))[0]
        return page, cached_analyze(page, engine=engine)

    return get


@pytest.fixture(scope="session")
def reference(tmp_path_factory):
    """reference(form, filled) で、ページの画像（200dpi）と、それを解析した墨消しする範囲を返す

    傾けずに画像にしたので、作業画像の座標はページの画像の座標と同じになる（field_coverage.py で使う）。
    """
    made = {}

    def get(form, filled=False):
        if (form, filled) not in made:
            img = page_image(form, filled)
            path = tmp_path_factory.mktemp(form) / "page.png"
            Image.fromarray(img).save(path, dpi=(200, 200))
            res = cached_analyze(imaging.open_pages(path)[0])
            assert res.quarter_turns == 0 and abs(res.skew) < 0.05
            made[form, filled] = (img, res.boxes)
        return made[form, filled]

    return get


class BreakingPool:
    """解析のプロセスの executor の代わり：依頼をスレッドで動かし、依頼の1つが BrokenProcessPool を出したら、本物の
    解析のプロセスが途中で終わったときと同じく、そのとき出していたほかの依頼もすべて BrokenProcessPool にする（executor
    全体が壊れる）。失敗させるのは、本物（ProcessPoolExecutor の pending_work_items）と同じく出した順。壊れた後は依頼を
    受け付けない。巻き込まれた依頼のスレッドは動き続けるが、結果は届かない。shutdown(cancel_futures=True) は、本物と
    同じく、まだ動き始めていない依頼だけを取り消す（ThreadPoolExecutor をそのまま使うと、壊れた後に待っていた依頼が、
    失敗でなく取り消しで終わる）"""

    def __init__(self, n):
        self.pool = ThreadPoolExecutor(n)
        self.pending: dict[Future, None] = {}  # 出した順
        self.broken = False
        self.lock = threading.Lock()

    def submit(self, fn, *args):
        with self.lock:
            if self.broken:
                raise BrokenProcessPool("解析のプロセスが終わった後")
            outer = Future()
            self.pending[outer] = None

        def run():
            with self.lock:
                if outer not in self.pending:
                    return  # ほかの依頼で壊れたか、取り消された
                outer.set_running_or_notify_cancel()
            try:
                result = fn(*args)
            except BrokenProcessPool:
                with self.lock:
                    self.broken = True
                    victims = list(self.pending)
                    self.pending.clear()
                for f in victims:
                    if not f.done():
                        f.set_exception(BrokenProcessPool("解析のプロセスが終わった"))
                return
            except BaseException as e:  # noqa: BLE001  依頼の失敗として届ける
                with self.lock:
                    if self.pending.pop(outer, False) is False:
                        return  # ほかの依頼で壊れた後に終わった（結果は届かない）
                outer.set_exception(e)
                return
            with self.lock:
                if outer not in self.pending:
                    return  # ほかの依頼で壊れた後に終わった（結果は届かない）
                self.pending.pop(outer, None)
            outer.set_result(result)

        self.pool.submit(run)
        return outer

    def shutdown(self, wait=True, cancel_futures=False):
        if cancel_futures:
            with self.lock:
                waiting = [f for f in self.pending if not f.running()]
                for f in waiting:
                    del self.pending[f]
            for f in waiting:
                f.cancel()
        self.pool.shutdown(wait=False, cancel_futures=cancel_futures)


# ---- 画面のテストのフィクスチャ
# このファイルは画面の部品（PySide6）を読み込めない環境でも読み込むので、PySide6・画面のモジュール・tests/gui.py は、
# フィクスチャの中で読み込む。画面のテストのファイルは tests/gui.py を読み込み、その環境では飛ばされる。


@pytest.fixture
def gui_env(tmp_path, monkeypatch):
    """画面のテストの環境（作業ログ・設定・様式の既定の場所はテストごと。解析はスレッドで行う）。
    出したメッセージの文の一覧と、QApplication を返す"""
    from gui import QSettings, QtWidgets

    from sumikeshi_studio import (
        depthdialog,
        formstext,
        formswin,
        guigc,
        reviewtext,
        widgets,
        workers,
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    # 様式の既定の場所に、導入で入れるのと同じ配布用パッケージの3様式を置く
    install_bundled(formstore.default_folder())
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "ini")
    )
    # 同じ試験画像の解析は、テスト全体で1回だけ行う。置き換えた解析を使うため、解析は解析のプロセスでは
    # なくスレッドで行う（解析のプロセスでの動きは、test_workers.py と、下の画面のプロセスのテストで確かめる）
    monkeypatch.setattr(pipeline, "analyze", cached_analyze)
    monkeypatch.setenv(workers.ENV_PROCESSES, "1")
    monkeypatch.setattr(workers, "new_executor", BreakingPool)
    shown = []
    box = QtWidgets.QMessageBox
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(box, name, lambda *a, **k: shown.append(a[2]))
    # 「はい」「いいえ」の確認は「はい」で答える（本物と同じく、答えるあいだはダイアログを出している扱いにする）

    def ask(*a, **k):
        with widgets.dialog():
            return True

    monkeypatch.setattr(widgets, "ask", ask)
    # 確認のメッセージ（既定のボタンが「しない」側のもの）は「する」で答え、問いの文を残す。ボタンが3つ以上の
    # 確認のメッセージは最初のボタンで答え、本文と説明のあるメッセージは本文を残す
    monkeypatch.setattr(widgets, "confirm", lambda *a, **k: shown.append(a[2]) or True)
    monkeypatch.setattr(widgets, "choose", lambda *a, **k: shown.append(a[2]) or 0)
    monkeypatch.setattr(widgets, "message", lambda *a, **k: shown.append(a[2]))
    # 登録を始めるときに、類似した様式があったときのダイアログは「新しい様式として登録を続ける」で答え、文を残す
    monkeypatch.setattr(
        formswin,
        "similar_choice",
        lambda parent, similar: (
            shown.append(formstext.similar_text(similar)) or formswin.CONTINUE
        ),
    )
    # 深さを選ぶダイアログは、問いの文を残し「直下だけ」で開く（選ぶ深さを変えるテストは、置き換え直す）
    monkeypatch.setattr(
        depthdialog,
        "choose_depth",
        lambda parent, selection: shown.append(reviewtext.DEPTH_QUESTION) or (True, 0),
    )
    # 出力の結果のダイアログ（QDialog）も、文を残してすぐに閉じる
    monkeypatch.setattr(
        widgets.ResultDialog, "exec", lambda self: shown.append(self.text) or 0
    )
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # 画面のテストのあいだは、本物のツールと同じく、ガベージコレクションを画面のスレッドで行う。解析のスレッドで
    # ガベージコレクションが動くと、前のテストの画面をそのスレッドで消して、強制終了することがあるため。
    # 終わったら、画面のスレッドで残りを回収してから、自動のガベージコレクションに戻す（画面を使わないテストは変えない）
    collector = guigc.GarbageCollector()
    collector.start()
    yield shown, qapp
    collector.stop()


@pytest.fixture
def new_window(gui_env):
    """墨消しの画面を作る関数（自分で解析のプロセスを持つ）。wait_ready=False なら、起動の準備が
    終わるのを待たずに返す"""
    from gui import settle, shiboken6, wait

    from sumikeshi_studio import app

    shown, qapp = gui_env
    made = []

    def make(wait_ready=True):
        win = app.MainWindow()
        win.resize(1400, 900)
        win.show()
        win.shown = shown
        win.qapp = qapp
        made.append(win)
        if wait_ready:
            wait(win, lambda: win.ready)
        return win

    yield make
    for win in made:
        if not shiboken6.isValid(win):  # 閉じて消えた画面（WA_DeleteOnClose）
            continue
        settle(win)
        win.close()  # 終了の確認は「はい」で答える


@pytest.fixture
def new_launcher(gui_env):
    """起動の画面を作る関数。起動の準備と、様式の読み込みが終わるまで待って返す"""
    from gui import settle, wait

    from sumikeshi_studio import launcher

    shown, qapp = gui_env
    made = []

    def make():
        win = launcher.LauncherWindow()
        win.show()
        win.shown = shown
        win.qapp = qapp
        made.append(win)
        wait(
            win,
            lambda: win.session.ready and win.loaded is not None and not win.loading,
        )
        return win

    yield make
    for win in made:
        settle(win)
        if win.child is not None:
            win.child.close()
        win.close()


@pytest.fixture
def window(new_window):
    return new_window()


@pytest.fixture
def files(scan):
    """記入した扶養控除等申告書（1ページ）と、記入なしの保険料控除申告書（1ページ）"""
    return scan("2027bun_01", filled=True), scan("2026bun_04")


@pytest.fixture
def two_pages(files, tmp_path):
    """記入した扶養控除等申告書と、記入なしの保険料控除申告書をつないだ2ページの PDF"""
    import pypdfium2 as pdfium

    path = tmp_path / "二ページ.pdf"
    out = pdfium.PdfDocument.new()
    for f in files:
        src = pdfium.PdfDocument(f)
        out.import_pages(src)
        src.close()
    out.save(path)
    out.close()
    return path
