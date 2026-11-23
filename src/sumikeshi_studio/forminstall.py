# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""導入のときに、様式のフォルダに様式がなければ、国税庁の公開様式から作った3様式（サンプルの様式）を入れるかを
尋ねて入れる

使い方：python.exe -I -m sumikeshi_studio.forminstall [--years] [--samples y|n]（install.ps1 が呼ぶ）
--years を付けると（install.ps1 が付ける）、問いと入れた後の文の様式の名前に年分を添える（例：扶養控除等申告書（令和9年分））。
--samples を付けると（install.ps1 が -Samples から渡す）、問いを出さずに、y なら入れ、n なら入れない。様式があるときは、
付けないときと同じく何も入れない。値が y・n でなければ、文を出して 2 で終わる（何も入れない）。
様式のフォルダは、ツールと同じく、起動の画面で選んだフォルダ、なければ既定の場所（prefs.forms_choice）。様式が1つでも
あれば（読めない .json も数える。formstore.has_forms）、尋ねず何も入れない。問いは導入の画面（コンソール）で出し、
Enter だけでも入れる。保存は画面からの保存と同じ formsave.save で行う（版は1、変更の記録の操作は setup）。
文の言語は、導入（install.ps1）が環境変数 SUMIKESHI_STUDIO_LANGUAGE に入れた言語にする。
どの場合も導入は続けるので、文を出したら 0 で終わる（Python が例外で終わったときは 1。install.ps1 は、0 でなければ
その番号を示して続ける）。
"""

import json
import sys
import unicodedata
from collections.abc import Callable

from . import formsave, formstext, formstore, i18n
from .i18n import tr

# 答えは NFKC で正規化し（全角の「Ｙ」なども読む）、前後の空白を除いて小文字にしてから比べる
YES = ("", "y", "yes")
NO = ("n", "no")

QUESTION = (
    "様式のフォルダ（{folder}）に様式がありません。サンプルとして、国税庁の様式（{names}）を入れますか？ "
    "入れるときは Y、入れないときは N を入力して Enter を押してください（何も入力せずに Enter を押すと入れます）。"
    "[Y/n]"
)
ADDED = "様式のフォルダ（{folder}）に、サンプルの様式を入れました：{names}"
NOT_ADDED = (
    "サンプルの様式は入れませんでした。あとで入れるときは、様式のフォルダに様式がない状態で、起動の画面の"
    "「サンプルの様式を入れる」を押してください。"
)
HAS_FORMS = "様式のフォルダ（{folder}）に様式があるため、サンプルの様式は入れません。"
APPEARED = "尋ねているあいだに様式のフォルダ（{folder}）に様式が保存されたため、サンプルの様式は入れませんでした。"
UNREACHABLE_CHOSEN = (
    "起動の画面で選んだ様式のフォルダ（{folder}）に届かないため、サンプルの様式は入れませんでした（{error}）。"
    "導入は続けます。そのフォルダにつながる状態で様式がなければ、起動の画面の「サンプルの様式を入れる」で"
    "入れられます。"
)
UNREACHABLE_DEFAULT = "既定の様式のフォルダ（{folder}）を読めないため、サンプルの様式は入れませんでした（{error}）。導入は続けます。"
FAILED = "サンプルの様式を入れられませんでした（{error}）。途中まで入れた様式は削除しました。導入は続けます。"
BAD_ARGUMENTS = "引数（{arguments}）が決まりに合わないため、サンプルの様式は入れませんでした。--samples は y か n にしてください。"
SAMPLE_ITEM = "{name}（令和{year}年分）"  # 入れる様式の名前と年分（問いと確認で示す）


def bundled_forms() -> list[formstore.FormSpec]:
    """入れる3様式（formstore.BUNDLED_DIR の .json）。様式のフォルダで読んだときと同じ、名前の順"""
    specs = [formstore.read_spec(p) for p in formstore.BUNDLED_DIR.glob("*.json")]
    return sorted(specs, key=lambda s: (s.name, s.id))


def sample_items() -> list[str]:
    """入れる様式の名前と年分の文。配布用パッケージの3様式の .json の name と year から読む（見本の画像や墨消しする
    欄は示さない）。並びは bundled_forms と同じ（名前の順）。year がなければ名前だけ"""
    found = []
    for path in formstore.BUNDLED_DIR.glob("*.json"):
        data = json.loads(path.read_text("utf-8"))
        found.append((data["name"], data.get("id", path.stem), data.get("year")))
    return [
        tr(SAMPLE_ITEM, name=name, year=year) if year else name
        for name, _, year in sorted(found)
    ]


def error_text(e: BaseException) -> str:
    """失敗した理由：例外の種類と、Windows の文（OSError なら strerror。パスは文の中に別に出すので入れない）"""
    detail = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
    return f"{type(e).__name__}: {detail}" if detail else type(e).__name__


def wants_samples(question: str, ask: Callable[[str], str]) -> bool:
    """問いを出し、入れると答えたら True。読めない答えなら同じ問いをもう一度出す。入力が閉じていれば False"""
    while True:
        try:
            answer = ask(question + " ")
        except EOFError:
            return False
        answer = unicodedata.normalize("NFKC", answer).strip().lower()
        if answer in YES:
            return True
        if answer in NO:
            return False


def install(
    choice: formstore.FolderChoice,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    user: str | None = None,
    years: bool = False,
    answer: bool | None = None,
) -> list[formstore.FormSpec]:
    """様式のフォルダ choice に様式がなければ、尋ねてサンプルの様式を入れる。入れた様式を返す（入れなければ空）

    ask は問いを出して答えを返す関数、say は文を1つ出す関数（どちらもテストで置き換える）。user は保存した人
    （省くと Windows の利用者名）。years なら、問いと入れた後の文の様式の名前に年分を添える（sample_items）。
    answer は引数で渡した答え（True は入れる、False は入れない）。None なら尋ねる。
    """
    folder = choice.path
    if (
        choice.origin == formstore.ORIGIN_DEFAULT
    ):  # 既定の場所は、なければ作る（起動の画面と同じ）
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # 作れなければ、下で読めないフォルダとして扱う

    def unreachable(e: OSError) -> list[formstore.FormSpec]:
        text = (
            UNREACHABLE_CHOSEN
            if choice.origin == formstore.ORIGIN_USER
            else UNREACHABLE_DEFAULT
        )
        say(tr(text, folder=folder, error=error_text(e)))
        return []

    try:
        if formstore.has_forms(folder):
            say(tr(HAS_FORMS, folder=folder))
            return []
    except OSError as e:
        return unreachable(e)
    try:
        specs = bundled_forms()
    except Exception as e:  # noqa: BLE001  配布用パッケージが壊れていても、導入は続ける
        say(tr(FAILED, error=error_text(e)))
        return []
    names = i18n.join(sample_items() if years else (s.name for s in specs))
    if answer is None:
        answer = wants_samples(tr(QUESTION, folder=folder, names=names), ask)
    if not answer:
        say(tr(NOT_ADDED))
        return []
    # 尋ねているあいだに、ほかの PC が同じ様式のフォルダに様式を保存したかもしれない
    try:
        if formstore.has_forms(folder):
            say(tr(APPEARED, folder=folder))
            return []
    except OSError as e:
        return unreachable(e)
    warnings: list[str] = []
    try:
        saved = save_samples(folder, specs, user, warnings)
    except Exception as e:  # noqa: BLE001  途中で失敗したら（入れた分は消してある）、導入は続ける
        say(tr(FAILED, error=error_text(e)))
        return []
    text = tr(ADDED, folder=folder, names=names)
    say(formstext.done_with_warnings(text, warnings) if warnings else text)
    return saved


def save_samples(
    folder, specs: list[formstore.FormSpec], user: str | None, warnings: list[str]
) -> list[formstore.FormSpec]:
    """サンプルの様式を1つずつ formsave.save で保存する（変更の記録の操作は setup）。途中で失敗したら、入れた分の
    .json を消してから例外を伝える。保存した後にできなかったこと（変更の記録など）は warnings に足す"""
    saved: list[formstore.FormSpec] = []
    try:
        for spec in specs:
            png = spec.image_path().read_bytes()
            saved.append(
                formsave.save(folder, spec, png, formsave.OP_SETUP, user, warnings)
            )
    except Exception:
        # .json を消せば様式のない状態に戻る（見本の画像は、.json が指さなければ使われない）。
        # 変更の記録の行は、ほかの PC が同じときに書いた行を壊さないよう、消さない
        for spec in saved:
            try:
                (folder / f"{spec.id}.json").unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return saved


def add_samples(
    folder, user: str | None = None
) -> tuple[list[formstore.FormSpec] | None, list[str]]:
    """様式のフォルダに様式がなければ、サンプルの様式を入れる（起動の画面から、裏のスレッドで呼ぶ）。尋ねている
    あいだにほかの PC が様式を保存していれば、入れずに (None, [])。入れたら (入れた様式, できなかったこと)。
    途中で失敗したら、入れた分を消して例外を伝える"""
    if formstore.has_forms(folder):
        return None, []
    warnings: list[str] = []
    return save_samples(folder, bundled_forms(), user, warnings), warnings


def parse_arguments(args: list[str]) -> tuple[bool, bool | None] | None:
    """引数から (years, answer) を読む。answer は --samples の答え（y は True、n は False、なければ None）。決まりに
    合わない引数（知らない引数、--samples の値がない・y と n のほか）があれば None。大文字と小文字は区別しない"""
    years = False
    answer = None
    rest = list(args)
    while rest:
        arg = rest.pop(0)
        if arg == "--years":
            years = True
        elif arg == "--samples" and rest and rest[0].lower() in ("y", "n"):
            answer = rest.pop(0).lower() == "y"
        else:
            return None
    return years, answer


def main(args: list[str] | None = None) -> int:
    from . import prefs  # Qt の設定の読み書き（QSettings）を使う

    # 導入と同じ言語にする（install.ps1 が環境変数に入れる）
    i18n.set_language(i18n.choose_language(None))
    args = sys.argv[1:] if args is None else args
    if (parsed := parse_arguments(args)) is None:
        print(tr(BAD_ARGUMENTS, arguments=" ".join(args)))
        return 2
    years, answer = parsed
    install(prefs.forms_choice(), years=years, answer=answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
