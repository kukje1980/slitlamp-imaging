"""시작(홈) 화면 — 큰 아이콘 메뉴로 바로 가기: 신규 환자 · 환자 목록 · 촬영 · 검사 기록 · 설정.
(v1.20.3: Pro 검토 타일 없앰 — Pro 기능은 촬영 화면의 멀티 안에)

참고한 장비 프로그램의 Welcome 화면처럼, 왼쪽에 인사말, 오른쪽에 아이콘 타일을 둔다.
'다음부터 촬영 화면으로 바로 시작' 을 켜면 config.start_screen = "capture".

왼쪽 위에는 큰 사진 칸(HomeHero) — Keeler 판은 배경을 떼어 낸 Keeler 장비 사진이 기본으로 보이고,
'홈 사진 바꾸기' 로 Keeler 사진 중에서 고르거나(config.home_image = "brand:<경로>") 병원 장비 사진 등을 넣을 수 있다
(데이터 폴더 home/ 에 복사).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import __version__, paths
from app import brand as app_brand
from app.ui import icons
from app.ui.home_hero import HomeHero
from app.ui.icon_button import IconTextButton
from app.ui.theme import ICON_ACCENT

PHOTO_FILTER = "사진 (*.jpg *.jpeg *.png *.bmp *.webp)"
BUILTIN = "brand:"                       # config.home_image 가 이것으로 시작하면 브랜드에 든 사진

# (동작 id, 이름, 아이콘, 설명)
TILES = [
    ("newPatient", "신규 환자", "user-plus", "환자 정보를 등록하고 촬영을 시작합니다"),
    ("patients", "환자 목록", "list", "등록된 환자를 검색·선택합니다"),
    ("capture", "촬영", "camera", "슬릿램프 실시간 영상 · 사진 캡처 · 녹화"),
    ("exam", "검사 기록", "clipboard", "촬영일별 영상과 소견을 한눈에"),
    ("settings", "설정", "sliders", "화면 · 카메라 · 저장 · 전송 · 백업"),
]


class HomeScreen(QWidget):
    action = Signal(str)          # TILES 의 동작 id
    startScreenChanged = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setObjectName("Home")
        root = QHBoxLayout(self)
        root.setContentsMargins(48, 36, 48, 28)
        root.setSpacing(40)

        br = app_brand.current()
        k = br.get("k_colors")

        # 왼쪽: 큰 사진 + 인사말 + 안내
        left = QVBoxLayout()
        left.setSpacing(6)
        self.hero = HomeHero()
        left.addWidget(self.hero, 5)
        self.spacer_top = QWidget()                # 사진이 없을 때 인사말을 가운데쯤으로
        left.addWidget(self.spacer_top, 2)
        left.addSpacing(14)
        self.kicker = None
        if k:
            self.kicker = QLabel(f"{br.get('short_name', 'KEELER').upper()}  ·  SLIT LAMP IMAGING")
            self.kicker.setObjectName("HomeKicker")
            left.addWidget(self.kicker)
        hello = QLabel("환영합니다")
        hello.setObjectName("HomeTitle")
        sub = QLabel(br["app_name"])
        sub.setObjectName("HomeSub")
        sub.setWordWrap(True)
        guide = QLabel("환자를 등록하거나 선택한 뒤 촬영을 시작하세요.\n"
                       "촬영한 사진·동영상은 환자별로 저장되고, 검사 기록에서 소견과 함께 볼 수 있습니다.")
        guide.setObjectName("HomeGuide")
        guide.setWordWrap(True)
        left.addWidget(hello)
        left.addWidget(sub)
        if k:
            rule = QFrame()
            rule.setObjectName("HomeRule")               # 짧은 K 4색 밑줄
            left.addSpacing(6)
            left.addWidget(rule)
        left.addSpacing(10)
        left.addWidget(guide)
        left.addStretch(1)
        links = QHBoxLayout()
        links.setSpacing(14)
        self.btn_keys = self._link("단축키 안내", "keyboard")
        self.btn_keys.clicked.connect(lambda: self.action.emit("shortcuts"))
        links.addWidget(self.btn_keys)
        self.btn_photo = self._link("홈 사진 바꾸기", "image")
        self.btn_photo.setToolTip("홈 화면 큰 사진을 병원 장비 사진 등으로 바꿉니다")
        self.btn_photo.clicked.connect(lambda: self._photo_menu())
        links.addWidget(self.btn_photo)
        links.addStretch(1)
        left.addLayout(links)
        self.chk_skip = QCheckBox("다음부터 촬영 화면으로 바로 시작")
        self.chk_skip.setChecked(getattr(config, "start_screen", "home") == "capture")
        self.chk_skip.toggled.connect(self._on_skip)
        left.addSpacing(8)
        left.addWidget(self.chk_skip)
        root.addLayout(left, 4)

        # 오른쪽: 아이콘 타일
        grid_box = QFrame()
        grid_box.setObjectName("HomeTiles")
        grid = QGridLayout(grid_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(14)
        self.tiles: dict[str, IconTextButton] = {}
        # Keeler: 아이콘을 로고 K 의 하늘 · 파랑 · 주황으로 번갈아 (네이비는 상단바·사진 바탕색)
        tile_colors = [k[0], k[1], k[3]] if k else [ICON_ACCENT]
        for i, (aid, text, icon, tip) in enumerate(TILES):
            col = tile_colors[i % len(tile_colors)]
            b = IconTextButton(text, icons.icon(icon, col, 40, disabled="#4a5262"), 40)
            b.setObjectName("HomeTile")
            if k:
                b.setProperty("kc", str(i % 3))
            b.layout().setSpacing(12)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setFixedSize(176, 132)
            b.clicked.connect(lambda _=False, a=aid: self.action.emit(a))
            self.tiles[aid] = b
            grid.addWidget(b, i // 3, i % 3)
        wrap = QVBoxLayout()
        wrap.addStretch(1)
        wrap.addWidget(grid_box, 0, Qt.AlignmentFlag.AlignCenter)
        wrap.addStretch(1)
        self.lbl_version = QLabel(f"Version {__version__}")
        self.lbl_version.setObjectName("HomeGuide")
        wrap.addWidget(self.lbl_version, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(wrap, 6)
        self.refresh_hero()

    @staticmethod
    def _link(text: str, icon: str) -> QPushButton:
        b = QPushButton(icons.gap(text))
        b.setObjectName("HomeLink")
        b.setIcon(icons.icon(icon, "#98a2b3", 18))
        b.setIconSize(QSize(18, 18))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    # ---- 홈 사진 ----
    def hero_path(self) -> str | None:
        """지금 보여줄 사진: 고른 사진(브랜드 사진 · 내 사진) > 브랜드 기본 사진 (없으면 None → 그림)."""
        own = getattr(self.config, "home_image", "") or ""
        if own.startswith(BUILTIN):
            path = app_brand.asset(own[len(BUILTIN):])
            if path:
                return path
        elif own and Path(own).is_file():
            return own
        return app_brand.asset(app_brand.current().get("home_image"))

    @staticmethod
    def builtin_photos() -> list:
        """브랜드에 든 홈 사진 [(경로, 이름)] — 파일이 있는 것만."""
        return [(rel, name) for rel, name in app_brand.current().get("home_images", []) if app_brand.asset(rel)]

    def use_builtin_photo(self, rel: str) -> bool:
        if not app_brand.asset(rel):
            return False
        self._drop_own_photo()
        self.config.home_image = BUILTIN + rel
        self._save_config()
        self.refresh_hero()
        return True

    def _drop_own_photo(self) -> None:
        """데이터 폴더 home/ 에 복사해 둔 내 사진은 지운다 (다른 사진으로 바꿀 때)."""
        old = getattr(self.config, "home_image", "") or ""
        if old and not old.startswith(BUILTIN) and Path(old).parent == paths.get_appdata_dir() / "home":
            Path(old).unlink(missing_ok=True)

    def refresh_hero(self) -> None:
        path = self.hero_path()
        self.hero.set_image(path)
        # 브랜드에 홈 사진 자리가 있거나(Keeler) 사용자가 사진을 넣었을 때만 큰 사진 칸을 보인다
        show = bool(path) or bool(app_brand.current().get("home_image"))
        self.hero.setVisible(show)
        self.spacer_top.setVisible(not show)

    def set_home_image(self, src: str) -> bool:
        """사진을 데이터 폴더(home/)에 복사해 홈 사진으로 쓴다. 열 수 없는 파일이면 False."""
        if not src or QPixmap(src).isNull():
            return False
        folder = paths.ensure_dir(paths.get_appdata_dir() / "home")
        dst = Path(folder) / f"home-{int(time.time() * 1000)}{Path(src).suffix.lower() or '.jpg'}"
        shutil.copyfile(src, dst)
        self._drop_own_photo()
        self.config.home_image = str(dst)
        self._save_config()
        self.refresh_hero()
        return True

    def reset_home_image(self) -> None:
        self._drop_own_photo()
        self.config.home_image = ""
        self._save_config()
        self.refresh_hero()

    def _photo_menu(self) -> None:
        m = QMenu(self)
        current = self.hero_path()
        builtin = {}
        for rel, name in self.builtin_photos():
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(app_brand.asset(rel) == current)
            builtin[a] = rel
        if builtin:
            m.addSeparator()
        a_pick = m.addAction("내 사진 고르기…")
        a_reset = m.addAction("기본 사진으로 되돌리기")
        a_reset.setEnabled(bool(getattr(self.config, "home_image", "")))
        act = self._exec_menu(m, self.btn_photo.mapToGlobal(QPoint(0, self.btn_photo.height())))
        if act in builtin:
            self.use_builtin_photo(builtin[act])
        elif act is a_pick:
            path, _ = QFileDialog.getOpenFileName(self, "홈 화면 사진 고르기", "", PHOTO_FILTER)
            if path and not self.set_home_image(path):
                QMessageBox.warning(self, "홈 사진", "사진 파일을 열 수 없습니다.")
        elif act is a_reset:
            self.reset_home_image()

    @staticmethod
    def _exec_menu(menu, pos):
        return menu.exec(pos)

    def _save_config(self) -> None:
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            pass

    def _on_skip(self, on: bool) -> None:
        self.config.start_screen = "capture" if on else "home"
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            pass
        self.startScreenChanged.emit(self.config.start_screen)
