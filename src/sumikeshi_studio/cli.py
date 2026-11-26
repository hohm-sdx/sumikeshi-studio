# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""コマンドラインから、画面を出さずに、ファイル（PDF・画像）を解析して墨消しした PDF を出力する

呼び方：python -I -m sumikeshi_studio.cli <入力のファイル・フォルダ> … --out <出力のフォルダ>。進み具合と結果は標準出力に、
処理を始められない理由は標準エラー出力に出す。確かめるべきページ（要確認・向き・様式不明・墨消しなし・処理できない）の
あるファイルも出力し、そのページを知らせる（人が出力した後に確かめる）。

画面の部品（QtWidgets）は読み込まない。解析のプロセスと出力のスレッドは、画面と同じもの（session.Session・
workers.Analyzer・workers.Exporter）を、QtCore のイベントループで動かす。ファイルは1つずつ、解析してから出力する
（全ファイルを先に解析すると、全ページの作業画像をメモリに持つため）。
"""

import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer

from . import (
    APP_NAME,
    crashlog,
    exporttext,
    formstext,
    formstore,
    i18n,
    prefs,
    review,
    uitext,
    version,
    workers,
    worklog,
)
from .analysis import AnalysisJob
from .i18n import tr
from .review import Document
from .session import Session

# 終了の番号
EXIT_OK = 0  # 開いたすべてのファイルを出力した（確かめるべきページがあっても 0）
EXIT_NOT_EXPORTED = 1  # 出力しなかったファイルがある
EXIT_NOT_STARTED = 2  # どのファイルも処理しなかった（引数の誤り、様式、起動の準備）
EXIT_STOPPED = 3  # Ctrl+C で途中で止めた

# 使い方と、引数の誤り
HELP = (
    "使い方：sumikeshi-cli.bat <入力のファイル・フォルダ> … --out <出力のフォルダ> [--depth <数> | --recursive] "
    "[--overwrite] [--forms <様式のフォルダ>] [--language ja|en]\n"
    "スキャンした帳票の PDF・画像を、画面を出さずに解析し、様式で決めた欄を墨消しした PDF"
    "（元の名前_redacted.pdf）を出力します。\n"
    "　　<入力のファイル・フォルダ>　PDF・画像のファイルか、フォルダ（フォルダは、直下の PDF・画像を開きます）\n"
    "　　--out <フォルダ>、-o <フォルダ>　出力先のフォルダ（あるフォルダを指定します）\n"
    "　　--depth <数>　フォルダのサブフォルダの中も、その深さまで開きます（0 は直下だけ、1 は1つ下のフォルダまで）。"
    "出力先には、フォルダからの相対パスのフォルダを作ります\n"
    "　　--recursive　フォルダのサブフォルダの中も、すべての深さまで開きます（--depth と一緒には使えません）\n"
    "　　--overwrite　出力先に同じ名前のファイルがあっても上書きします\n"
    "　　--forms <フォルダ>　使う様式のフォルダ（省くと、起動の画面で選んだ様式のフォルダ）\n"
    "　　--language ja|en　文の言語（省くと、画面と同じ言語）\n"
    "　　--help、-h　この使い方を出します\n"
    "確かめるべきページ（⚠ 要確認・⚠ 向き・様式不明・墨消しなし・✕ 処理できません）のあるファイルも出力し、"
    "そのページを知らせます。出力した後に、そのページを確かめてください。\n"
    "終了の番号：0 すべてのファイルを出力した、1 出力しなかったファイルがある、2 処理を始められなかった、"
    "3 途中で止めた"
)
USAGE = "使い方を見るには --help を付けてください。"
NEED_INPUT = "入力のファイルかフォルダを指定してください。"
NEED_OUT = "出力先のフォルダを --out で指定してください。"
OUT_MISSING = "出力先のフォルダがありません：{folder}"
NEED_VALUE = "{option} の後に値を指定してください。"
BAD_LANGUAGE = "--language には ja か en を指定してください。"
BAD_DEPTH = "--depth には 0 以上の数を指定してください。"
DEPTH_AND_RECURSIVE = "--depth と --recursive は、一緒には使えません。"
UNKNOWN_OPTION = "知らない引数です：{arg}"
# 進み具合と結果
START = "{name} {version}：{count}件のファイル（全{pages}ページ）を処理します。"
FORMS_LINE = "様式のフォルダ：{folder}（使う様式 {count}件）"
NO_FORMS = (
    "使う様式が1つもないため、どのファイルも処理しません（様式のフォルダ：{folder}）。"
)
FILE_HEAD = "[{index}／{total}] {name}（{count}ページ）"
ANALYZED = "　　{number}ページ目を解析しました（{done}／{pages}）"
NOT_ANALYZED = "　　{number}ページ目は処理できません（{done}／{pages}）"
CRASHED_PAGES = "{pages}ページ目の解析で、解析のプロセスが途中で終了しました。"
EXPORTED = "　　✓ {out}　墨消しした範囲：{count}か所"
CHECK_HEAD = "　　確かめるべきページ："
CHECK_LINE = "　　　　{label}：{pages}"
FAILED_LINE = "　　　　{label}（墨消しせずに出力しました）：{pages}"
FAILED_REASON = "　　　　　　{number}ページ目：{error}"
PROGRESS = "　　{message}"  # 出力の進み具合（export.export の文）、出力できなかった理由、除いた墨消し済みのPDFの行
NOT_EXPORTED = "　　✗ {name}：出力しませんでした。{reason}"
EXISTS = "出力先に同じ名前のファイルがあります（{out}）。上書きするときは --overwrite を付けてください。"
TOO_LONG = "出力先のパスが長すぎます（{out}）。出力先のフォルダを浅い場所（ドライブのすぐ下など）にすると出力できます。"
SUMMARY = "出力したファイル：{done}件、出力しなかったファイル：{failed}件、確かめるべきページのあるファイル：{check}件"
STOPPED = "途中で止めました。残りの{count}件のファイルは処理していません。"
# Ctrl+C を受け取ったかを Python に戻って確かめる間隔（ミリ秒）。Qt のイベントループの中では、Python のシグナルの
# 処理が動かないため
STOP_CHECK_MS = 200


class UsageError(Exception):
    """引数の誤り（理由の文を持つ）"""


@dataclass
class Args:
    inputs: list[Path] = field(default_factory=list)
    out: Path | None = None
    overwrite: bool = False
    forms: Path | None = None
    language: str | None = None
    help: bool = False
    depth: int | None = 0  # フォルダを開く深さ（None はすべて）
    recursive: bool = False
    depth_given: bool = False


def language_arg(argv: list[str]) -> str | None:
    """引数の --language の値（ja・en でなければ None）。引数の誤りの文もその言語で出すため、ほかの引数より先に読む"""
    for i, a in enumerate(argv):
        if a.startswith("--language="):
            value = a.partition("=")[2]
        elif a == "--language" and i + 1 < len(argv):
            value = argv[i + 1]
        else:
            continue
        return value if value in i18n.LANGUAGES else None
    return None


def parse_args(argv: list[str]) -> Args:
    """引数を読む。誤りは UsageError（argparse は英語の文を出すので使わない）

    パスは、画面と同じく絶対パスにする。作業ログに相対パスのまま書くと、「=」で始まるファイル名が値の先頭に来て、
    表計算のソフトで開いたときに式として扱われるおそれがあるため。
    """
    args = Args()
    i = 0
    while i < len(argv):
        a = argv[i]
        name, eq, value = a.partition("=") if a.startswith("--") else (a, "", "")
        if a in ("-h", "--help"):
            args.help = True
        elif a == "--overwrite":
            args.overwrite = True
        elif a == "--recursive":
            args.recursive = True
        elif name in ("--out", "-o", "--forms", "--language", "--depth"):
            if not eq:
                i += 1
                if i >= len(argv):
                    raise UsageError(tr(NEED_VALUE, option=name))
                value = argv[i]
            if not value:
                raise UsageError(tr(NEED_VALUE, option=name))
            if name == "--forms":
                args.forms = Path(value).resolve()
            elif name == "--language":
                if value not in i18n.LANGUAGES:
                    raise UsageError(tr(BAD_LANGUAGE))
                args.language = value
            elif name == "--depth":
                if not (value.isascii() and value.isdigit()):
                    raise UsageError(tr(BAD_DEPTH))
                args.depth, args.depth_given = int(value), True
            else:
                args.out = Path(value).resolve()
        elif a.startswith("-") and a != "-":
            raise UsageError(tr(UNKNOWN_OPTION, arg=a))
        else:
            args.inputs.append(Path(a).resolve())
        i += 1
    if args.recursive:
        if args.depth_given:
            raise UsageError(tr(DEPTH_AND_RECURSIVE))
        args.depth = None
    return args


def check_args(args: Args) -> None:
    """処理を始める前に確かめる引数（入力・出力先のフォルダ）。誤りは UsageError"""
    if not args.inputs:
        raise UsageError(tr(NEED_INPUT))
    if args.out is None:
        raise UsageError(tr(NEED_OUT))
    if not args.out.is_dir():
        raise UsageError(tr(OUT_MISSING, folder=args.out))


def say(text: str) -> None:
    print(text, flush=True)


def complain(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


class Runner(QObject):
    """開いたファイルを1つずつ解析して出力する（Qt の合図を受けるため QObject にする。出力のスレッドの合図は、
    この部品のスレッド（呼んだスレッド）で受け取る）"""

    def __init__(
        self,
        session: Session,
        loaded: formstore.Loaded,
        targets: list[Document],
        names: dict[Document, Path],
        overwrite: bool,
        not_exported: int,
    ):
        super().__init__()
        self.session = session
        self.loaded = loaded
        self.targets = targets
        self.names = names
        self.overwrite = overwrite
        self.not_exported = not_exported  # 出力しなかったファイルの数（開けない・出力先に同じ名前 を含む）
        self.exported = 0
        self.check_files = 0  # 確かめるべきページのあるファイルの数
        self.index = 0  # 処理しているファイル（targets の番号）
        self.done = 0  # そのファイルの解析が終わったページの数
        self.token: object | None = None
        self.exporting = False
        self.stop_requested = False
        self.code: int | None = None
        self.loop = QEventLoop()

    def run(self) -> int:
        self.session.ready_changed.connect(self.prepared)
        self.session.analyzer.page_done.connect(self.page_done)
        timer = QTimer(self)
        timer.timeout.connect(self.check_stop)
        timer.start(STOP_CHECK_MS)
        say(tr(formstext.PREPARING))
        self.session.start()
        self.loop.exec()
        timer.stop()
        self.session.shutdown()
        return self.code if self.code is not None else EXIT_NOT_STARTED

    def prepared(self, error: str) -> None:
        if error:
            complain(
                tr("{failed}。{error}", failed=tr(uitext.PRELOAD_FAILED), error=error)
            )
            self.finish(EXIT_NOT_STARTED, summary=False)
            return
        self.next_doc()

    def next_doc(self) -> None:
        if self.stop_requested:
            self.stopped()
            return
        if self.index >= len(self.targets):
            self.finish(EXIT_OK if self.not_exported == 0 else EXIT_NOT_EXPORTED)
            return
        doc = self.targets[self.index]
        say(
            tr(
                FILE_HEAD,
                index=self.index + 1,
                total=len(self.targets),
                name=doc.label,
                count=len(doc.pages),
            )
        )
        self.token, self.done = object(), 0
        self.session.analyzer.submit(
            self.token,
            [
                AnalysisJob(i, st.source, None, doc.engine, self.loaded.active)
                for i, st in enumerate(doc.pages)
            ],
        )

    def page_done(self, token, index: int, result, error: str) -> None:
        if token is not self.token:
            return
        doc = self.targets[self.index]
        st = doc.pages[index]
        st.source = review.with_result_dpi(st.source, result)
        # 作業画像は持たない（出力は元のファイルから描き直し、作業画像は高さ・幅だけを使う。1つのファイルのページが
        # 多くても、メモリが増え続けないように）
        st.result = (
            result.without_work().sharing(self.loaded.active)
            if result is not None
            else None
        )
        st.error = error
        self.done += 1
        say(
            tr(
                NOT_ANALYZED if error else ANALYZED,
                number=index + 1,
                done=self.done,
                pages=len(doc.pages),
            )
        )
        if self.done < len(doc.pages):
            return
        self.token = None
        if self.stop_requested:
            self.stopped()
            return
        # 解析のプロセスが途中で終了したページは、墨消しせずに入れるためにページを描くと、また終了するので、そのファイルは
        # 出力を試さない
        crashed = [st.number + 1 for st in doc.pages if st.error == tr(workers.CRASHED)]
        if crashed:
            self.not_exported += 1
            reason = tr(CRASHED_PAGES, pages=exporttext.ranges_text(crashed))
            say(tr(NOT_EXPORTED, name=doc.label, reason=reason))
            self.release(doc)
            return
        self.export(doc)

    def export(self, doc: Document) -> None:
        out = self.names[doc]
        if (
            not self.overwrite and out.exists()
        ):  # 解析のあいだに、出力先に同じ名前のファイルができた
            self.not_exported += 1
            say(tr(NOT_EXPORTED, name=doc.label, reason=tr(EXISTS, out=out)))
            self.release(doc)
            return
        th = workers.Exporter([(doc, out)], self.session.analyzer.masker)
        th.progress.connect(self.export_progress)
        th.file_done.connect(self.file_done)
        th.all_done.connect(lambda doc=doc: self.release(doc))
        self.session.keep(th)
        self.exporting = True
        th.start()

    def export_progress(self, _done: int, _total: int, message: str) -> None:
        # 出力のスレッドは「1／1ファイル目（名前）」の行を前に付けるので、最後の行（していること）だけを出す
        say(tr(PROGRESS, message=message.splitlines()[-1]))

    def file_done(self, doc: Document, out: Path, report, error: str) -> None:
        if report is None:
            self.not_exported += 1
            say(tr(PROGRESS, message=exporttext.failure_text(doc, error)))
            return
        log_error = exporttext.write_worklog(doc, out, report)
        self.exported += 1
        # 出力先のフォルダからの相対パス（画面の結果のダイアログと同じ）
        say(tr(EXPORTED, out=str(doc.folder / out.name), count=report.masked))
        if checks := exporttext.check_pages(doc):
            self.check_files += 1
            say(tr(CHECK_HEAD))
            for label, pages in checks:
                if label == uitext.FAILED_LABEL:
                    say(
                        tr(
                            FAILED_LINE,
                            label=tr(label),
                            pages=exporttext.ranges_text(pages),
                        )
                    )
                    for st in doc.kept:
                        if st.error:
                            say(tr(FAILED_REASON, number=st.number + 1, error=st.error))
                else:
                    say(
                        tr(
                            CHECK_LINE,
                            label=tr(label),
                            pages=exporttext.ranges_text(pages),
                        )
                    )
        if log_error:
            say(tr(exporttext.LOG_FAILED, error=log_error, file=worklog.LOG_NAME))

    def release(self, doc: Document) -> None:
        """1ファイルを終えた。作業画像を手放し、次のファイルに進む"""
        self.exporting = False
        for st in doc.pages:
            st.result = None
        self.index += 1
        self.next_doc()

    def check_stop(self) -> None:
        """Ctrl+C を受け取っていたら、解析中のファイルはそこで止める（出力中なら、出力の終わりを待つ）"""
        if self.stop_requested and self.token is not None and not self.exporting:
            self.session.analyzer.clear()
            self.token = None
            self.stopped()

    def stopped(self) -> None:
        if self.code is not None:
            return
        say(tr(STOPPED, count=len(self.targets) - self.index))
        self.finish(EXIT_STOPPED)

    def finish(self, code: int, summary: bool = True) -> None:
        if self.code is not None:
            return
        self.code = code
        if summary:
            say(
                tr(
                    SUMMARY,
                    done=self.exported,
                    failed=self.not_exported,
                    check=self.check_files,
                )
            )
            say(tr(exporttext.LOG_PATH, file=worklog.log_dir() / worklog.LOG_NAME))
        self.loop.quit()


def run(argv: list[str]) -> int:
    """コマンドラインからの出力（終了の番号を返す）。異常終了の記録は開かない（main が開く）"""
    i18n.set_language(
        language_arg(argv) or i18n.choose_language(prefs.saved_language())
    )
    if not argv:
        # 入力は必ず要るので、引数なしは渡し忘れとして使い方を出す。処理を始めていないので、--help の 0 でなく 2
        complain(tr(HELP))
        return EXIT_NOT_STARTED
    try:
        args = parse_args(argv)
        if args.help:
            say(tr(HELP))
            return EXIT_OK
        check_args(args)
    except UsageError as e:
        complain(str(e))
        complain(tr(USAGE))
        return EXIT_NOT_STARTED
    assert args.out is not None
    _app = QCoreApplication.instance() or QCoreApplication([])
    choice = (
        formstore.FolderChoice(args.forms, formstore.ORIGIN_USER)
        if args.forms is not None
        else prefs.forms_choice()
    )
    loaded = workers.load_forms(choice, prefs.forms_off())
    if text := loaded.blocking_text():
        complain(text)
        return EXIT_NOT_STARTED
    if not loaded.active:
        complain(tr(NO_FORMS, folder=choice.path))
        return EXIT_NOT_STARTED
    session = Session()
    opened = review.open_documents(
        args.inputs, session.engine, session.engine.label, args.depth
    )
    docs, failed = opened.docs, opened.failed
    names = exporttext.output_names(docs, args.out)
    if exporttext.overwrites_input(docs, list(names.values())):
        complain(tr(exporttext.NO_OVERWRITE))
        return EXIT_NOT_STARTED
    say(
        tr(
            START,
            name=tr(APP_NAME),
            version=version.version_text(),
            count=len(docs) + len(failed),
            pages=f"{sum(len(d.pages) for d in docs):,}",
        )
    )
    if opened.skipped:
        # 記録として残すことが多く、件数が多くても読み飛ばせるので、すべて出す
        say(tr(review.SKIPPED_NOTE, count=len(opened.skipped)))
        for name in opened.skipped:
            say(tr(PROGRESS, message=name))
    say(tr(FORMS_LINE, folder=choice.path, count=len(loaded.active)))
    if failed:
        say(review.open_failure_text(failed, bool(docs)))
    elif not docs:
        say(tr(review.NO_FILES_TEXT))
    targets = []
    for d in docs:
        if exporttext.path_too_long(names[d]):
            say(tr(NOT_EXPORTED, name=d.label, reason=tr(TOO_LONG, out=names[d])))
        elif not args.overwrite and names[d].exists():
            say(tr(NOT_EXPORTED, name=d.label, reason=tr(EXISTS, out=names[d])))
        else:
            targets.append(d)
    not_exported = len(failed) + len(docs) - len(targets)
    runner = Runner(session, loaded, targets, names, args.overwrite, not_exported)
    if not targets:
        runner.finish(EXIT_NOT_EXPORTED)
        return EXIT_NOT_EXPORTED
    previous = signal.signal(
        signal.SIGINT, lambda *_: setattr(runner, "stop_requested", True)
    )
    try:
        return runner.run()
    finally:
        signal.signal(signal.SIGINT, previous)


def main() -> None:
    # コンソールの文字コードに無い文字（⚠ など）で止まらないようにする（配布用パッケージの .bat は -X utf8 で UTF-8 にする）
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    crashlog.setup(crashlog.ROLE_COMMAND)
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
