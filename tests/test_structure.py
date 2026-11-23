# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""モジュールの依存の決まりのテスト

src/sumikeshi_studio の .py を AST で読み（モジュールは読み込まない）、sumikeshi_studio の中の import を集めて、次の決まりを
確かめる。ファイルの上部・関数の中・TYPE_CHECKING の中の import を、すべて数える（TYPE_CHECKING の中は実行されないが、
型として依存しているため）。

- 循環しない。
- Qt を使わない層（層4〜7）のモジュールは、PySide6・shiboken6 をファイルの上部で読み込まない。関数の中で読み込んで
  よいのは、QT_INSIDE の関数だけ。
- 下の層から上の層を読み込まない。同じ層の中は読み込んでよいが、画面の層（層2）のモジュールどうしは読み込まない。
- RESTRICTED のモジュールは、決めたモジュールからだけ読み込む。
- すべてのモジュールが、ちょうど1つの層に入っている。

あわせて、tests・tools の .py も AST で読み、次のことを確かめる。

- テストが monkeypatch.setattr で置き換える名前を、src が from … import でコピーしない。
- src・tests・tools の「モジュール.名前」の名前と、from … import で取り込む名前が、そのモジュールにある。
- 仕様の文書（docs/23-spec.md）の「2. 構成」の表に、src のモジュールがちょうど1回ずつある（仕様の文書がなければ
  飛ばす）。
- 英語の表（i18n_en.EN）の鍵が src にあり、鍵と値とで {name} が同じで、値に日本語がない。src の日本語の文が、英語の
  表にあるか、日本語のまま残す一覧にある（まだ英語にしていないモジュールを除く）。

層ごとのモジュールの一覧は、このファイルの LAYERS が正。
"""

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "sumikeshi_studio"
# ソースの先頭に置く、著作権とライセンス（SPDX の形）の2行
LICENSE_HEADER = (
    "Copyright (C) 2026 Hideki Ohmura",
    "SPDX-License-Identifier: AGPL-3.0-only",
)
TESTS = Path(__file__).resolve().parent
PACKAGE = "sumikeshi_studio"

# 層（上から）。上の層は下の層を読み込んでよい。新しいモジュールは、役目に合う層に足す
LAYERS = [
    # forminstall は導入（install.ps1）から起動する（様式のフォルダを、画面と同じ設定から決める）。cli はコマンドラインからの
    # 出力（画面を出さない）
    ("起動", {"__main__", "launcher", "forminstall", "cli"}),
    ("画面（ウィンドウ）", {"app", "formswin"}),
    (
        "画面の部品と裏で動かす処理",
        {
            "formeditor",
            "formlist",
            "depthdialog",
            "exportflow",
            "prefs",
            "widgets",
            "pageview",
            "commands",
            "session",
            "workers",
            "guigc",
            "instance",
            "appicon",
        },
    ),
    (
        "解析の依頼と画面のデータ",
        {
            "review",
            "reviewtext",
            "workcache",
            "analysis",
            "export",
            "exporttext",
            "formstext",
            "uitext",
        },
    ),
    (
        "解析",
        {
            "pipeline",
            "forms",
            "align",
            "bend",
            "paper",
            "titles",
            "formcheck",
            "orientation",
            "frames",
            "imaging",
            "ocr",
            "rulings",
            "variants",
            "rowshift",
        },
    ),
    (
        "データの型と Windows の部品",
        {
            "boxes",
            "filetypes",
            "folders",
            "formstore",
            "formsave",
            "nativecheck",
            "winjob",
        },
    ),
    (
        "記録とバージョン",
        {"crashlog", "worklog", "datadir", "version", "i18n", "i18n_en", "__init__"},
    ),
]
QT_FREE_FROM = 4  # この番号（1始まり）以降の層は Qt を使わない
NO_SIBLINGS = {2}  # この番号の層では、モジュールどうしを読み込まない
QT_PACKAGES = {"PySide6", "shiboken6"}
# Qt を使わない層で、関数の中で Qt を読み込んでよい関数（モジュール, 関数）
QT_INSIDE = {("crashlog", "install_qt_handler")}
# 読み込む相手を限るモジュールと、読み込んでよいモジュール
RESTRICTED = {
    # 異常終了の記録：プロセスを始めるときの設定をするモジュール
    "crashlog": {"launcher", "analysis", "cli"},
    # 解析のプロセスなどをまとめたもの：Session を作る・受け取るウィンドウと、コマンドラインからの出力
    "session": {"launcher", "app", "formswin", "cli"},
}
# 直すまでの例外：(読み込むモジュール, 読み込まれるモジュール) と、直す段階。
# 例外を足すのは最後の手段にし、直す段階を書いてから足す。
EXCEPTIONS: dict[tuple[str, str], str] = {}


@dataclass(frozen=True)
class Import:
    source: str  # 読み込むモジュール
    target: str  # 読み込まれるもの（パッケージの中のモジュールの名前か、外のパッケージの名前）
    where: str  # 読み込む関数（クラスの中なら「クラス.関数」）。ファイルの上部なら空


def _targets(node: ast.Import | ast.ImportFrom, modules: set[str]) -> list[str]:
    """import の文が読み込むもの。パッケージから名前を読み込むとき（from . import APP_NAME）は __init__"""

    def inside(names: list[str]) -> list[str]:
        return [n if n in modules else "__init__" for n in names]

    if isinstance(node, ast.Import):
        found = []
        for a in node.names:
            parts = a.name.split(".")
            if parts[0] == PACKAGE:
                found.append(parts[1] if len(parts) > 1 else "__init__")
            else:
                found.append(parts[0])
        return found
    names = [a.name for a in node.names]
    if node.level == 1:
        return [node.module.split(".")[0]] if node.module else inside(names)
    if node.level == 0 and node.module:
        parts = node.module.split(".")
        if parts[0] != PACKAGE:
            return [parts[0]]
        return [parts[1]] if len(parts) > 1 else inside(names)
    raise AssertionError(f"扱えない import の形です（{ast.unparse(node)}）")


def imports(src: Path) -> tuple[set[str], list[Import]]:
    """パッケージのモジュールの名前と、すべての import"""
    paths = sorted(src.rglob("*.py"))
    modules = {".".join(p.relative_to(src).with_suffix("").parts) for p in paths}
    found = []

    def walk(node: ast.AST, source: str, stack: list[str], in_function: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                where = ".".join(stack) if in_function else ""
                for target in _targets(child, modules):
                    found.append(Import(source, target, where))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, source, [*stack, child.name], True)
            elif isinstance(child, ast.ClassDef):
                walk(child, source, [*stack, child.name], in_function)
            else:
                walk(child, source, stack, in_function)

    for p in paths:
        source = ".".join(p.relative_to(src).with_suffix("").parts)
        walk(ast.parse(p.read_text("utf-8")), source, [], False)
    return modules, found


def _cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """循環の1つ（モジュールの並び）。なければ None"""
    state: dict[str, int] = {}  # 1：たどっている途中、2：たどり終えた
    path: list[str] = []

    def visit(m: str) -> list[str] | None:
        state[m] = 1
        path.append(m)
        for n in sorted(graph.get(m, ())):
            if state.get(n) == 1:
                return path[path.index(n) :]
            if n not in state and (found := visit(n)):
                return found
        path.pop()
        state[m] = 2
        return None

    for m in sorted(graph):
        if m not in state and (found := visit(m)):
            return found
    return None


def check(
    src: Path,
    layers=LAYERS,
    *,
    qt_free_from=QT_FREE_FROM,
    no_siblings=NO_SIBLINGS,
    restricted=RESTRICTED,
    exceptions=EXCEPTIONS,
    qt_inside=QT_INSIDE,
) -> list[str]:
    """決まりに合わない点の文の一覧（空なら、すべての決まりに合う）"""
    modules, found = imports(src)
    problems = []
    layer_of: dict[str, int] = {}
    for number, (_, members) in enumerate(layers, 1):
        for m in sorted(members):
            if m in layer_of:
                problems.append(
                    f"{m} が2つの層（層{layer_of[m]}と層{number}）に入っています"
                )
            layer_of[m] = number
    for m in sorted(modules - layer_of.keys()):
        problems.append(
            f"{m} がどの層にも入っていません。tests/test_structure.py の LAYERS の、役目に合う層に足してください"
        )
    for m in sorted(layer_of.keys() - modules):
        problems.append(
            f"層の一覧にある {m} が、パッケージにありません。LAYERS から消すか、名前を直してください"
        )

    def layer(m: str) -> str:
        return f"層{layer_of[m]}「{layers[layer_of[m] - 1][0]}」"

    edges = {
        (i.source, i.target)
        for i in found
        if i.target in modules and i.target != i.source
    }
    for (a, b), stage in sorted(exceptions.items()):
        if (a, b) not in edges:
            problems.append(
                f"{a} → {b} は、{stage}で直す例外として一覧にありますが、もう読み込んでいません。"
                "tests/test_structure.py の EXCEPTIONS から消してください"
            )
    for a, b in sorted(edges - exceptions.keys()):
        if a in layer_of and b in layer_of:
            if layer_of[a] > layer_of[b]:
                problems.append(
                    f"{a} が {b} を読み込んでいます（{a} は{layer(a)}、{b} は{layer(b)}。"
                    "下の層から上の層は読み込みません）"
                )
            elif layer_of[a] == layer_of[b] and layer_of[a] in no_siblings:
                problems.append(
                    f"{a} が {b} を読み込んでいます（どちらも{layer(a)}。"
                    "この層のモジュールどうしは読み込みません）"
                )
        if b in restricted and a not in restricted[b]:
            problems.append(
                f"{a} が {b} を読み込んでいます（{b} を読み込んでよいのは、"
                f"{'・'.join(sorted(restricted[b]))} だけです）"
            )
    for i in found:
        if i.target not in QT_PACKAGES or layer_of.get(i.source, 0) < qt_free_from:
            continue
        if not i.where:
            problems.append(
                f"{i.source} が {i.target} をファイルの上部で読み込んでいます"
                f"（{layer(i.source)}は Qt を使いません）"
            )
        elif (i.source, i.where) not in qt_inside:
            allowed = "・".join(f"{m}.{f}" for m, f in sorted(qt_inside))
            problems.append(
                f"{i.source} の {i.where} が {i.target} を読み込んでいます（Qt を使わない層で、"
                f"関数の中で Qt を読み込んでよいのは {allowed} だけです）"
            )
    graph: dict[str, set[str]] = {}
    for a, b in edges - exceptions.keys():
        graph.setdefault(a, set()).add(b)
    if cycle := _cycle(graph):
        problems.append(
            f"循環しています（{' → '.join([*cycle, cycle[0]])}）。関数の中で読み込んで避けず、"
            "使う値や関数を下の層に移すか、引数で渡してください"
        )
    return problems


def stdlib_only(path: Path) -> list[str]:
    """ファイルの上部で、標準ライブラリのほかのモジュールを読み込む箇所の文の一覧。モジュールの直下の import だけを
    見る（TYPE_CHECKING の中や、関数の中の import は見ない）"""
    problems = []
    for node in ast.parse(path.read_text("utf-8")).body:
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [("." * node.level) + node.module]
        elif isinstance(node, ast.ImportFrom):  # from . import forms
            names = [("." * node.level) + a.name for a in node.names]
        else:
            continue
        for name in names:
            if (
                name.startswith(".")
                or name.split(".")[0] not in sys.stdlib_module_names
            ):
                problems.append(
                    f"{path.stem} がファイルの上部で {name} を読み込んでいます。{path.stem} は、解析のプロセスで"
                    "最初に読み込まれるモジュールで、画面のプロセスも読み込むため、上部では標準ライブラリだけを"
                    "読み込みます（ほかのモジュールは、型の注釈なら TYPE_CHECKING の中で、使う関数の中で読み込む）"
                )
    return problems


def test_analysis_imports_only_stdlib_at_top():
    """analysis.py は、ファイルの上部で標準ライブラリだけを読み込む"""
    assert stdlib_only(SRC / "analysis.py") == []


def test_stdlib_only_finds_other_imports(tmp_path):
    """標準ライブラリのほかのモジュールを、ファイルの上部で読み込んだら見つける"""
    path = tmp_path / "analysis.py"
    path.write_text(
        "import os\nfrom typing import TYPE_CHECKING\nimport numpy as np\nfrom . import forms\n"
        "if TYPE_CHECKING:\n    import cv2\n\ndef f():\n    import PIL\n",
        "utf-8",
    )
    found = stdlib_only(path)
    assert len(found) == 2
    assert found[0].startswith("analysis がファイルの上部で numpy を読み込んでいます。")
    assert found[1].startswith(
        "analysis がファイルの上部で .forms を読み込んでいます。"
    )


def test_package_follows_rules():
    """src/sumikeshi_studio が依存の決まりに合う（合わない点の文が、そのまま失敗の理由として出る）"""
    assert check(SRC) == []


# ---- check が決まりに合わない点を見つけること（小さなパッケージを作って確かめる）

MINI = [("上", {"top"}), ("ウィンドウ", {"win1", "win2"}), ("下", {"low", "base"})]


def mini(tmp_path: Path, files: dict[str, str]) -> Path:
    pkg = tmp_path / PACKAGE
    pkg.mkdir()
    for name in ("top", "win1", "win2", "low", "base"):
        (pkg / f"{name}.py").write_text(files.get(name, ""), "utf-8")
    for name, text in files.items():
        (pkg / f"{name}.py").write_text(text, "utf-8")
    return pkg


def mini_check(pkg: Path, **rules) -> list[str]:
    options = {
        "qt_free_from": 3,
        "no_siblings": {2},
        "restricted": {},
        "exceptions": {},
        "qt_inside": set(),
    }
    return check(pkg, MINI, **(options | rules))


def test_mini_package_without_problems(tmp_path):
    """上から下、同じ層の中（画面の層を除く）の読み込みは、決まりに合う"""
    pkg = mini(
        tmp_path,
        {
            "top": "from . import win1, low",
            "win1": "from .low import x",
            "low": "import sumikeshi_studio.base",
        },
    )
    assert mini_check(pkg) == []


def test_upward_and_window_siblings_are_found(tmp_path):
    """下の層から上の層の読み込みと、画面の層のモジュールどうしの読み込みを見つける"""
    pkg = mini(tmp_path, {"low": "from . import win1", "win1": "from . import win2"})
    assert mini_check(pkg) == [
        "low が win1 を読み込んでいます（low は層3「下」、win1 は層2「ウィンドウ」。下の層から上の層は読み込みません）",
        "win1 が win2 を読み込んでいます（どちらも層2「ウィンドウ」。この層のモジュールどうしは読み込みません）",
    ]


def test_cycle_through_function_and_type_checking_is_found(tmp_path):
    """関数の中と TYPE_CHECKING の中の import も数えて、循環を見つける"""
    pkg = mini(
        tmp_path,
        {
            "low": "from . import base",
            "base": "def f():\n    from . import low\n",
        },
    )
    cycle = (
        "循環しています（base → low → base）。関数の中で読み込んで避けず、"
        "使う値や関数を下の層に移すか、引数で渡してください"
    )
    assert mini_check(pkg) == [cycle]
    (pkg / "base.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from . import low\n",
        "utf-8",
    )
    assert mini_check(pkg) == [cycle]


def test_restricted_module_is_found(tmp_path):
    """読み込む相手を限るモジュールを、ほかのモジュールが読み込んだら見つける"""
    pkg = mini(tmp_path, {"top": "from . import base", "low": "from .base import x"})
    assert mini_check(pkg, restricted={"base": {"top"}}) == [
        "low が base を読み込んでいます（base を読み込んでよいのは、top だけです）"
    ]


def test_qt_in_qt_free_layer_is_found(tmp_path):
    """Qt を使わない層で、ファイルの上部の Qt と、決めた関数のほかの関数の中の Qt を見つける"""
    pkg = mini(
        tmp_path,
        {
            "top": "from PySide6.QtCore import QObject",
            "low": "import shiboken6",
            "base": "def ok():\n    from PySide6 import QtCore\n\n"
            "class C:\n    def ng(self):\n        import PySide6.QtGui\n",
        },
    )
    assert mini_check(pkg, qt_inside={("base", "ok")}) == [
        "base の C.ng が PySide6 を読み込んでいます（Qt を使わない層で、関数の中で Qt を読み込んでよいのは base.ok だけです）",
        "low が shiboken6 をファイルの上部で読み込んでいます（層3「下」は Qt を使いません）",
    ]


def test_modules_outside_layers_are_found(tmp_path):
    """どの層にも入っていないモジュールと、層の一覧にあってパッケージにないモジュールを見つける"""
    pkg = mini(tmp_path, {"extra": ""})
    (pkg / "base.py").unlink()
    assert mini_check(pkg) == [
        "extra がどの層にも入っていません。tests/test_structure.py の LAYERS の、役目に合う層に足してください",
        "層の一覧にある base が、パッケージにありません。LAYERS から消すか、名前を直してください",
    ]


def test_exceptions_are_skipped_until_fixed(tmp_path):
    """例外の一覧の依存は、決まりに合わなくても失敗しない。読み込まなくなったら、一覧から消すよう求める"""
    pkg = mini(tmp_path, {"low": "def f():\n    from . import top\n"})
    exceptions = {("low", "top"): "段階1", ("win1", "win2"): "段階2"}
    stale = (
        "win1 → win2 は、段階2で直す例外として一覧にありますが、もう読み込んでいません。"
        "tests/test_structure.py の EXCEPTIONS から消してください"
    )
    assert mini_check(pkg, exceptions=exceptions) == [stale]


# ---- テストが置き換える名前を、src がコピーしていないこと


def patched_names(tests: Path, modules: set[str]) -> list[tuple[str, str, str]]:
    """tests の .py で、monkeypatch.setattr が置き換える sumikeshi_studio のモジュールの名前：(モジュール, 名前, 場所)"""
    found = []
    for path in sorted(tests.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        alias = {}  # テストのファイルの中の名前 → sumikeshi_studio のモジュール
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module == PACKAGE:
                alias.update(
                    {a.asname or a.name: a.name for a in n.names if a.name in modules}
                )
            elif isinstance(n, ast.Import):
                for a in n.names:
                    parts = a.name.split(".")
                    if parts[0] == PACKAGE and len(parts) == 2 and a.asname:
                        alias[a.asname] = parts[1]
        for n in ast.walk(tree):
            if not (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "setattr"
            ):
                continue
            args, place = n.args, f"tests/{path.name}:{n.lineno}"
            # 先を文字列で書く形（"sumikeshi_studio.モジュール.名前"）と、モジュールと名前で書く形
            dotted = (
                args[0].value if args and isinstance(args[0], ast.Constant) else None
            )
            if isinstance(dotted, str):
                if dotted.startswith(PACKAGE + "."):
                    module, _, name = dotted[len(PACKAGE) + 1 :].rpartition(".")
                    if module in modules:
                        found.append((module, name, place))
            elif len(args) >= 2 and isinstance(args[1], ast.Constant):
                target, name = args[0], args[1].value
                if isinstance(target, ast.Name) and target.id in alias:
                    found.append((alias[target.id], name, place))
                elif (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in alias
                    and target.attr in modules
                ):  # app.worklog のように、ほかのモジュールを通して置き換えるもの
                    found.append((target.attr, name, place))
    return found


def patch_copies(src: Path, tests: Path) -> list[str]:
    """src が、テストの置き換える名前を from … import でコピーしている箇所の文の一覧"""
    modules = {p.stem for p in src.glob("*.py")}
    patched: dict[tuple[str, str], list[str]] = {}
    for module, name, place in patched_names(tests, modules):
        patched.setdefault((module, name), []).append(place)
    problems = []
    for path in sorted(src.glob("*.py")):
        for n in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(n, ast.ImportFrom):
                continue
            if n.level == 1 and n.module:
                module = n.module.split(".")[0]
            elif n.level == 0 and n.module and n.module.startswith(PACKAGE + "."):
                module = n.module.split(".")[1]
            else:
                continue
            for a in n.names:
                if (module, a.name) in patched:
                    places = "・".join(patched[(module, a.name)])
                    problems.append(
                        f"src/{PACKAGE}/{path.name}:{n.lineno} が {module} から {a.name} をコピーしています。"
                        f"{places} が monkeypatch.setattr で置き換えるので、{module}.{a.name} のように"
                        "モジュールを通して使ってください。コピーした側は、テストが置き換えたものを使わず、"
                        "置き換えが黙って効かなくなります"
                    )
    return problems


def test_patched_names_are_not_copied():
    """テストが monkeypatch.setattr で置き換える名前を、src が from … import でコピーしていない（コピーすると、置き換えが
    黙って効かなくなる）"""
    modules = {p.stem for p in SRC.glob("*.py")}
    # 集め方が働いていること（数えたときは55か所）
    assert len(patched_names(TESTS, modules)) > 40
    assert patch_copies(SRC, TESTS) == []


def test_patch_copies_finds_copies(tmp_path):
    """テストが置き換える名前をコピーしている箇所を、属性と文字列のどちらで書いた置き換えでも見つける"""
    src = tmp_path / PACKAGE
    src.mkdir()
    (src / "a.py").write_text("def f():\n    pass\n\n\ndef g():\n    pass\n", "utf-8")
    (src / "b.py").write_text("from .a import f, g\n", "utf-8")
    (src / "c.py").write_text("from . import a\n", "utf-8")  # モジュールを通すのはよい
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        "from sumikeshi_studio import a\n\n\ndef test_x(monkeypatch):\n"
        '    monkeypatch.setattr(a, "f", None)\n'
        '    monkeypatch.setattr("sumikeshi_studio.a.g", None)\n',
        "utf-8",
    )
    found = patch_copies(src, tests)
    assert len(found) == 2
    assert found[0].startswith(
        "src/sumikeshi_studio/b.py:1 が a から f をコピーしています。tests/test_x.py:5 が"
    )
    assert found[1].startswith(
        "src/sumikeshi_studio/b.py:1 が a から g をコピーしています。tests/test_x.py:6 が"
    )


# ---- 使う名前が、そのモジュールにあること
# 名前を移したときに参照を直し忘れると、その行を通るまで誤りが出ない。エラーのときにしか通らない行は、全件の
# テストでも見つからない。

MODULE_ATTRS = {
    "__name__",
    "__file__",
    "__doc__",
    "__dict__",
    "__spec__",
    "__loader__",
    "__package__",
}


def defined_names(path: Path) -> set[str] | None:
    """モジュールの上の段で決まる名前（関数・クラス・代入・import、if・try・with・for の中を含む）と、関数の中で
    global にした名前。from … import * があれば、決められないので None"""
    tree = ast.parse(path.read_text("utf-8"))
    names = set(MODULE_ATTRS)
    star = False

    def bind(target: ast.AST) -> None:
        names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))

    def visit(body: list[ast.stmt]) -> None:
        nonlocal star
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, ast.Assign):
                for target in n.targets:
                    bind(target)
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
                bind(n.target)
            elif isinstance(n, ast.TypeAlias):
                names.add(n.name.id)
            elif isinstance(n, ast.Import):
                names.update(a.asname or a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                star = star or any(a.name == "*" for a in n.names)
                names.update(a.asname or a.name for a in n.names)
            elif isinstance(n, (ast.For, ast.AsyncFor)):
                bind(n.target)
                visit(n.body)
                visit(n.orelse)
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                for item in n.items:
                    if item.optional_vars is not None:
                        bind(item.optional_vars)
                visit(n.body)
            elif isinstance(n, (ast.If, ast.While)):
                visit(n.body)
                visit(n.orelse)
            elif isinstance(n, ast.Try):
                for h in n.handlers:
                    if h.name:
                        names.add(h.name)
                    visit(h.body)
                visit(n.body)
                visit(n.orelse)
                visit(n.finalbody)

    visit(tree.body)
    for n in ast.walk(tree):
        if isinstance(n, ast.Global):
            names.update(n.names)
    return None if star else names


def module_aliases(tree: ast.Module, modules: set[str]) -> dict[str, str]:
    """ファイルの中で sumikeshi_studio のモジュールを指す名前 → モジュール（関数の中の import も含める）"""
    alias = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (
            (n.level == 0 and n.module == PACKAGE)
            or (n.level == 1 and n.module is None)
        ):
            alias.update(
                {a.asname or a.name: a.name for a in n.names if a.name in modules}
            )
        elif isinstance(n, ast.Import):
            # as のない import sumikeshi_studio.x は、sumikeshi_studio.x.名前 と書くので調べない
            alias.update(
                {
                    a.asname: a.name.split(".")[1]
                    for a in n.names
                    if a.asname
                    and a.name.split(".")[:1] == [PACKAGE]
                    and a.name.count(".") == 1
                    and a.name.split(".")[1] in modules
                }
            )
    return alias


def missing_refs(src: Path, others: list[Path]) -> tuple[int, list[str]]:
    """src（sumikeshi_studio のフォルダ）と others のフォルダの .py で、sumikeshi_studio のモジュールの名前を使う箇所（モジュールを
    指す名前の「名前.属性」と、from sumikeshi_studio.モジュール import 名前・from .モジュール import 名前）を集め、その数と、
    モジュールにない名前の文の一覧を返す"""
    paths = {p.stem: p for p in src.glob("*.py")}
    names = {m: defined_names(p) for m, p in paths.items()}
    files = [(f"src/{PACKAGE}/{p.name}", p) for p in sorted(src.glob("*.py"))]
    for folder in others:
        files += [(f"{folder.name}/{p.name}", p) for p in sorted(folder.glob("*.py"))]
    count, problems = 0, []
    for place, path in files:
        tree = ast.parse(path.read_text("utf-8"))
        alias = module_aliases(tree, set(paths))
        uses = []  # (行, 書いたもの, モジュール, 名前)
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id in alias
            ):
                uses.append(
                    (n.lineno, f"{n.value.id}.{n.attr}", alias[n.value.id], n.attr)
                )
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.level == 0 and n.module.startswith(PACKAGE + "."):
                    module = n.module[len(PACKAGE) + 1 :]
                elif n.level == 1:
                    module = n.module
                else:
                    continue
                if module in paths:
                    uses += [
                        (
                            n.lineno,
                            f"from {'.' * n.level}{n.module} import {a.name}",
                            module,
                            a.name,
                        )
                        for a in n.names
                        if a.name != "*"
                    ]
        found = []  # ast.walk は行の順にたどらないので、ファイルごとに行の順に並べる
        for line, written, module, name in uses:
            if names[module] is None:
                continue
            count += 1
            if name not in names[module]:
                text = (
                    f"{place}:{line} の {written} の {name} は、src/{PACKAGE}/{module}.py にありません。"
                    "名前を移したときは参照も直してください（直し忘れた参照は、その行を通るまで誤りが出ません）。"
                    "モジュールを指す名前がローカル変数・引数なら、モジュールと同じ名前にしないでください"
                )
                found.append((line, text))
        problems += [text for _, text in sorted(found)]
    return count, problems


def test_module_references_exist():
    """src・tests・tools の「モジュール.名前」の名前と、from … import で取り込む名前が、そのモジュールにある"""
    count, problems = missing_refs(SRC, [TESTS, TESTS.parent / "tools"])
    assert count > 1000  # 集め方が働いていること（数えたときは1,452か所）
    assert problems == []


def test_missing_refs_finds_missing_names(tmp_path):
    """ない名前の参照を、src・tests・tools のどれでも、import の書き方によらず見つける。from … import の名前も調べる。
    ローカル変数がモジュールと同じ名前でも、モジュールの名前の参照として調べる"""
    src = tmp_path / PACKAGE
    src.mkdir()
    (src / "a.py").write_text(
        "import os\n"
        "X = 1\n"
        "try:\n    Y = 2\nexcept ImportError:\n    Y = None\n\n\n"
        "def f():\n    global Z\n    Z = 3\n\n\n"
        "class C:\n    pass\n",
        "utf-8",
    )
    (src / "b.py").write_text(
        "from . import a\nfrom .a import C, old\n\n\n"
        "def g():\n    return a.f, a.X, a.Y, a.Z, a.C, a.os, a.__file__, a.gone\n\n\n"
        "def h(a):\n    return a.error\n",
        "utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        "from sumikeshi_studio import a as m\n\n\ndef test_x():\n    assert m.X and m.Y2\n",
        "utf-8",
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "t.py").write_text(
        "import sumikeshi_studio.a as aa\nfrom sumikeshi_studio.a import X, moved\n\naa.W = aa.C\n",
        "utf-8",
    )
    count, problems = missing_refs(src, [tests, tools])
    assert count == 17  # 「名前.属性」が13か所、from … import の名前が4つ
    assert [p.split(" は、")[0] for p in problems] == [
        "src/sumikeshi_studio/b.py:2 の from .a import old の old",
        "src/sumikeshi_studio/b.py:6 の a.gone の gone",
        "src/sumikeshi_studio/b.py:10 の a.error の error",
        "tests/test_x.py:5 の m.Y2 の Y2",
        "tools/t.py:2 の from sumikeshi_studio.a import moved の moved",
        "tools/t.py:4 の aa.W の W",
    ]


# ---- モジュールの名前と、関数の中の名前
# 関数の中の変数の名前がモジュールの名前と重なると、変数のつもりの名前がモジュールを指す誤り
# （`if paper is not None:` がいつも真になる）になり、全件のテストが通っても見つからないことがある。


def _functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def bare_module_names(src: Path) -> list[str]:
    """src で、sumikeshi_studio のモジュールを指す名前を「名前.属性」以外の形（そのまま値として）で使う箇所の文の一覧"""
    modules = {p.stem for p in src.glob("*.py")}
    problems = []
    for path in sorted(src.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        alias = module_aliases(tree, modules)
        attr_bases = {
            id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }
        found = sorted(
            (n.lineno, n.id)
            for n in ast.walk(tree)
            if isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Load)
            and n.id in alias
            and id(n) not in attr_bases
        )
        problems += [
            f"src/{PACKAGE}/{path.name}:{line} の {name} は、モジュール {alias[name]} を指しますが、"
            f"「{name}.名前」の形ではなく、そのまま値として使っています（たとえば「if {name} is not None:」は、"
            "モジュールを指していつも真になります）。変数のつもりなら、モジュールと違う名前にしてください"
            for line, name in found
        ]
    return problems


def local_module_names(src: Path) -> list[str]:
    """src の関数の中で、そのファイルが読み込むモジュールと同じ名前の引数・ローカル変数（代入・for・with・except・
    中の関数）の文の一覧。モジュールを関数の中で読み込むファイルも、ファイル全体で読み込む名前として調べる"""
    modules = {p.stem for p in src.glob("*.py")}
    problems = []
    for path in sorted(src.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        alias = module_aliases(tree, modules)
        found = set()
        for f in [
            *_functions(tree),
            *(n for n in ast.walk(tree) if isinstance(n, ast.Lambda)),
        ]:
            a = f.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None and arg.arg in alias:
                    found.add((arg.lineno, arg.arg, getattr(f, "name", "lambda")))
        for f in _functions(tree):
            for n in ast.walk(f):
                if n is f:
                    continue
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    name = n.id
                elif isinstance(
                    n,
                    (
                        ast.ExceptHandler,
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                        ast.ClassDef,
                    ),
                ):
                    name = n.name  # except の名前は、名前がなければ None
                else:
                    continue
                if name in alias:
                    found.add((n.lineno, name, f.name))
        problems += [
            f"src/{PACKAGE}/{path.name}:{line} の {func} の {name} は、このファイルが読み込むモジュール "
            f"{alias[name]} と同じ名前です。関数の中でモジュールが隠れるので、違う名前にしてください"
            for line, name, func in sorted(found)
        ]
    return problems


def test_module_names_are_used_only_with_attributes():
    """src で、モジュールを指す名前は「名前.属性」の形でだけ使う"""
    assert bare_module_names(SRC) == []


def test_locals_do_not_shadow_modules():
    """src の関数の引数・ローカル変数に、そのファイルが読み込むモジュールと同じ名前を付けない"""
    assert local_module_names(SRC) == []


def test_module_name_checks_find_problems(tmp_path):
    """モジュールの名前をそのまま値として使う箇所と、モジュールと同じ名前の引数・ローカル変数を見つける。モジュールを
    関数の中で読み込むファイルでも、ほかの関数の同じ名前を見つける"""
    pkg = mini(
        tmp_path,
        {
            "a": "X = 1\n",
            "b": (
                "from . import a\n\n\n"
                "def f(c):\n"
                "    if a is not None:\n"  # 5行：そのまま使う
                "        return a.X\n"
                "    return c(a)\n\n\n"  # 7行：そのまま使う
                "def g(a):\n"  # 10行：引数
                "    for a in []:\n"  # 11行：for
                "        pass\n"
                "    return lambda a: 0\n"  # 13行：lambda の引数
            ),
            "c": (
                "def h():\n"
                "    from . import a\n\n"
                "    return a.X\n\n\n"
                "def k():\n"
                "    a = 1\n"  # 8行：別の関数で読み込むモジュールと同じ名前
                "    return 0 if a else 1\n"  # 9行：そのまま使う（ローカル変数のつもり）
            ),
            "d": "from . import a\n\n\ndef m(x):\n    y = a.X\n    return x, y\n",
        },
    )
    assert [p.split(" は、")[0] for p in bare_module_names(pkg)] == [
        "src/sumikeshi_studio/b.py:5 の a",
        "src/sumikeshi_studio/b.py:7 の a",
        "src/sumikeshi_studio/c.py:9 の a",
    ]
    assert [p.split(" は、")[0] for p in local_module_names(pkg)] == [
        "src/sumikeshi_studio/b.py:10 の g の a",
        "src/sumikeshi_studio/b.py:11 の g の a",
        "src/sumikeshi_studio/b.py:13 の lambda の a",
        "src/sumikeshi_studio/c.py:8 の k の a",
    ]


# ---- 代入した値を一度も使わずに、同じ名前にもう一度代入しない
# 関数の結果を受けた直後に別の値で上書きすると、受けた結果を使っていないことに気づけない。テストがその関数だけを
# 確かめていると、片方だけを変えたときに、テストが通ったまま画面の文が食い違う。


def _statement_lists(func: ast.AST):
    """関数の中の文の並び（本体と、if・for・while・with・try・match の中）。中の関数とクラスには入らない"""
    stack = [func]
    while stack:
        node = stack.pop()
        lists = [getattr(node, k, None) for k in ("body", "orelse", "finalbody")]
        lists += [h.body for h in getattr(node, "handlers", None) or []]
        lists += [c.body for c in getattr(node, "cases", None) or []]
        for body in lists:
            if not (isinstance(body, list) and body and isinstance(body[0], ast.stmt)):
                continue
            yield node, body
            stack += [
                s
                for s in body
                if not isinstance(
                    s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
            ]


def _assigned(stmt: ast.stmt) -> set[str]:
    if isinstance(stmt, ast.Assign):
        return {
            n.id
            for t in stmt.targets
            for n in ast.walk(t)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        }
    if (
        isinstance(stmt, ast.AnnAssign)
        and stmt.value
        and isinstance(stmt.target, ast.Name)
    ):
        return {stmt.target.id}
    return set()


def _uses(node: ast.AST, name: str) -> bool:
    """name を読む・消す・足し込む（+= など）"""
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Name)
            and n.id == name
            and not isinstance(n.ctx, ast.Store)
        ):
            return True
        target = n.target if isinstance(n, ast.AugAssign) else None
        if isinstance(target, ast.Name) and target.id == name:
            return True
    return False


def _may_leave(node: ast.AST) -> bool:
    """値が次の文に届かずに外へ出ることがある（break・continue・raise・yield）"""
    leave = (ast.Break, ast.Continue, ast.Raise, ast.Yield, ast.YieldFrom)
    return any(isinstance(n, leave) for n in ast.walk(node))


def overwritten_values(src: Path) -> list[str]:
    """src の関数の中で、同じ文の並びの中の文が名前に代入し、その値を一度も使わないまま、後の文が同じ名前に代入している
    箇所の文の一覧。「_」は使わない値を捨てる名前なので除く。あいだに break・continue・raise・yield がある箇所と、
    try の中で、except・finally がその名前を使う箇所は、前の値が使われることがあるので除く"""
    problems = []
    for path in sorted(src.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for func in _functions(tree):
            for owner, body in _statement_lists(func):
                for i, first in enumerate(body):
                    for name in _assigned(first) - {"_"}:
                        if (
                            isinstance(owner, (ast.Try, ast.TryStar))
                            and body is owner.body
                            and any(
                                _uses(h, name)
                                for h in [*owner.handlers, *owner.finalbody]
                            )
                        ):
                            continue
                        for later in body[i + 1 :]:
                            if name in _assigned(later) and not _uses(later, name):
                                problems.append(
                                    f"src/{PACKAGE}/{path.name}:{first.lineno} の {func.name} の {name} は、"
                                    f"代入した値を一度も使わないまま、{later.lineno}行でもう一度代入しています。"
                                    "使うはずの値を捨てている誤りのことがあります。"
                                    "前の代入が要らないなら消し、要るなら後の代入の名前を変えてください"
                                )
                                break
                            if _uses(later, name) or _may_leave(later):
                                break
    return problems


def test_values_are_not_overwritten_unused():
    """src の関数の中で、代入した値を一度も使わずに、同じ名前にもう一度代入しない"""
    assert overwritten_values(SRC) == []


def test_overwritten_values_are_found(tmp_path):
    """同じ文の並びの中の再代入を見つけ、前の値が使われうるもの（if の中だけの代入、使ってからの代入、累算代入、
    try の中で except が使う値、「_」）は見つけない"""
    pkg = mini(
        tmp_path,
        {
            "a": (
                "def f():\n"
                "    x = g()\n"  # 2行：使わないまま、3行で代入し直す
                "    x = []\n"
                "    return x\n\n\n"
                "def h(c):\n"
                "    y = 1\n"
                "    if c:\n"
                "        y = 2\n"  # if の中だけの代入
                "    return y\n\n\n"
                "def k():\n"
                "    z = 1\n"
                "    print(z)\n"
                "    z = 2\n"  # 使ってからの代入
                "    w = 1\n"
                "    w += 1\n"  # 累算代入
                "    w = 3\n"
                "    _ = 1\n"
                "    _ = 2\n"
                "    return z, w\n\n\n"
                "def t():\n"
                "    try:\n"
                "        v = 1\n"
                "        g()\n"
                "        v = 2\n"
                "    except ValueError:\n"
                "        print(v)\n"  # except が前の値を使うことがある
                "    for i in range(3):\n"
                "        u = i\n"
                "        if i:\n"
                "            break\n"
                "        u = 0\n"  # あいだに break がある
                "    return u\n\n\n"
                "def m():\n"
                "    def inner():\n"
                "        s = 1\n"  # 43行：中の関数も調べる
                "        s = 2\n"
                "        return s\n"
                "    return inner\n"
            ),
        },
    )
    assert [p.split(" は、")[0] for p in overwritten_values(pkg)] == [
        "src/sumikeshi_studio/a.py:2 の f の x",
        "src/sumikeshi_studio/a.py:43 の inner の s",
    ]


# ---- 同じ画面の文を、2つ以上のファイルに書かない
# ボタンの名前や状態の文を変えるときに、ファイルごとに直し忘れると、画面ごとに文が食い違う。数えるのは、6文字以上で
# 日本語を含む文字列の定数（f 文字列の文字の部分を含み、説明の文を除く）。

JAPANESE = re.compile(r"[ぁ-んァ-ン一-龥]")
# 意図して2つ以上のファイルに書く文と、その理由。2つ以上のファイルになくなったら、消すよう失敗する
SAME_TEXTS = {
    "保存しますか？": "別々の2つの問い（formeditor の保存の前の警告と、formstext.unclear_text）の最後の一句",
}


def same_texts(src: Path, allowed: dict[str, str]) -> list[str]:
    """src の2つ以上のファイルにある同じ文（allowed のものを除く）と、2つ以上のファイルになくなった allowed の文の一覧"""
    places: dict[str, list[str]] = {}
    for path in sorted(src.glob("*.py")):
        if path.stem == EN_TABLE_MODULE:  # 英語の表の鍵は、src の日本語の文と同じにする
            continue
        tree = ast.parse(path.read_text("utf-8"))
        docs = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(
                n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and id(n) not in docs
                and len(n.value) >= 6
                and JAPANESE.search(n.value)
            ):
                places.setdefault(n.value, []).append(f"{path.stem}.py:{n.lineno}")
    problems = []
    for text, where in sorted(places.items(), key=lambda kv: kv[1]):
        files = {w.split(":")[0] for w in where}
        if len(files) > 1 and text not in allowed:
            problems.append(
                f"「{text}」が {'・'.join(where)} にあります。2つ以上の画面に出す文は uitext に、1つの画面の文は"
                "その画面の文を作るモジュール（formstext・reviewtext・exporttext など）に置いて、ほかから使ってください。意図して同じに"
                "しているなら、tests/test_structure.py の SAME_TEXTS に理由を書いて足してください"
                "（文を変えるときに直し忘れて、画面ごとに文が食い違わないようにするため）"
            )
    for text in sorted(allowed):
        if len({w.split(":")[0] for w in places.get(text, [])}) < 2:
            problems.append(
                f"「{text}」は、もう2つ以上のファイルにありません。tests/test_structure.py の SAME_TEXTS から"
                "消してください"
            )
    return problems


def test_same_texts_are_in_one_place():
    """同じ画面の文を、2つ以上のファイルに書かない（意図して同じにしているものは SAME_TEXTS に理由を書く）"""
    assert same_texts(SRC, SAME_TEXTS) == []


def test_same_texts_finds_copies(tmp_path):
    """2つ以上のファイルにある同じ文を、f 文字列の文字の部分でも見つけ、例外・説明の文・5文字以下・同じファイルの
    中だけのもの・英語の表の鍵は見つけない。2つ以上のファイルになくなった例外も見つける"""
    pkg = mini(
        tmp_path,
        {
            "a": (
                '"""説明の文は数えない"""\n\n'
                'A = "画面に出す同じ文"\n'
                'B = "意図して同じにする文"\n'
                'C = "短い文です"\n'  # 5文字
                'D = "同じファイルの中だけ"\n'
                'E = "同じファイルの中だけ"\n'
            ),
            "b": (
                '"""説明の文は数えない"""\n\n'
                'X = f"画面に出す同じ文{1}"\n'  # f 文字列の文字の部分
                'Y = "意図して同じにする文"\n'
                'Z = "短い文です"\n'
            ),
            # 英語の表の鍵は数えない
            "i18n_en": 'EN = {"同じファイルの中だけ": "Only in one file"}\n',
        },
    )
    allowed = {"意図して同じにする文": "試験", "もう1つしかない文": "試験"}
    found = same_texts(pkg, allowed)
    assert [p.split("。")[0] for p in found] == [
        "「画面に出す同じ文」が a.py:3・b.py:3 にあります",
        "「もう1つしかない文」は、もう2つ以上のファイルにありません",
    ]
    assert "uitext に" in found[0] and "SAME_TEXTS に理由を書いて足して" in found[0]


# ---- docs/23 の構成の表と src のモジュールを一致させる
# モジュールを足したり分けたりしたときに表を直し忘れると、読む人が、ないモジュールを探したり、あるモジュールの役目を
# 知らずにいたりする。

DOCS_TABLE = SRC.parents[1] / "docs" / "23-spec.md"
TABLE_SECTION = re.compile(r"^## 2\. 構成\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
TABLE_MODULE = re.compile(r"`src/sumikeshi_studio/([^`]+\.py)`")


def table_modules(text: str) -> list[str] | None:
    """docs/23 の「## 2. 構成」の節の表の1列目にある src/sumikeshi_studio の .py の、パッケージからの位置（表の順）。
    2列目の文の中と、節の外の参照は数えない。節がなければ None"""
    section = TABLE_SECTION.search(text)
    if section is None:
        return None
    return [
        name
        for line in section[1].splitlines()
        if line.startswith("|")
        for name in TABLE_MODULE.findall(line.split("|")[1])
    ]


def module_table_gaps(text: str, src: Path) -> list[str]:
    """src の .py と docs/23 の構成の表の、片方にしかないものと、表に2回以上あるものの一覧"""
    names = table_modules(text)
    if names is None:
        return ["docs/23-spec.md に「## 2. 構成」の節が見つかりません"]
    modules = {p.relative_to(src).as_posix() for p in src.rglob("*.py")}
    fix = "（docs/23-spec.md の「2. 構成」の表を直してください）"
    return (
        [
            f"{n}：src にあるが、docs/23 の構成の表にない{fix}"
            for n in sorted(modules - set(names))
        ]
        + [
            f"{n}：docs/23 の構成の表にあるが、src にない{fix}"
            for n in sorted(set(names) - modules)
        ]
        + [
            f"{n}：docs/23 の構成の表に2回以上ある{fix}"
            for n in sorted({n for n in names if names.count(n) > 1})
        ]
    )


@pytest.mark.skipif(
    not DOCS_TABLE.exists(),
    reason="仕様の文書（docs/23-spec.md）がない環境では確かめない",
)
def test_docs_module_table_matches_src():
    """docs/23-spec.md の「2. 構成」の表に、src のモジュールがちょうど1回ずつある"""
    assert module_table_gaps(DOCS_TABLE.read_text("utf-8"), SRC) == []


def test_module_table_gaps_finds_differences(tmp_path):
    """表にないモジュール・src にないモジュール・2回以上あるモジュールを見つけ、下のパッケージのモジュールも数える。
    2列目の文の中と、節の外の参照は数えない。節がなければ、そのことだけを知らせる"""
    src = tmp_path / PACKAGE
    (src / "sub").mkdir(parents=True)
    for name in ("a.py", "b.py", "sub/c.py"):
        (src / name).write_text("", "utf-8")
    text = (
        "# 題\n\n## 2. 構成\n\n| ファイル | 役割 |\n|---|---|\n"
        "| `src/sumikeshi_studio/a.py` | 役割 |\n"
        "| `src/sumikeshi_studio/d.py` | `src/sumikeshi_studio/b.py` を使う |\n"
        "| `src/sumikeshi_studio/sub/c.py` | 下のパッケージ |\n"
        "| `src/sumikeshi_studio/a.py` | 2回目 |\n"
        "| `tests/` | 自動テスト |\n\n"
        "## 3. 画面の操作\n\n| `src/sumikeshi_studio/b.py` | 節の外 |\n"
    )
    assert table_modules(text) == ["a.py", "d.py", "sub/c.py", "a.py"]
    assert [p.split("（")[0] for p in module_table_gaps(text, src)] == [
        "b.py：src にあるが、docs/23 の構成の表にない",
        "d.py：docs/23 の構成の表にあるが、src にない",
        "a.py：docs/23 の構成の表に2回以上ある",
    ]
    assert module_table_gaps("# 題\n\n## 3. 画面の操作\n", src) == [
        "docs/23-spec.md に「## 2. 構成」の節が見つかりません"
    ]


# ---- 英語の表と、英語の表にない文
# 画面に出す文は、日本語の文を鍵にして英語の表（i18n_en.EN）から引く（i18n.tr）。日本語の文を変えて英語の表の鍵を
# 変え忘れると、英語の画面に日本語が出る。英語の表の {name} を書き間違えると、文を出すときに失敗する。

# 英語の表の検査で日本語として数える文字：かな・漢字に、日本語の句読点・かっこ・記号を足したもの（英語の画面に「。」や
# 「」が残らないように）
JAPANESE_TEXT = re.compile(
    r"[ぁ-んァ-ヶー一-龥々「」『』（）［］【】。、・：；？！／〜]"
)
EN_TABLE_MODULE = "i18n_en"  # 英語の表のモジュール（鍵が日本語なので、英語の表にない文の検査では数えない）
# まだ英語にしていないモジュール。モジュールの文を tr に通して英語の表に足したら、ここから消す。日本語の
# 文がなくなったのに残っていると失敗する
NOT_TRANSLATED: set[str] = set()
# 日本語のまま残す文と、その理由（様式の名前、題名の語、前のバージョンのファイルの名前など）
KEEP_JAPANESE: dict[str, str] = {
    # 言語ごとの区切りと引用符（i18n.join・i18n.quoted。日本語の画面で使う）
    "、": "i18n.join の日本語の区切り",
    "「」": "i18n.quoted の日本語のかぎかっこ",
    "『』": "i18n.quoted に渡す二重かぎかっこ（様式の名前を囲む）",
    # 言語を選ぶ欄（どちらの言語の画面でも同じ文を出す）
    "言語 / Language": "言語を選ぶ欄のラベル。読めない言語で起動した人も見つけられるよう、両方の語を出す",
    "日本語": "言語を選ぶ欄の言語の名前。その言語の語で出す",
    # 配布用パッケージの3様式の名前（帳票の名前は日本語のまま）
    "扶養控除等申告書": "配布用パッケージの3様式の名前（forms.FUYOU）",
    "保険料控除申告書": "配布用パッケージの3様式の名前（forms.HOKEN）",
    "基・配・特・所の申告書": "配布用パッケージの3様式の名前（forms.KIHAI）",
    # Windows の文を見分ける正規表現
    "4551|アプリケーション制御|Application Control": (
        "アプリケーション制御に止められたことを、日本語・英語の Windows の文から見分ける正規表現（nativecheck.POLICY_PATTERN）"
    ),
    # 題名を比べるための正規表現
    r"令和\d+年分": "見本の題名から年分を探す正規表現（formcheck。和暦の日本語の帳票の題名を読む）",
    r"[\s()（）「」]": "題名と題名の語を比べる前に除く文字（formstore.TITLE_DROP）",
}


def docstring_ids(tree: ast.AST) -> set[int]:
    """モジュール・関数・クラスの説明の文（最初の文字列）の id"""
    return {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(
            n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        )
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }


def is_tr(call: ast.Call) -> bool:
    """tr(…) か i18n.tr(…) の呼び出し"""
    f = call.func
    return (isinstance(f, ast.Name) and f.id == "tr") or (
        isinstance(f, ast.Attribute) and f.attr == "tr"
    )


@dataclass
class ModuleTexts:
    """1つのモジュールの、説明の文を除く文字列"""

    plain: list[tuple[int, str]]  # (行, 文)。f 文字列の部分と、tr の文脈を除く
    fstrings: list[
        tuple[int, str]
    ]  # (行, 文字の部分をつなぎ、値の部分を {…} にしたもの)
    contexts: list[tuple[int, str, str]]  # tr(文, context=文脈) の (行, 文脈, 文)


def module_texts(path: Path) -> ModuleTexts:
    tree = ast.parse(path.read_text("utf-8"))
    skip = docstring_ids(tree)
    skip |= {
        id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values
    }
    contexts = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and is_tr(n):
            for k in n.keywords:
                if k.arg == "context" and isinstance(k.value, ast.Constant):
                    skip.add(id(k.value))
                    if n.args and isinstance(n.args[0], ast.Constant):
                        contexts.append((n.lineno, k.value.value, n.args[0].value))
    plain = [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in skip
    ]
    fstrings = [
        (
            n.lineno,
            "".join(
                v.value if isinstance(v, ast.Constant) else "{…}" for v in n.values
            ),
        )
        for n in ast.walk(tree)
        if isinstance(n, ast.JoinedStr)
    ]
    return ModuleTexts(plain, fstrings, contexts)


def table_problems(src: Path, table: dict) -> list[str]:
    """英語の表の、src にない鍵、鍵と値とで {name} の名前か数が違うもの、日本語の文字がある値の一覧"""
    from sumikeshi_studio.i18n import field_names

    strings = {
        s
        for path in src.glob("*.py")
        if path.stem != EN_TABLE_MODULE
        for _, s in module_texts(path).plain
    }
    problems = []
    for key, value in table.items():
        if isinstance(key, tuple) and not (
            len(key) == 2 and all(isinstance(k, str) for k in key)
        ):
            problems.append(
                f"英語の表の鍵 {key!r} は、(文脈, 日本語の文) の組にしてください"
            )
            continue
        if isinstance(value, tuple) and not (
            len(value) == 2 and all(isinstance(v, str) for v in value)
        ):
            problems.append(
                f"英語の表の値 {value!r} は、英語の文か（単数, 複数）の組にしてください"
            )
            continue
        text = key[1] if isinstance(key, tuple) else key
        if text not in strings:
            problems.append(
                f"英語の表の鍵「{text}」が src にありません。src の文を変えたなら鍵も変え、消したなら英語の表からも"
                "消してください（src/sumikeshi_studio/i18n_en.py）"
            )
        for english in value if isinstance(value, tuple) else (value,):
            try:
                same = sorted(field_names(text)) == sorted(field_names(english))
            except ValueError as e:  # { と } の組が合わない
                problems.append(
                    f"「{text}」か英語の「{english}」の {{ }} を読めません（{e}）"
                )
                continue
            if not same:
                problems.append(
                    f"「{text}」と英語の「{english}」とで、{{ }} の名前か数が違います"
                )
            if JAPANESE_TEXT.search(english):
                problems.append(
                    f"「{text}」の英語の「{english}」に、日本語の文字があります"
                )
    return problems


def untranslated(
    src: Path, table: dict, pending: set[str], keep: dict[str, str]
) -> list[str]:
    """src の日本語の文のうち、英語の表にも、日本語のまま残す一覧（keep）にもないもの、日本語の f 文字列、英語の表に
    ない文脈の文の一覧（pending のモジュールは数えない）。pending と keep の、もう要らないものも知らせる"""
    keys = {k[1] if isinstance(k, tuple) else k for k in table}
    problems, kept, modules = [], set(), set()
    for path in sorted(src.glob("*.py")):
        module = path.stem
        modules.add(module)
        if module == EN_TABLE_MODULE:
            continue
        texts = module_texts(path)
        plain = [(line, s) for line, s in texts.plain if JAPANESE_TEXT.search(s)]
        fstrings = [(line, s) for line, s in texts.fstrings if JAPANESE_TEXT.search(s)]
        kept |= {s for _, s in plain if s in keep}
        if module in pending:
            if not plain and not fstrings:
                problems.append(
                    f"{module}.py に日本語の文がありません。tests/test_structure.py の NOT_TRANSLATED から消してください"
                )
            continue
        for line, s in plain:
            if s not in keys and s not in keep:
                problems.append(
                    f"「{s}」（{module}.py:{line}）が英語の表にありません。src/sumikeshi_studio/i18n_en.py に英語を"
                    "足すか、日本語のまま残すなら tests/test_structure.py の KEEP_JAPANESE に理由を書いて足してください"
                )
        for line, s in fstrings:
            problems.append(
                f'「{s}」（{module}.py:{line}）は日本語の f 文字列です。tr("…{{name}}…", name=…) の形に書き換えて'
                "ください"
            )
        for line, context, s in texts.contexts:
            if (context, s) not in table:
                problems.append(
                    f"文脈「{context}」の「{s}」（{module}.py:{line}）が英語の表にありません。"
                    "src/sumikeshi_studio/i18n_en.py に (文脈, 文) の組で足してください"
                )
    for module in sorted(pending - modules):
        problems.append(
            f"{module}.py がありません。tests/test_structure.py の NOT_TRANSLATED から消してください"
        )
    for s in sorted(set(keep) - kept):
        problems.append(
            f"「{s}」は、もう src にありません。tests/test_structure.py の KEEP_JAPANESE から消してください"
        )
    return problems


def test_english_table_is_consistent():
    """英語の表の鍵は src にあり、鍵と値とで {name} の名前と数が同じで、値に日本語の文字がない"""
    from sumikeshi_studio import i18n_en

    assert table_problems(SRC, i18n_en.EN) == []


def test_japanese_texts_are_in_english_table():
    """src の日本語の文は、英語の表にあるか、日本語のまま残す一覧にある（まだ英語にしていないモジュールを除く）"""
    from sumikeshi_studio import i18n_en

    assert untranslated(SRC, i18n_en.EN, NOT_TRANSLATED, KEEP_JAPANESE) == []


def test_table_problems_finds_mistakes(tmp_path):
    """src にない鍵、{name} の違い、日本語の文字がある値、形の違う鍵と値、読めない { } を見つける"""
    pkg = mini(
        tmp_path,
        {
            "a": '"""説明の文は数えない"""\n\nA = "様式を開く"\nB = "{n}ページ"\nC = "{name}を消す"\nD = "見本"\n'
        },
    )
    table = {
        "様式を開く": "Open a form",
        "説明の文は数えない": "Not counted",
        ("ボタン", "{n}ページ"): ("{n} page", "{count} pages"),
        "{name}を消す": "Delete {name} 様式",
        ("ボタン",): "Bad key",
        "見本": ("Sample",),
        "{name}を消す ": "{name",
    }
    assert [p.split("。")[0] for p in table_problems(pkg, table)] == [
        "英語の表の鍵「説明の文は数えない」が src にありません",
        "「{n}ページ」と英語の「{count} pages」とで、{ } の名前か数が違います",
        "「{name}を消す」の英語の「Delete {name} 様式」に、日本語の文字があります",
        "英語の表の鍵 ('ボタン',) は、(文脈, 日本語の文) の組にしてください",
        "英語の表の値 ('Sample',) は、英語の文か（単数, 複数）の組にしてください",
        "英語の表の鍵「{name}を消す 」が src にありません",
        "「{name}を消す 」か英語の「{name」の { } を読めません（expected '}' before end of string）",
    ]


def test_untranslated_finds_missing_texts(tmp_path):
    """英語の表にない日本語の文、日本語の f 文字列、英語の表にない文脈の文を見つけ、まだ英語にしていないモジュールと
    説明の文は数えない。要らなくなった NOT_TRANSLATED と KEEP_JAPANESE の項目も見つける"""
    pkg = mini(
        tmp_path,
        {
            "a": (
                '"""説明の文は数えない"""\n\n'
                'A = "表にある文"\n'
                'B = "表にない文"\n'
                'C = "残す文"\n'
                'D = f"{1}ページ"\n'
                'E = "english only"\n'
                'F = tr("編集", context="ボタン")\n'
                'G = i18n.tr("編集", context="題名")\n'
            ),
            "b": 'X = "まだ英語にしていない文"\nY = f"{1}件"\n',
            "c": 'Z = "english only"\n',
            "i18n_en": 'EN = {"表にある文": "In the table"}\n',
        },
    )
    table = {"表にある文": "In the table", ("ボタン", "編集"): "Edit"}
    keep = {"残す文": "試験", "もうない文": "試験"}
    found = untranslated(pkg, table, {"b", "c", "gone"}, keep)
    assert [p.split("。")[0] for p in found] == [
        "「表にない文」（a.py:4）が英語の表にありません",
        "「{…}ページ」（a.py:6）は日本語の f 文字列です",
        "文脈「題名」の「編集」（a.py:9）が英語の表にありません",
        "c.py に日本語の文がありません",
        "gone.py がありません",
        "「もうない文」は、もう src にありません",
    ]


def test_sources_start_with_copyright_and_license():
    """ソース（.py・.ps1・.bat）の先頭に、著作権とライセンス（SPDX の形）の2行がある（ファイルが1つだけ写されて使われても、
    作者とライセンスがわかるように）。.bat は「@echo off」の次の行から"""
    root = SRC.parents[1]
    missing = []
    for folder in ("src", "tools", "tests", "windows"):
        for path in sorted((root / folder).rglob("*")):
            if (
                path.suffix not in (".py", ".ps1", ".bat")
                or "__pycache__" in path.parts
            ):
                continue
            lines = path.read_bytes().decode("utf-8-sig").splitlines()
            bat = path.suffix == ".bat"
            head = lines[1:3] if bat else lines[:2]
            prefix = "rem " if bat else "# "
            if head != [prefix + s for s in LICENSE_HEADER]:
                missing.append(path.relative_to(root).as_posix())
    assert missing == []
