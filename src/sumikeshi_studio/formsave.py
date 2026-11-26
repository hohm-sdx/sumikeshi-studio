# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式の保存・履歴・変更の記録

様式のフォルダに新しい版を保存し、前の版と削除した様式を「history」フォルダに移し、変更の記録の CSV（changes.csv）に
1行足す。フォルダ・ファイルの名前と記録の中身は英語。様式のデータの型と読む処理は formstore.py にある。
フォーマットの番号（formstore.FORMAT）は、呼ぶときに formstore から引く（テストが formstore.FORMAT を置き換えて、
保存に効くことを確かめるため）。
"""

import csv
import getpass
import json
import os
import re
import shutil
import traceback
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from . import formstore
from .boxes import Box, Source
from .i18n import tr

HISTORY_DIR = "history"  # 前の版と、削除した様式を移すフォルダ（様式のフォルダの中）
CHANGE_LOG = "changes.csv"  # 様式の変更の記録
CHANGE_FIELDS = ["Date/time", "User", "Operation", "Form name", "Revision", "Fields"]
# 変更の記録の「Operation」の値。画面に出す操作の名前とは分ける（範囲を描く画面の題名は formeditor が
# 決める）
OP_REGISTER = "register"
OP_EDIT = "edit"
OP_REPLACE = "replace sample image"  # 様式の一覧の「用紙を置き換える」
OP_DELETE = "delete"
OP_SETUP = "setup"  # 導入で、国税庁の公開様式から作った様式を入れた（forminstall）


def new_user_id() -> str:
    """画面から登録する様式の識別子（u- に続けて、重ならない英数字）"""
    return f"u-{uuid.uuid4().hex[:16]}"


def new_spec(
    form_id: str,
    name: str,
    spec: formstore.FormSpec | None,
    size: tuple[int, int],
    boxes: list[Box],
    title_words: tuple[str, ...],
    title_excludes: tuple[str, ...],
    line_match: tuple[bool, float] | None = None,
) -> formstore.FormSpec:
    """範囲を描く画面の内容から、保存する様式を作る。spec は編集を始めた様式（新しい様式なら None）、size は
    作業画像の幅・高さ、line_match は (罫線の並びを判別に使うか, 罫線の一致度の下限)。None なら、編集を始めた様式の
    値（新しい様式は、使うで formstore.LINE_MATCH_MIN）"""
    if line_match is None:
        line_match = (
            (spec.line_match_use, spec.line_match_min)
            if spec is not None
            else (True, formstore.LINE_MATCH_MIN)
        )

    def field(b: Box) -> formstore.Field:
        frame = None if b.source == Source.ERASE else tuple(b.frame or ())
        return formstore.Field(
            b.kind,
            round(b.x),
            round(b.y),
            max(1, round(b.w)),
            max(1, round(b.h)),
            frame,
        )

    return formstore.FormSpec(
        id=form_id,
        name=name,
        image=spec.image if spec is not None else "",
        size=size,
        fields=tuple(field(b) for b in boxes if b.source != Source.ERASE),
        erase=tuple(field(b) for b in boxes if b.source == Source.ERASE),
        title_words=title_words,
        title_excludes=title_excludes,
        revision=spec.revision if spec is not None else 0,
        folder=spec.folder if spec is not None else "",
        # 範囲だけの編集では、見本の罫線をそのまま持つ。登録と新しい用紙への置き換えでは、保存の前に塗る前の画像から
        # 読み直す（formeditor）
        lines=spec.lines if spec is not None else None,
        line_match_use=line_match[0],
        line_match_min=line_match[1],
    )


def to_json(spec: formstore.FormSpec) -> dict:
    def rects(fields):
        out = []
        for f in fields:
            item = {"name": f.name, "rect": [f.x, f.y, f.w, f.h]}
            if f.frame is not None:
                item["frame"] = list(f.frame)
            out.append(item)
        return out

    return (
        {
            "format": formstore.FORMAT,  # 古いフォーマットの様式も、このツールのフォーマットで保存する
            "usable_from_format": formstore.USABLE_FROM_FORMAT,
            "saved_with": spec.saved_with,
            "id": spec.id,
            "name": spec.name,
            "revision": spec.revision,
            "updated_at": spec.updated_at,
            "updated_by": spec.updated_by,
            "image": spec.image,
            "size": list(spec.size),
            "fields": rects(spec.fields),
            "erase": rects(spec.erase),
            "title_words": list(spec.title_words),
            "title_excludes": list(spec.title_excludes),
            "line_match": {"use": spec.line_match_use, "min": spec.line_match_min},
        }
        | ({"lines": lines_json(spec.lines)} if spec.lines is not None else {})
    )


def lines_json(lines) -> dict:
    """見本の罫線を .json に書く形（横の線 h・縦の線 v。線は [位置, 端, 端, 太さ]）"""
    h, v = lines
    return {"h": [list(s) for s in h], "v": [list(s) for s in v]}


@dataclass(frozen=True)
class Saved:
    """様式のフォルダにある、その様式の今の版（保存の直前に読み直し、編集を始めたときの版と比べる）"""

    revision: int  # 読めなければ -1
    updated_at: str = ""
    updated_by: str = ""

    def conflict_text(self) -> str:
        """ほかの人が先に保存していたときの確認の文"""
        if self.revision < 0:
            return tr(
                "この様式のファイルが、ほかの人によって変えられています。上書きしますか？"
            )
        when = self.updated_at.replace("T", " ")[:16]
        return tr(
            "{user}さんが（{when}）に保存しています。上書きしますか？",
            user=self.updated_by,
            when=when,
        )


def saved_revision(folder: Path, form_id: str) -> Saved | None:
    """様式のフォルダにある、その様式の今の版。ファイルがなければ None"""
    path = folder / f"{form_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8-sig"))
        revision = data["revision"]
        if not isinstance(revision, int):
            raise TypeError(revision)
        return Saved(
            revision, str(data.get("updated_at", "")), str(data.get("updated_by", ""))
        )
    except OSError, ValueError, KeyError, TypeError:
        return Saved(-1)


def save(
    folder: Path,
    spec: formstore.FormSpec,
    png: bytes | None,
    operation: str,
    user: str | None = None,
    warnings: list[str] | None = None,
) -> formstore.FormSpec:
    """spec を新しい版として様式のフォルダに保存し、保存したものを返す

    spec.revision は編集を始めたときの版（新しい登録は 0）。png は新しい型の画像で、None なら
    前の版の型の画像を、新しい版の名前でコピーする。ほかの人が先に保存していないかは、
    呼ぶ側が saved_revision で確かめる。
    1. 型の画像を、新しい版の名前（<識別子>-r<版>.png）で書く。同じ名前があれば書かずに版を進める（上書きしない）
    2. .json を一時的な名前で書いてから置き換える（書いている途中のファイルを、ほかの人が読まないように）
    3. 前の版の .json と型の画像を「history」に移し、変更の記録に1行足す

    新しい版の番号は、編集を始めたときの版・今の .json の版・様式のフォルダと「history」にある同じ様式のファイルの
    番号のうち、いちばん大きいものの次にする（削除した後に同じ識別子で保存するときに1から数えると、前の版と同じ
    名前の画像を書いてしまうため）。
    2. までに失敗したら、書いたファイルを消して例外を出す。3. の失敗は保存の成功として扱い、理由を warnings に
    足す（2. で保存は終わっているので、「保存できませんでした」と出さないため）。

    見本の罫線が、横か縦で formstore.MAX_LINES 本を超える様式は、罫線を持たない様式として保存する（読む側は、
    本数が上限を超える .json を、読めない様式にするため。ディザのスキャンなどで、罫線が数千本読まれることがある）。
    """
    if spec.lines is not None and any(
        len(part) > formstore.MAX_LINES for part in spec.lines
    ):
        spec = replace(spec, lines=None)
    path = folder / f"{spec.id}.json"
    old_json = path.read_bytes() if path.exists() else None
    old = saved_revision(folder, spec.id)
    revision = max(
        spec.revision, old.revision if old else 0, _used_revision(folder, spec.id)
    )
    if png is None:
        src = spec.image_path()
        if spec.folder and not src.exists():
            # 編集を始めた後に、ほかの人が保存して、その版の画像が「history」に移っていた
            src = Path(spec.folder) / HISTORY_DIR / spec.image
        png = src.read_bytes()
    revision, image_path = _write_new_image(folder, spec.id, revision, png)
    from .version import version_text

    new = replace(
        spec,
        revision=revision,
        image=image_path.name,
        updated_at=datetime.now().isoformat(timespec="seconds"),
        updated_by=user or getpass.getuser(),
        format=formstore.FORMAT,
        saved_with=version_text(),  # 保存したツールのバージョン（version.txt の作った日を含む）
        folder=str(folder),
    )
    tmp = folder / f".{spec.id}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.write_text(
            json.dumps(to_json(new), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, path)
    except BaseException:
        for f in (tmp, image_path):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    if old_json is not None:
        _after_change(
            warnings,
            "前の版を「history」に移せませんでした",
            lambda: _keep_old(folder, spec.id, old_json, keep_image=image_path.name),
        )
    _after_change(
        warnings,
        "様式の変更の記録（changes.csv）に書けませんでした",
        lambda: append_change(
            folder, operation, new.name, revision, len(new.fields), new.updated_by
        ),
    )
    return new


REVISION_FILE = re.compile(r"-r(\d+)(?:-\d+)?\.(?:json|png)")


def _used_revision(folder: Path, form_id: str) -> int:
    """様式のフォルダと「history」にある、その様式のファイル（<識別子>-r<版>.json・.png）の版のうち最大のもの"""
    best = 0
    for where in (folder, folder / HISTORY_DIR):
        try:
            names = [p.name for p in where.iterdir()]
        except OSError:
            continue
        for name in names:
            if name.startswith(form_id) and (
                m := REVISION_FILE.fullmatch(name[len(form_id) :])
            ):
                best = max(best, int(m[1]))
    return best


def _write_new_image(
    folder: Path, form_id: str, revision: int, png: bytes
) -> tuple[int, Path]:
    """型の画像を、revision の次の版の名前で、同じ名前があれば失敗する開き方で書く。あれば版を進める（10回まで）。
    書いた版とパスを返す（ほかの人がほぼ同時に保存して、同じ名前の画像を書いたときに、上書きしないため）"""
    for n in range(revision + 1, revision + 11):
        path = folder / f"{form_id}-r{n}.png"
        try:
            f = path.open("xb")
        except FileExistsError:
            continue
        try:
            with f:
                f.write(png)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return n, path
    raise OSError(
        tr(
            "見本の画像の名前を決められませんでした（{name} など）",
            name=f"{form_id}-r{revision + 1}.png",
        )
    )


def _after_change(warnings: list[str] | None, what: str, step) -> None:
    """保存・削除が終わった後の処理（history に移す・変更の記録に書く）。失敗しても例外にせず、理由を warnings に足す"""
    try:
        step()
    except Exception as e:  # noqa: BLE001  保存そのものは終わっている
        traceback.print_exc()
        if warnings is not None:
            # what は「〜できませんでした」までの文（画面の言語の文にしてから、理由を足す）
            warnings.append(
                tr("{what}（{error}）", what=tr(what), error=f"{type(e).__name__}: {e}")
            )


def move_to_history(
    folder: Path,
    form_id: str,
    operation: str,
    user: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    """様式のファイル（.json と型の画像）を「history」に移す（削除する）

    .json を読めなくても移す。
    .json を先に移す（画像を先に移すと、画像を移せなかったときに .json だけが残り、全員の PC でその様式が
    「読めません」になるため）。.json を移した後の、画像を移す処理と変更の記録の失敗は、成功として扱い、理由を
    warnings に足す。
    """
    path = folder / f"{form_id}.json"
    data = path.read_bytes()
    name, revision, count, image = _old_info(data)
    history = folder / HISTORY_DIR
    history.mkdir(exist_ok=True)
    stem = f"{form_id}-r{revision}" if isinstance(revision, int) else form_id
    os.replace(path, _unique(history, f"{stem}.json"))
    if image:
        _after_change(
            warnings,
            "見本の画像を「history」に移せませんでした",
            lambda: _move_image(folder, image),
        )
    _after_change(
        warnings,
        "様式の変更の記録（changes.csv）に書けませんでした",
        lambda: append_change(
            folder,
            operation,
            name or path.name,
            revision,
            count,
            user or getpass.getuser(),
        ),
    )


def _old_info(data: bytes) -> tuple[str, int | str, int | str, str]:
    """前の版の .json の、名前・版・範囲の数・型の画像のファイル名（読めた範囲）"""
    try:
        old = json.loads(data.decode("utf-8-sig"))
        old = old if isinstance(old, dict) else {}
    except ValueError:
        old = {}
    revision = old.get("revision", "")
    revision = revision if isinstance(revision, int) else ""
    fields = old.get("fields")
    name = old.get("name") if isinstance(old.get("name"), str) else ""
    image = old.get("image")
    image = image if isinstance(image, str) and Path(image).name == image else ""
    return name, revision, len(fields) if isinstance(fields, list) else "", image


def _move_image(folder: Path, image: str) -> None:
    src = folder / image
    if src.is_file():
        os.replace(src, _unique(folder / HISTORY_DIR, image))


def _keep_old(folder: Path, form_id: str, data: bytes, keep_image: str = "") -> None:
    """前の版の .json（data）のコピーと、その型の画像を「history」に移す（保存の後）"""
    history = folder / HISTORY_DIR
    history.mkdir(exist_ok=True)
    _, revision, _, image = _old_info(data)
    stem = f"{form_id}-r{revision}" if isinstance(revision, int) else form_id
    _unique(history, f"{stem}.json").write_bytes(data)
    if image and image != keep_image:
        _move_image(folder, image)


def _unique(folder: Path, name: str) -> Path:
    """folder の中で重ならないファイルの名前（あれば「-2」「-3」…を付ける）"""
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    return path


def copy_forms(src: Path, dst: Path) -> list[str]:
    """様式のフォルダ src の直下の様式（.json）と、それぞれが指す見本の画像を、様式のないフォルダ dst にコピーする。
    コピーした .json の名前を返す。history と changes.csv、ほかのファイルはコピーしない

    様式ごとに、.json を読み、その .json が指す画像をコピーしてから、読んだ .json を書く（コピーの途中で src の様式が
    ほかの PC で保存されても、.json と画像が食い違わないように）。画像が src になければ、history の同じ名前の画像を
    使う（.json を読んだ後に、ほかの PC が保存して画像が history に移った場合）。dst に同じ名前の画像があれば
    上書きする（様式のないフォルダにだけコピーするので、使われている画像はない）。.json は、同じ名前があれば
    失敗する開き方で書く（コピーの途中でほかの PC が dst に保存した様式を上書きしないため）。.json を読めないときと、
    指す画像がどこにもないときは、.json だけをコピーする（dst でも src と同じく読めない様式として一覧に出る）。
    失敗したら、このコピーで書いた .json を消してから例外を出す（コピーした画像は、.json が指さないので使われない）。
    """
    dst.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        for name in formstore.form_files(src):
            data = (src / name).read_bytes()
            image = _image_name(data)
            if image is not None:
                for place in (src / image, src / HISTORY_DIR / image):
                    if place.is_file():
                        shutil.copyfile(place, dst / image)
                        break
            target = dst / name
            with target.open("xb") as f:
                written.append(target)
                f.write(data)
    except BaseException:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return [p.name for p in written]


def _image_name(data: bytes) -> str | None:
    """様式の .json のバイト列から、見本の画像のファイル名を読む。読めなければ None"""
    try:
        image = json.loads(data.decode("utf-8-sig")).get("image")
    except UnicodeDecodeError, ValueError, AttributeError, RecursionError:
        return None
    if (
        isinstance(image, str)
        and image not in ("", ".", "..")
        and Path(image).name == image
    ):
        return image
    return None


def append_change(
    folder: Path, operation: str, name: str, revision, fields, user: str
) -> None:
    """様式の変更の記録（CSV、Excel で開ける UTF-8 BOM 付き）に1行足す"""
    path = folder / CHANGE_LOG
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CHANGE_FIELDS)
        w.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                user,
                operation,
                name,
                revision,
                fields,
            ]
        )
