"""애플리케이션 진입점.

    python -m app                 # 설정에 따른 소스(기본 device)
    python -m app --source synthetic   # 하드웨어 없이 합성 영상으로 실행
    python -m app --source file        # 설정의 test_file_path 재생
    python -m app --brand keeler       # Keeler 전용 화면 (로고·색)
    python -m app --edition agent      # AI 에이전트 에디션 (별도 데이터 폴더 SlitLampAgent)
    python -m app --import-samples <폴더|zip>   # 질환별 샘플 사진을 등록하고 끝냄 (화면 없음)
    python -m app --brand explorer     # Explorer 판 (메뉴·툴바·환자/검사 트리·겹쳐보기·참조 영상·Word 보고서)
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from app import brand, edition, paths
from app.config import AppConfig
from app.db.database import Database, DatabaseError
from app.logging_setup import install_excepthook, setup_logging


def _apply_cli_overrides(argv, config: AppConfig) -> None:
    for i, a in enumerate(argv):
        if a == "--source" and i + 1 < len(argv):
            config.source_mode = argv[i + 1]
        elif a.startswith("--source="):
            config.source_mode = a.split("=", 1)[1]


def _import_samples_arg(argv):
    for i, a in enumerate(argv):
        if a == "--import-samples" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--import-samples="):
            return a.split("=", 1)[1]
    return None


def _run_sample_import(config, source, log) -> int:
    """화면 없이 샘플 사진만 등록하고 끝낸다. 프로그램이 켜져 있으면 같은 DB 에 동시에 쓰지 않도록 멈춘다."""
    from app.services.sample_import import import_samples

    lock = QLockFile(str(paths.get_appdata_dir() / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(500):
        print("프로그램이 실행 중입니다. 프로그램을 닫은 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    try:
        db = Database(config.db_path).init()
    except DatabaseError as e:
        log.critical("database open failed: %s", e)
        print(f"데이터베이스를 열지 못했습니다: {e}", file=sys.stderr)
        lock.unlock()
        return 1
    try:
        print(import_samples(db, config, source).summary())
        return 0
    except (OSError, ValueError) as e:
        print(f"샘플 사진을 가져오지 못했습니다: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()
        lock.unlock()


class _ErrorNotifier:
    """처리되지 않은 예외를 한 번에 하나씩만 대화상자로 알린다 (연속 오류로 창이 쌓이지 않게)."""

    def __init__(self):
        self._showing = False

    def __call__(self, text: str) -> None:
        if self._showing or QApplication.instance() is None:
            return
        self._showing = True
        try:
            QMessageBox.warning(
                None, "오류",
                f"예상치 못한 오류가 발생했습니다. 프로그램은 계속 실행됩니다.\n\n{text}\n\n"
                f"자세한 내용: {paths.get_appdata_dir() / 'logs' / 'app.log'}")
        finally:
            self._showing = False


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    setup_logging()
    log = logging.getLogger("app")

    ed = edition.select(edition.key_from(argv))       # 데이터 폴더가 달라지므로 설정보다 먼저
    config = AppConfig.load()
    _apply_cli_overrides(argv, config)
    # 브랜드는 UI 모듈을 불러오기 전에 정한다 (아이콘 강조색 등이 import 시점에 정해짐)
    b = brand.select(brand.key_from(argv, getattr(config, "brand", "")))
    if brand.layout() == "explorer":
        from app.ui.explorer.explorer_window import ExplorerWindow as MainWindow
    else:
        from app.ui.main_window import MainWindow
    from app.ui.theme import build_qss, make_app_icon
    log.info("source_mode=%s media_root=%s export_root=%s db=%s",
             config.source_mode, config.media_root, config.export_root, config.db_path)

    samples = _import_samples_arg(argv)
    if samples:
        return _run_sample_import(config, samples, log)

    app = QApplication(argv)
    app.setApplicationName(b["app_name"] + ed["title_suffix"])
    app.setStyle("Fusion")
    app.setWindowIcon(make_app_icon())
    app.setStyleSheet(build_qss(config.theme))
    install_excepthook(_ErrorNotifier())

    # 같은 PC 에서 두 번 실행되면 카메라를 서로 빼앗고 같은 DB 에 동시에 쓰게 된다 → 한 번만 실행
    lock = QLockFile(str(paths.get_appdata_dir() / "app.lock"))
    lock.setStaleLockTime(0)                 # 비정상 종료로 남은 잠금은 프로세스가 없으면 자동 해제
    if not lock.tryLock(500):
        QMessageBox.information(None, b["app_name"],
                                "프로그램이 이미 실행 중입니다.\n이미 열려 있는 창을 사용하세요.")
        return 0

    try:
        db = Database(config.db_path).init()
    except DatabaseError as e:
        log.critical("database open failed: %s", e)
        QMessageBox.critical(None, "데이터베이스 오류",
                             f"{e}\n\n로그: {paths.get_appdata_dir() / 'logs' / 'app.log'}")
        return 1
    stats = db.stats()
    log.info("db ready: patients=%s captures=%s backup=%s",
             stats["patients"], stats["captures"], stats["backup"])

    from app.services.sample_import import ensure_bundled_samples
    ensure_bundled_samples(db, config)        # 설치판의 질환별 샘플 사진 — 처음 실행 때 한 번만 등록

    win = MainWindow(config, db)
    win.show()
    rc = app.exec()
    lock.unlock()
    return rc


if __name__ == "__main__":
    sys.exit(main())
