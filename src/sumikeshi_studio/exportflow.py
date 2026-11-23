# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しの画面の出力の流れ：出力先のフォルダを選ぶダイアログ、出力の前の確認、進み具合のダイアログと裏の出力、
作業ログ、結果の画面

出力の文と判断は exporttext.py、墨消しした PDF を作る処理は export.py（解析のプロセスで動かす）にある。
墨消しの画面とは、ExportHost に並べた属性を通して、開いたファイル・開き直したかを見分けるトークン・画面の状態の更新を共有する。
出力中か（exporting）と出力先（out_dir）は、このクラスが持つ。
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog, QWidget

from . import APP_NAME, exporttext, widgets, worklog
from .i18n import tr
from .prefs import OUTPUT_FOLDER_KEY, remembered_folder, settings
from .workers import Exporter

if TYPE_CHECKING:
    from .review import Document


class ExportHost(Protocol):
    """出力の流れが使う、墨消しの画面の属性（墨消しの画面は、ダイアログの親にもなる QWidget）"""

    docs: list[Document]  # 開いたファイル
    token: object  # 開き直すたびに変わるトークン（ダイアログを出しているあいだに開き直していないかを確かめる）
    opening: Any  # 開くファイルとフォルダ（様式のフォルダを読み終わってから開き、開き終わるまで持つ）
    analyzer: Any  # 解析のプロセス（executor で墨消しを頼む）
    session: Any  # 画面どうしで共有する処理（keep で出力のスレッドを持たせる）

    def can_export(self) -> bool: ...

    def export_targets(self) -> list[Document]: ...

    def update_state(self) -> None: ...


class ExportFlow:
    """墨消しの画面の出力の流れ。window は墨消しの画面（ダイアログの親で、ExportHost の属性を持つ）"""

    def __init__(self, window: QWidget | ExportHost):
        self.window = window
        self.exporting = False
        self.out_dir: Path | None = None  # 出力先のフォルダ（結果の画面に示す）
        # 出力先のパスが長すぎて出力しなかったファイル（結果の画面に示す）
        self.too_long: list[Document] = []

    def export_dialog(self):
        if not self.window.can_export():
            return
        # 前に出力したフォルダ（なくなっていれば、出力するファイルのフォルダ）を最初に見せる
        start = remembered_folder(OUTPUT_FOLDER_KEY) or str(
            self.window.export_targets()[0].path.parent
        )
        token = self.window.token
        with widgets.dialog():
            name = QFileDialog.getExistingDirectory(
                self.window, tr("墨消しPDFの出力先のフォルダを選ぶ"), start
            )
        if (
            name and self.window.token is token
        ):  # ダイアログを出しているあいだに開き直していない
            self.export_to(Path(name))

    def export_to(self, folder: Path):
        """出力できるファイルを、folder の下に「元の名前_redacted.pdf」で出力する（開いたフォルダからの相対パスの
        フォルダを作る）

        出力の前に、exporttext.export_questions の文を、その順に1つずつ確かめる。
        """
        if self.exporting:
            return
        plan = exporttext.plan_export(self.window.docs, folder)
        if not plan.jobs:
            return
        if plan.error:
            QMessageBox.warning(self.window, tr(APP_NAME), plan.error)
            return
        token = self.window.token
        for text in exporttext.export_questions(self.window.docs, plan):
            if not widgets.ask(self.window, tr(APP_NAME), text):
                return
            # 確認のメッセージを出しているあいだに開き直していたら、出力しない
            if (
                self.window.token is not token
                or self.exporting
                or self.window.opening is not None
            ):
                return
        self.out_dir = folder
        self.too_long = [d for d, _ in plan.too_long]
        settings().setValue(OUTPUT_FOLDER_KEY, str(folder))
        self.start_export(plan.jobs)

    def start_export(self, jobs: list[tuple[Document, Path]]):
        # 出力するページ（削除したページを除く）の数
        total = sum(len(d.kept) for d, _ in jobs)
        dialog = QProgressDialog(tr("出力しています…"), "", 0, total, self.window)
        dialog.setCancelButton(None)
        dialog.setWindowTitle(tr(APP_NAME))
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        results: list[str] = []
        problems: list[bool] = []
        th = Exporter(
            jobs, lambda: self.window.analyzer.executor
        )  # 墨消しする処理は解析のプロセスで
        th.progress.connect(
            lambda d, n, m: (dialog.setValue(d), dialog.setLabelText(m))
        )
        th.file_done.connect(
            lambda doc, out, rep, err: self.file_exported(
                doc, out, rep, err, results, problems
            )
        )
        th.all_done.connect(lambda: self.export_done(dialog, results, problems))
        self.window.session.keep(th)
        self.exporting = True
        self.window.update_state()
        th.start()

    def file_exported(self, doc, out, report, error, results, problems):
        """1ファイルの出力が終わったとき。作業ログを書き、結果の文を足す

        作業ログを書けなくても（作業ログのファイルを Excel で開いているなど）、結果の文は必ず足し、書けなかった理由を
        入れて「⚠」にする（例外を外へ出すと、そのファイルの結果の文が消えるため）。
        """
        if report is None:
            results.append(exporttext.failure_text(doc, error))
            problems.append(True)
            return
        doc.exported = True
        log_error = exporttext.write_worklog(doc, out, report)
        results.append(exporttext.result_text(doc, out, report, log_error))
        problems.append(exporttext.result_warns(doc, log_error))

    def export_done(self, dialog, results, problems):
        dialog.close()
        self.exporting = False
        self.window.update_state()
        # 作業ログのフォルダは、パスを組み立てるだけでフォルダを作らないので、警告にしないときも求める
        r = exporttext.export_result(
            self.out_dir,
            results,
            problems,
            self.window.docs,
            worklog.log_dir(),
            self.too_long,
        )
        result = widgets.ResultDialog(
            self.window, tr(APP_NAME), r.text, r.warning, r.folders
        )
        result.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        with widgets.dialog():
            result.exec()
