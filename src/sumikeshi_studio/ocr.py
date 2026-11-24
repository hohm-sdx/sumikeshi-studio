# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""OCRの呼び出し

文字を読む OCR は RapidOCR。
90度単位の向きの判定（find_lines・classify_lines）は、RapidOCR の部品を使うため、常に RapidOCR で行う。

RapidOCR は呼び出しのときに渡した指定（use_rec など）を次の呼び出しにも持ち越すため、
ここで毎回すべての指定を明示する。また同時に2か所から呼ばないよう排他する。

墨消しの画面は起動してすぐに Engine を使うため、このモジュールを読み込んだだけでは、
OpenCV・numpy・RapidOCR を読み込まない（使う関数の中で読み込む）。
"""

import os
import re
import threading
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from .i18n import tr

if TYPE_CHECKING:
    import numpy as np
    from rapidocr import RapidOCR


class Engine(StrEnum):
    """文字を読む OCR。値は比べるテストの記録に書く文字列。解析の依頼で使う OCR を渡し、作業ログの「OCR」の列に
    使った OCR を書くための値（OCR は RapidOCR だけ）"""

    RAPIDOCR = "rapidocr"

    @property
    def label(self) -> str:
        """作業ログの「OCR」の列に書く名前"""
        return "RapidOCR"


_lock = threading.Lock()
# OCRに使うスレッドの数。CPUのコアの4分の3とし、残りを画面とほかのソフトに回す。
# 8コアの環境で、全コアを使ったときと6スレッドのときの時間は同じ（4ページで17.7秒と17.1秒）で、
# CPUの使用量は約25%少なかった。4スレッド以下では遅くなった
THREADS = max(2, (os.cpu_count() or 4) * 3 // 4)


@dataclass(frozen=True)
class Text:
    """読み取った文字と、その範囲（画像の画素）"""

    text: str
    left: float
    top: float
    right: float
    bottom: float
    cx: float  # 範囲の中心（4つの角の平均）
    cy: float

    @classmethod
    def from_corners(cls, text: str, corners) -> Text:
        """4つの角（左上から時計回り）から作る"""
        import numpy as np

        c = np.asarray(corners)
        xs, ys = c[:, 0], c[:, 1]
        return cls(
            text,
            float(xs.min()),
            float(ys.min()),
            float(xs.max()),
            float(ys.max()),
            float(xs.mean()),
            float(ys.mean()),
        )

    def centered_in(self, x, y, w, h) -> bool:
        """範囲の中心が、左上 (x, y)・幅 w・高さ h の中にある"""
        return x <= self.cx <= x + w and y <= self.cy <= y + h


def join(texts: list[Text]) -> str:
    """左から順につなげ、空白を除く"""
    ordered = sorted(texts, key=lambda t: (t.left, t.text))
    return re.sub(r"\s", "", "".join(t.text for t in ordered))


@dataclass
class Result:
    """読み取った文字の一覧"""

    texts: list[Text]


# RapidOCR に同梱されたモデルのファイル（rapidocr の models フォルダ）。場所をはっきり渡し、ファイルがないか壊れて
# いるときに RapidOCR がインターネットから取り直す処理を通らないようにする（導入した後のツールは通信しない
# ため）。Rec の文字の一覧は、モデルの中のものを使う
MODELS = {
    "Det": "PP-OCRv6_det_small.onnx",
    "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "Rec": "PP-OCRv6_rec_small.onnx",
}
MISSING_MODEL = (
    "OCR のモデルのファイルがありません（{path}）。ツールをインストールし直してください。"
)


class ModelError(Exception):
    """OCR のモデルのファイルがない"""


def model_params(folder: Path | None = None) -> dict[str, str]:
    """RapidOCR に渡す、モデルのファイルの場所。folder を省くと同梱のもの。ファイルがなければ ModelError"""
    if folder is None:
        import rapidocr

        folder = Path(rapidocr.__file__).resolve().parent / "models"
    params = {}
    for part, name in MODELS.items():
        path = folder / name
        if not path.is_file():
            raise ModelError(tr(MISSING_MODEL, path=path))
        params[f"{part}.model_path"] = str(path)
    return params


@cache
def _engine() -> RapidOCR:
    from rapidocr import RapidOCR

    return RapidOCR(
        params={
            "Global.log_level": "warning",
            "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
            **model_params(),
        }
    )


def read(
    img: np.ndarray, engine: Engine = Engine.RAPIDOCR, upright: bool = False
) -> Result:
    """文字の位置と文字を読み取る。engine が Engine の値でなければ ValueError

    upright は、画像の向きが正しいと分かっているとき True にする。RapidOCR で、行ごとの向きの判定（0度か
    180度か）をせずに読む。向きの判定をすると、長い行を180度と取り違えて逆さに読むことがあるため。
    """
    Engine(engine)
    with _lock:
        res = _engine()(_bgr(img), use_det=True, use_cls=not upright, use_rec=True)
    if not res.txts:
        return Result([])
    return Result([Text.from_corners(t, c) for c, t in zip(res.boxes, res.txts)])


def find_lines(img: np.ndarray):
    """文字の行の位置だけを検出する（文字は読まない）。結果の boxes を使う"""
    with _lock:
        return _engine()(_bgr(img), use_det=True, use_cls=False, use_rec=False)


def classify_lines(crops: list[np.ndarray]) -> list[tuple[str, float]]:
    """切り出した行ごとに、0度か180度かとその確からしさを返す"""
    with _lock:
        return _engine()._load_cls_model()(crops).cls_res


def _bgr(img: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img
