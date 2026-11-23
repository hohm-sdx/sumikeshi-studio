# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""テスト用の様式のフォルダを作る関数"""

import json
import shutil
import struct
import zlib
from functools import cache
from pathlib import Path

import cv2
from scan import page_image

from sumikeshi_studio import formcheck, formsave, formstore

R8_NAME = "扶養控除等申告書（令和8年分）"


@cache
def bundled_forms() -> tuple[formstore.FormSpec, ...]:
    """配布用パッケージに入れる3様式（formstore.BUNDLED_DIR の .json）。様式のフォルダに入れて読んだときと同じ順（名前の文字の
    符号の順。保険料控除申告書・基・配・特・所の申告書・扶養控除等申告書）"""
    specs = [formstore.read_spec(p) for p in formstore.BUNDLED_DIR.glob("*.json")]
    return tuple(sorted(specs, key=lambda s: (s.name, s.id)))


def bundled(form_id: str) -> formstore.FormSpec:
    return {s.id: s for s in bundled_forms()}[form_id]


def install_bundled(folder: Path) -> None:
    """配布用パッケージの3様式を、様式のフォルダにそのままコピーする（導入で入れたときの代わり。版などは配布用パッケージのファイルのまま）"""
    folder.mkdir(parents=True, exist_ok=True)
    for path in formstore.BUNDLED_DIR.iterdir():
        shutil.copy(path, folder / path.name)


def write_form(
    folder: Path,
    form_id: str,
    name: str,
    *,
    base: str = "nta-fuyou-r9",
    fields: int | None = None,
    revision: int = 1,
    image: str | None = None,
    copy_image: bool = True,
    frame: bool = False,
    **extra,
) -> Path:
    """配布用パッケージの様式 base の型の画像をコピーして、様式のフォルダに様式を1つ書く。書いた .json のパスを返す

    fields を指定すると、base の墨消しする範囲の先頭からその数だけを使う。frame を True にすると、範囲の枠の線の
    記録（frame）も書く（画面で編集して保存したときと同じ。書かなければ、記録のない様式として枠なしになる）。
    extra は .json にそのまま書く。
    """
    src = bundled(base)
    image = image or f"{form_id}-r{revision}.png"
    if copy_image:
        shutil.copy(src.image_path(), folder / image)
    spec = {
        "format": formstore.FORMAT,
        "id": form_id,
        "name": name,
        "revision": revision,
        "updated_at": "2026-11-07T10:15:00",
        "updated_by": "yamada",
        "image": image,
        "size": list(src.size),
        "fields": [
            {"name": f.name, "rect": [f.x, f.y, f.w, f.h]}
            | ({"frame": list(f.frame)} if frame else {})
            for f in src.fields[:fields]
        ],
        "erase": [],
        "title_words": list(src.title_words),
        "title_excludes": list(src.title_excludes),
    }
    spec.update(extra)
    path = folder / f"{form_id}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def make_r8(
    folder: Path, words: tuple[str, ...], form_id: str | None = None
) -> tuple[formstore.FormSpec, ...]:
    """公開様式の扶養控除等申告書 令和8年分（samples/2026bun_01.pdf）から、配布用パッケージの扶養控除等申告書の範囲を移して
    類似した様式を登録し、配布用パッケージの3様式とあわせて様式のフォルダから読んだ、使う様式の組を返す。form_id を省くと、
    画面から登録したときと同じく乱数の識別子にする（回帰テストでは、記録に書く識別子を固定する）"""
    install_bundled(folder)
    work = page_image("2026bun_01")
    fields, _ = formcheck.move_ranges(work, bundled("nta-fuyou-r9"))
    small = formcheck.make_template(work, True, [(f.x, f.y, f.w, f.h) for f in fields])
    _, png = cv2.imencode(".png", small)
    h, w = work.shape
    spec = formstore.FormSpec(
        form_id or formsave.new_user_id(),
        R8_NAME,
        "",
        (w, h),
        tuple(fields),
        title_words=words,
    )
    formsave.save(folder, spec, png.tobytes(), formsave.OP_REGISTER, "試験")
    return formstore.load(formstore.FolderChoice(folder, formstore.ORIGIN_USER)).active


def png_bytes(width: int, height: int) -> bytes:
    """幅・高さの、真っ黒のグレーの PNG（画素の値をすべて0にした、圧縮の小さなもの）"""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    rows = zlib.compress(
        bytes((width + 1) * height), 9
    )  # 各行は、フィルタの種類0と画素
    return (
        formstore.PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", rows)
        + chunk(b"IEND", b"")
    )


def write_png(path: Path, width: int, height: int) -> None:
    """幅・高さの PNG を書く（大きすぎる画像や大きさの合わない画像の扱いを試すため）"""
    path.write_bytes(png_bytes(width, height))
