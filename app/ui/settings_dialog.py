"""설정 다이얼로그.

왼쪽 메뉴에서 분류를 고르면 오른쪽에 그 화면이 나온다. 화면마다 제목 · 한 줄 설명이 있고,
항목마다 아래 회색 글씨로 무엇을 하는 설정인지 쉬운 말로 적어 둔다.

- 도움말: 설정 안내 (메뉴 한눈에 보기 · '이럴 땐 여기' — 누르면 그 화면으로)
- 화면과 촬영: 화면 · 소리 / 촬영 / 사진 목록 · 멀티
- 장비 연결: 카메라 / 촬영 모드 / 단축키 · USB 버튼 (app/ui/usb_buttons_tab.py)
- 사진 관리: 저장 · 삭제 (프로그램을 끌 때 사진 지우기 포함) / 전송 / 백업 (app/ui/backup_tab.py)
- 그 밖에: 참조 · 샘플 / AI 에이전트 / 컴퓨터 사양 (안내만, 맨 끝)

바로 실행 버튼(카메라 다시 연결 · 단축키 설정 · 샘플 사진 가져오기 · 참조 영상 라이브러리)은 main_window 가
actions 로 넘겨 주고, 관련 화면(카메라 · 단축키 · USB 버튼 · 참조 · 샘플)에 있다.
self.tabs 는 탭 줄을 숨긴 QTabWidget — 화면 이름은 tabText 로 그대로 찾는다 (show_tab).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import sounds, system_specs
from app.capture.frame_utils import device_names, enumerate_devices, is_sony
from app.ui import icons
from app.ui.backup_tab import BackupTab
from app.ui.theme import ICON_ACCENT, palette
from app.ui.usb_buttons_tab import UsbButtonsTab

_THEMES = [("dark", "어두운 화면"), ("light", "밝은 화면")]
_SOURCES = [("device", "카메라 · 캡처 장비 (진료할 때)"), ("file", "동영상 파일 재생 (시험용)"),
            ("synthetic", "시험 화면 (카메라 없이 연습)")]
_PHOTO_FORMATS = [("png", "PNG — 화질 손실 없음 (기본)"), ("jpg", "JPG — 파일이 작음")]
_EXIT_SCOPES = [("all", "모든 환자의 사진 · 동영상 (샘플 사진 제외)"), ("today", "오늘 촬영한 사진 · 동영상"),
                ("session", "이번에 프로그램을 켠 뒤 촬영한 사진 · 동영상")]

# 왼쪽 메뉴: (분류, [(화면 이름, 아이콘, 설정 안내에 보일 한 줄 설명)])
PAGES = [
    ("도움말", [
        ("설정 안내", "book", "설정 화면 사용법 · 메뉴 한눈에 보기 · 이럴 땐 여기"),
    ]),
    ("화면과 촬영", [
        ("화면 · 소리", "sun", "화면 색(어둡게 · 밝게), 켰을 때 처음 화면, Smart 도구줄, 찰칵 소리 · 크기"),
        ("촬영", "camera", "지금 촬영 모드, 사진을 찍은 뒤 큰 화면으로 보기, 그 화면에서 촬영 버튼을 누르면 할 일"),
        ("사진 목록 · 멀티", "review", "오른쪽 사진 목록 보는 방식, 사진 오른쪽 클릭 메뉴, 멀티 · 분할 화면"),
    ]),
    ("장비 연결", [
        ("카메라", "videocam", "쓸 카메라 고르기 · 다시 연결, 화면 크기(해상도), 소니 카메라 연결 방법"),
        ("촬영 모드", "sliders", "자주 쓰는 밝기 · 노출 · 해상도 등을 이름 붙여 저장하고 고치기"),
        ("단축키 · USB 버튼", "keyboard", "키보드 단축키, 슬릿램프 USB 버튼 · 좌우(OD · OS) 신호"),
    ]),
    ("사진 관리", [
        ("저장 · 삭제", "folder", "사진 형식 · 동영상 화질 · 저장 폴더, 프로그램을 끌 때 촬영 사진 지우기"),
        ("전송", "send", "'전송' 버튼으로 사진을 폴더 · 클라우드 · PACS 로 보내는 방법과 파일 형식"),
        ("백업", "save", "외장 하드 · 다른 드라이브에 사진과 환자 기록을 자동 · 수동 백업"),
    ]),
    ("그 밖에", [
        ("참조 · 샘플", "image", "질환별 샘플 사진 · 참조 영상 라이브러리 열기, 샘플 사진 가져오기"),
        ("AI 에이전트", "bot", "장면 인식 · 자동 촬영 · 전후 클립 등 AI 도우미 (선택 기능)"),
        ("컴퓨터 사양", "info", "이 PC 가 4K 촬영 · 녹화에 충분한지 확인 (보기만 — 바꾸는 설정 없음)"),
    ]),
]

# 예전 탭 이름으로 불러도 그 내용이 있는 화면으로 (show_tab)
_OLD_NAMES = {"일반": "화면 · 소리", "카메라 · 저장": "카메라", "USB 버튼": "단축키 · USB 버튼"}

# 설정 안내 → '이럴 땐 여기를 보세요': (궁금한 것, 화면, 표준판(촬영 프로그램)에만 있음)
_FAQ = [
    ("카메라 화면이 안 나오거나 멈췄어요 · 다른 카메라로 바꾸고 싶어요", "카메라", False),
    ("사진을 찍은 뒤 큰 화면이 뜨지 않게 하고 싶어요", "촬영", False),
    ("찰칵 소리를 줄이거나 끄고 싶어요", "화면 · 소리", False),
    ("사진이 어디에 저장되는지 알고 싶어요 · 사진 형식 · 동영상 화질을 바꾸고 싶어요", "저장 · 삭제", False),
    ("프로그램을 끌 때 찍은 사진을 이 PC 에서 지우고 싶어요", "저장 · 삭제", True),
    ("찍은 사진을 다른 PC · 클라우드 · PACS 로 보내고 싶어요", "전송", False),
    ("외장 하드에 사진을 백업하고 싶어요", "백업", False),
    ("촬영 키나 슬릿램프 버튼이 하는 일을 바꾸고 싶어요", "단축키 · USB 버튼", False),
    ("사진을 오른쪽 클릭했을 때 나오는 메뉴를 바꾸고 싶어요", "사진 목록 · 멀티", False),
    ("질환 샘플 사진 · 참조 사진을 보고 싶어요", "참조 · 샘플", False),
]


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, actions: dict | None = None, standard: bool = True):
        """actions: {"camera": 함수, "shortcuts": 함수(parent), "samples": 함수(parent), "references": 함수()}
        — 없으면 그 버튼은 꺼짐. "references" 는 이 설정 창을 닫은 뒤 부른다 (라이브러리 창이 모달 설정 창에 막히지 않게).
        standard=False (Explorer 판): 참조 라이브러리는 메뉴에 따로 있고, 촬영 직후 멀티 캡처 · 끌 때 사진 지우기는 쓰지 않음."""
        super().__init__(parent)
        self.config = config
        self._actions = actions or {}
        self._standard = standard
        self.setWindowTitle("설정")

        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()                    # 화면 고르기는 왼쪽 메뉴로
        self.tabs.setDocumentMode(True)
        self._build_guide_page()
        self._build_display_page()
        self._build_capture_page()
        self._build_list_page()
        self._build_camera_page()
        self._build_preset_page()
        self._build_keys_page()
        self._build_storage_page()
        self._build_send_page()
        self._build_backup_page()
        self._build_reference_page()
        self._build_agent_page()
        self._build_specs_page()                     # 안내만 하는 화면은 맨 끝
        # 상자 안 묶음용 빈 QWidget 은 배경을 칠하지 않게 (전체 QWidget 배경색이 상자 색 위에 띠로 보이지 않게)
        for w in self.tabs.findChildren(QWidget):
            if type(w) is QWidget and not w.objectName():
                w.setObjectName("SettingsClear")

        self.nav = self._build_nav()
        self.tabs.currentChanged.connect(self._sync_nav)
        self._sync_nav(self.tabs.currentIndex())

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        foot_hint = QLabel("바꾼 내용은 '확인' 을 눌러야 저장됩니다. '취소' 를 누르면 처음 그대로 남습니다.")
        foot_hint.setObjectName("Muted")
        foot = QHBoxLayout()
        foot.setContentsMargins(12, 6, 12, 10)
        foot.addWidget(foot_hint, 1)
        foot.addWidget(btns)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.nav)
        body.addWidget(self.tabs, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(body, 1)
        lay.addWidget(self._rule())
        lay.addLayout(foot)

        w, h = 1000, 700
        scr = self.screen().availableGeometry() if self.screen() is not None else None
        if scr is not None:
            w, h = min(w, scr.width() - 40), min(h, scr.height() - 60)
        self.resize(max(720, w), max(480, h))

    # ================= 틀: 왼쪽 메뉴 · 화면 · 묶음 · 항목 =================
    def _build_nav(self) -> QListWidget:
        nav = QListWidget()
        nav.setObjectName("SettingsNav")
        nav.setFixedWidth(206)
        nav.setIconSize(QSize(18, 18))
        nav.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        muted = QColor(palette(getattr(self.config, "theme", "dark"))["ink_muted"])
        names = self.page_names()
        for group, pages in PAGES:
            head = QListWidgetItem(group)
            head.setFlags(Qt.ItemFlag.NoItemFlags)       # 분류 제목은 고를 수 없음
            f = head.font()
            f.setBold(True)
            f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
            head.setFont(f)
            head.setForeground(muted)
            nav.addItem(head)
            for name, icon_name, tip in pages:
                if name not in names:
                    continue
                it = QListWidgetItem(icons.icon(icon_name, ICON_ACCENT, 18), name)
                it.setData(Qt.ItemDataRole.UserRole, name)
                it.setToolTip(tip)
                nav.addItem(it)
        nav.currentItemChanged.connect(
            lambda cur, _prev: self.show_tab(cur.data(Qt.ItemDataRole.UserRole))
            if cur is not None and cur.data(Qt.ItemDataRole.UserRole) else None)
        return nav

    def _sync_nav(self, index: int) -> None:
        nav = getattr(self, "nav", None)
        if nav is None:
            return
        name = self.tabs.tabText(index)
        for row in range(nav.count()):
            if nav.item(row).data(Qt.ItemDataRole.UserRole) == name:
                if nav.currentRow() != row:
                    nav.blockSignals(True)
                    nav.setCurrentRow(row)
                    nav.blockSignals(False)
                return

    def _page(self, name: str, intro: str) -> QVBoxLayout:
        """새 화면 (왼쪽 메뉴 한 칸): 제목 · 설명 · 스크롤. 내용을 넣을 세로 레이아웃을 돌려준다."""
        body = QWidget()
        body.setObjectName("SettingsPage")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(6)
        title = QLabel(name)
        title.setObjectName("SettingsTitle")
        lay.addWidget(title)
        if intro:
            d = QLabel(intro)
            d.setObjectName("Muted")
            d.setWordWrap(True)
            lay.addWidget(d)
        area = QScrollArea()
        area.setObjectName("SettingsScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(body)
        self.tabs.addTab(area, name)
        return lay

    @staticmethod
    def _group(lay: QVBoxLayout, title: str) -> QVBoxLayout:
        """화면 안의 묶음 (작은 제목 + 테두리 상자). 상자 안 세로 레이아웃을 돌려준다."""
        head = QLabel(title)
        head.setObjectName("SettingsGroup")
        lay.addSpacing(8)
        lay.addWidget(head)
        card = QFrame()
        card.setObjectName("SettingsCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 14)
        cl.setSpacing(12)
        lay.addWidget(card)
        return cl

    @staticmethod
    def _desc(text: str, warn: bool = False) -> QLabel:
        d = QLabel(text)
        d.setObjectName("SettingsWarn" if warn else "SettingsDesc")
        d.setWordWrap(True)
        d.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        return d

    def _option(self, card: QVBoxLayout, label: str, widget: QWidget, desc: str = "",
                warn: str = "", fill: bool | None = None) -> QWidget:
        """항목 한 줄: 왼쪽 굵은 이름 · 오른쪽 조절 칸 + 그 아래 회색 설명 (warn = 주황 주의 글씨)."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        name = QLabel(label)
        name.setObjectName("SettingsLabel")
        name.setFixedWidth(150)
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        if fill is None:
            fill = not isinstance(widget, (QComboBox, QCheckBox, QPushButton))
        if fill:
            col.addWidget(widget)
        else:
            col.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)
        if desc:
            col.addWidget(self._desc(desc))
        if warn:
            col.addWidget(self._desc(warn, warn=True))
        h.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
        h.addLayout(col, 1)
        card.addWidget(row)
        return row

    @staticmethod
    def _row(*widgets) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for i, x in enumerate(widgets):
            h.addWidget(x, 1 if i == 0 else 0)
        return w

    @staticmethod
    def _rule() -> QFrame:
        f = QFrame()
        f.setObjectName("ThumbSep")
        f.setFixedHeight(1)
        return f

    def page_names(self) -> list[str]:
        return [self.tabs.tabText(i) for i in range(self.tabs.count())]

    def page_of(self, widget) -> str:
        """widget 이 들어 있는 화면 이름 (없으면 "")."""
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if page is widget or page.isAncestorOf(widget):
                return self.tabs.tabText(i)
        return ""

    def show_tab(self, name: str) -> None:
        name = _OLD_NAMES.get(name, name)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)
                return

    # ================= 설정 안내 =================
    def _build_guide_page(self) -> None:
        lay = self._page("설정 안내",
                         "왼쪽 메뉴에서 바꾸고 싶은 분류를 고르세요. 각 항목 아래 회색 글씨가 그 설정이 무엇을 하는지 "
                         "알려 줍니다. 무엇을 어디서 바꾸는지 모르겠으면 아래 '이럴 땐 여기를 보세요' 에서 고르면 그 화면으로 갑니다.")
        card = self._group(lay, "이럴 땐 여기를 보세요")
        card.setSpacing(2)
        self.faq_buttons = {}
        for question, page, standard_only in _FAQ:
            if standard_only and not self._standard:
                continue
            q = QLabel("•  " + question)
            q.setWordWrap(True)
            b = QPushButton(f"{page}  ›")
            b.setObjectName("SettingsLink")
            b.setToolTip(f"'{page}' 화면으로 갑니다")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=page: self.show_tab(p))
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(q, 1)
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
            card.addLayout(row)
            self.faq_buttons[question] = b

        card = self._group(lay, "메뉴 한눈에 보기")
        card.setSpacing(4)
        for group, pages in PAGES[1:]:
            g = QLabel(group)
            g.setObjectName("SettingsSubhead")
            card.addWidget(g)
            for name, icon_name, tip in pages:
                b = QPushButton(icons.gap(name))
                b.setObjectName("SettingsLink")
                b.setIcon(icons.icon(icon_name, ICON_ACCENT, 18))
                b.setIconSize(QSize(18, 18))
                b.setFixedWidth(190)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, p=name: self.show_tab(p))
                d = self._desc(tip)
                row = QHBoxLayout()
                row.setSpacing(10)
                row.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
                row.addWidget(d, 1)
                card.addLayout(row)

        card = self._group(lay, "알아 두면 좋은 점")
        for text in ("바꾼 내용은 창 아래 '확인' 을 눌러야 저장됩니다. '취소' 를 누르면 바꾸기 전 그대로 남습니다.",
                     "카메라 · 해상도 · 촬영 모드처럼 카메라에 관한 설정은 '확인' 뒤 카메라가 잠시 다시 켜집니다.",
                     "마우스를 버튼이나 칸 위에 잠시 올려 두면 더 자세한 설명이 나오는 곳도 있습니다."):
            card.addWidget(self._desc("•  " + text))
        lay.addStretch(1)

    # ================= 화면 · 소리 =================
    def _build_display_page(self) -> None:
        c = self.config
        lay = self._page("화면 · 소리", "프로그램이 어떻게 보이고, 촬영할 때 어떤 소리를 낼지 정합니다.")
        card = self._group(lay, "화면")
        self.theme = QComboBox()
        for code, text in _THEMES:
            self.theme.addItem(text, code)
        self.theme.setCurrentIndex(1 if c.theme == "light" else 0)
        self._option(card, "화면 테마", self.theme,
                     "어두운 화면은 어두운 진료실에서 눈이 덜 부시고 사진 색을 보기 좋습니다. "
                     "밝은 화면은 밝은 곳에서 글씨가 잘 보입니다.")
        self.start_home = QCheckBox("프로그램을 켜면 홈(아이콘 메뉴) 화면에서 시작")
        self.start_home.setChecked(getattr(c, "start_screen", "home") != "capture")
        self._option(card, "시작 화면", self.start_home,
                     "켜면 큰 아이콘 메뉴(홈)부터 나오고, 끄면 바로 촬영 화면에서 시작합니다.")
        self.smart_visible = QCheckBox("촬영 화면에 Smart 도구줄 보이기")
        self.smart_visible.setChecked(bool(getattr(c, "smart_bar_visible", True)))
        self._option(card, "Smart 도구줄", self.smart_visible,
                     "촬영 화면의 초점 도우미 줄(관심 영역 · Best Shot · 자동 정지 / 저장 · 포커스 피킹)입니다. "
                     "숨겨도 그 안에서 켜 둔 자동 촬영 설정은 그대로 동작합니다.")

        card = self._group(lay, "소리")
        self.sounds = QCheckBox("효과음 켜기")
        self.sounds.setChecked(bool(c.sounds_enabled))
        self._option(card, "효과음", self.sounds,
                     "사진 캡처(찰칵) · 정지영상 · 녹화 시작 / 중지를 소리로 알려 줍니다.")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setSingleStep(5)
        self.volume.setPageStep(10)
        self.volume.setValue(int(max(0, min(100, c.sound_volume))))
        self.volume.setToolTip("효과음 크기 (0 = 무음)")
        self.volume_label = QLabel(f"{self.volume.value()}%")
        self.volume_label.setMinimumWidth(40)
        self.btn_preview = QPushButton("들어보기")
        self.btn_preview.setToolTip("지금 슬라이더 크기로 '찰칵' 소리를 재생")
        vol_w = self._row(self.volume, self.volume_label, self.btn_preview)
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        self.btn_preview.clicked.connect(self._preview_sound)
        self.sounds.toggled.connect(vol_w.setEnabled)
        vol_w.setEnabled(self.sounds.isChecked())
        self._option(card, "효과음 크기", vol_w, "'들어보기' 로 지금 크기를 미리 들어 볼 수 있습니다. 0% 는 소리 없음.")
        lay.addStretch(1)

    # ================= 촬영 =================
    def _build_capture_page(self) -> None:
        c = self.config
        lay = self._page("촬영", "어떤 촬영 모드로 찍을지, 사진을 찍은 다음 화면이 어떻게 바뀔지 정합니다.")
        card = self._group(lay, "촬영 모드")
        # 촬영 모드 (예전 촬영 도크의 '모드' 버튼): 고르면 확인 때 바로 바뀜 · 만들기 · 고치기는 '촬영 모드' 화면
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(220)
        self.preset_combo.setToolTip("자주 쓰는 카메라 · 촬영 설정 묶음 — 고르고 '확인' 을 누르면 바로 바뀝니다")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_combo)
        self.btn_preset_edit = QPushButton("모드 만들기 · 편집…")
        self.btn_preset_edit.clicked.connect(lambda: self.show_tab("촬영 모드"))
        preset_row = QWidget()
        prl = QHBoxLayout(preset_row)
        prl.setContentsMargins(0, 0, 0, 0)
        prl.addWidget(self.preset_combo)
        prl.addWidget(self.btn_preset_edit)
        prl.addStretch(1)
        self._option(card, "지금 촬영 모드", preset_row,
                     "카메라 밝기 · 노출 · 해상도 · 자동 촬영 등을 묶어 둔 것입니다. 고르고 '확인' 을 누르면 바로 바뀝니다. "
                     "새로 만들거나 고치려면 '모드 만들기 · 편집…'.")

        card = self._group(lay, "사진을 찍은 뒤")
        self.view_after = QCheckBox("찍은 사진을 큰 화면으로 바로 보기")
        self.view_after.setChecked(bool(getattr(c, "view_after_capture", True)))
        self._option(card, "촬영 후 보기", self.view_after,
                     "켜면 찍은 사진이 큰 창으로 바로 떠서 초점 · 밝기를 확인할 수 있습니다. '다시 촬영' · Enter · Esc 로 "
                     "촬영 화면에 돌아옵니다. 끄면 사진은 오른쪽 목록에만 쌓이고 계속 찍을 수 있습니다.")
        self.capture_in_view = QComboBox()
        self.capture_in_view.addItem("초기화 — 그 화면을 닫고 촬영 화면으로 (사진은 찍지 않음)", "reset")
        self.capture_in_view.addItem("바로 촬영 — 사진을 바로 다시 찍기 (예전 방식)", "capture")
        self.capture_in_view.setCurrentIndex(
            max(0, self.capture_in_view.findData(getattr(c, "capture_in_view", "reset"))))
        self.capture_in_view.setToolTip("촬영 직후 큰 화면이나 멀티 화면에서 촬영 버튼 · 촬영 단축키 · "
                                        "슬릿램프 USB 사진 버튼을 눌렀을 때")
        row = self._option(card, "큰 화면 · 멀티에서 촬영 버튼", self.capture_in_view,
                           "찍은 사진을 큰 화면이나 멀티 화면으로 보고 있을 때 촬영 버튼 · 촬영 단축키 · 슬릿램프 USB 사진 버튼을 "
                           "누르면: '초기화' 는 보던 화면을 닫고 고른 사진을 비워 실시간 촬영 화면으로 돌아가기만 합니다. "
                           "'바로 촬영' 은 화면을 닫고 곧바로 새 사진을 찍습니다.")
        if not self._standard:
            row.hide()
        lay.addStretch(1)

    # ================= 사진 목록 · 멀티 =================
    def _build_list_page(self) -> None:
        c = self.config
        lay = self._page("사진 목록 · 멀티",
                         "촬영 화면 오른쪽의 사진 목록과, 사진 여러 장을 나란히 보는 멀티 · 분할 화면을 정합니다.")
        card = self._group(lay, "오른쪽 사진 목록")
        self.split_eyes = QCheckBox("OD(우안) · OS(좌안)로 나눠 보기")
        self.split_eyes.setChecked(bool(getattr(c, "thumbs_split_eyes", False)))
        self._option(card, "눈별로 나눠 보기", self.split_eyes,
                     "켜면 사진 목록을 오른눈 · 왼눈 두 칸으로 나눠 보여 줍니다. 끄면 촬영한 날짜별로 모아 보여 줍니다.")
        self.right_click = QComboBox()
        self.right_click.addItem("메뉴 — 전송 · 삭제 · 멀티 중에서 고르기", "menu")
        self.right_click.addItem("멀티 화면 바로", "multi")
        self.right_click.setCurrentIndex(max(0, self.right_click.findData(getattr(c, "right_click_action", "menu"))))
        self.right_click.setToolTip("오른쪽 사진 목록에서 사진을 마우스 오른쪽으로 눌렀을 때")
        self._option(card, "사진 오른쪽 클릭", self.right_click,
                     "사진을 마우스 오른쪽 단추로 눌렀을 때 할 일입니다. '메뉴' 는 아래에서 고른 항목이 담긴 작은 메뉴를 띄우고, "
                     "'멀티 화면 바로' 는 메뉴 없이 그 사진을 가운데 멀티 화면에 놓습니다.")
        self.menu_items = {}
        items_row = QHBoxLayout()
        items_row.setContentsMargins(0, 0, 0, 0)
        want = list(getattr(c, "right_click_items", None) or ["send", "multi", "delete"])
        for key, text in (("send", "전송"), ("send_delete", "전송(삭제)"), ("multi", "멀티"), ("delete", "삭제")):
            cb = QCheckBox(text)
            cb.setChecked(key in want)
            if key == "send_delete":
                cb.setToolTip("보낸 뒤 이 PC 에서 삭제 — 모두 보냈을 때만, 확인 후")
            self.menu_items[key] = cb
            items_row.addWidget(cb)
        items_row.addStretch(1)
        self.menu_items_w = QWidget()
        self.menu_items_w.setLayout(items_row)
        self.right_click.currentIndexChanged.connect(
            lambda _i: self.menu_items_w.setEnabled(self.right_click.currentData() == "menu"))
        self.menu_items_w.setEnabled(self.right_click.currentData() == "menu")
        self._option(card, "메뉴에 넣을 항목", self.menu_items_w,
                     "전송 = '전송' 설정대로 보내기 · 전송(삭제) = 보낸 뒤 이 PC 에서 지우기(모두 보냈을 때만, 확인 후) · "
                     "멀티 = 멀티 화면에 놓기 · 삭제 = 이 PC 에서 지우기(확인 후). '메뉴' 를 골랐을 때만 씁니다.")
        self.live_multi = QCheckBox("왼쪽 클릭으로 고르면 바로 멀티 화면에 놓기")
        self.live_multi.setChecked(bool(getattr(c, "live_multi", False)))
        self._option(card, "실시간 멀티화면", self.live_multi,
                     "켜면 사진 목록에서 사진을 왼쪽 클릭으로 고르는 즉시 가운데 멀티 화면에 놓입니다. "
                     "고른 사진을 모두 빼면 다시 실시간(라이브) 화면이 됩니다.")

        card = self._group(lay, "분할 화면")
        self.split_count = QComboBox()
        for n in (2, 4, 6, 8, 16):
            self.split_count.addItem(f"{n}분할", n)
        i = self.split_count.findData(int(getattr(c, "split_count", 4) or 4))
        self.split_count.setCurrentIndex(i if i >= 0 else 1)
        self._option(card, "분할 칸 수", self.split_count,
                     "촬영 화면의 '분할' 버튼을 누르면 화면을 몇 칸으로 나눠 사진을 차례로 담을지 정합니다.")
        self.split_ask = QCheckBox("분할 버튼을 누를 때마다 칸 수 묻기")
        self.split_ask.setChecked(not bool(getattr(c, "split_count_saved", False)))
        # 여기서 칸 수를 바꾸면 그 칸 수를 저장한 것으로 — 분할 버튼은 묻지 않고 바로 시작
        self.split_count.currentIndexChanged.connect(lambda _i: self.split_ask.setChecked(False))
        self._option(card, "칸 수 묻기", self.split_ask,
                     "켜 두면 누를 때마다 칸 수를 고르는 창이 뜹니다. 끄면 위 칸 수로 바로 시작합니다 "
                     "(위에서 칸 수를 바꾸면 저절로 꺼집니다).")
        lay.addStretch(1)

    # ================= 카메라 =================
    def _build_camera_page(self) -> None:
        c = self.config
        lay = self._page("카메라", "어떤 카메라(캡처 장비)에서 화면을 받을지 정합니다. "
                                 "'확인' 을 누르면 설정이 저장되고 카메라가 다시 켜집니다.")
        card = self._group(lay, "카메라 연결")
        self.btn_camera = self._quick("카메라 다시 연결", "videocam", "캡처 장비(카메라)를 끊었다가 다시 연결합니다", "camera")
        self.quick_status = QLabel("")
        self.quick_status.setObjectName("Muted")
        self._option(card, "다시 연결", self._row(self.btn_camera, self.quick_status),
                     "화면이 멈추거나 검게 나올 때 눌러 보세요. 카메라를 끊었다가 바로 다시 연결합니다.", fill=True)
        self.source_mode = QComboBox()
        for code, text in _SOURCES:
            self.source_mode.addItem(text, code)
        if self.source_mode.findData(c.source_mode) < 0:
            self.source_mode.addItem(c.source_mode, c.source_mode)
        self.source_mode.setCurrentIndex(self.source_mode.findData(c.source_mode))
        self._option(card, "영상 받는 곳", self.source_mode,
                     "진료할 때는 '카메라 · 캡처 장비' 를 고르세요. 나머지 둘은 카메라 없이 프로그램을 시험할 때 씁니다.")

        self.camera_index = QComboBox()
        self.camera_index.setMinimumWidth(220)
        # 카메라를 직접 고르면 실제 장비 모드로 (합성/파일 모드에 남아 고른 카메라가 안 나오는 일 방지)
        self.camera_index.activated.connect(
            lambda _i: self.source_mode.setCurrentIndex(self.source_mode.findData("device")))
        self._names: list[str] = []
        btn_rescan = QPushButton("다시 찾기")
        btn_rescan.setToolTip("카메라를 연결하거나 USB 스트리밍으로 바꾼 뒤 눌러 목록을 새로 고칩니다")
        btn_rescan.clicked.connect(self._fill_cameras)
        self.camera_hint = QLabel()
        self.camera_hint.setObjectName("SettingsWarn")
        self.camera_hint.setWordWrap(True)
        self.camera_hint.setVisible(False)
        cam_w = QWidget()
        cam_col = QVBoxLayout(cam_w)
        cam_col.setContentsMargins(0, 0, 0, 0)
        cam_col.addWidget(self._row(self.camera_index, btn_rescan))
        cam_col.addWidget(self.camera_hint)
        self._option(card, "카메라", cam_w,
                     "연결된 카메라 이름이 나옵니다 (★ = 소니 카메라). 새로 연결했는데 목록에 없으면 '다시 찾기'.")
        self._fill_cameras()
        # synthetic·file 에서 device 로 바꾸면 그때 카메라를 찾는다
        self.source_mode.currentIndexChanged.connect(
            lambda _i: self._fill_cameras() if self._source() == "device" and not self._names else None)

        self.test_file = QLineEdit(c.test_file_path)
        self.test_file.setPlaceholderText("재생할 동영상 파일 경로")
        b = QPushButton("파일 선택")
        b.clicked.connect(self._browse_test_file)
        self.row_test_file = self._option(card, "시험용 동영상 파일", self._row(self.test_file, b),
                                          "'동영상 파일 재생 (시험용)' 을 골랐을 때 카메라 대신 보여 줄 파일입니다.")
        self.source_mode.currentIndexChanged.connect(lambda _i: self._sync_source_rows())
        self._sync_source_rows()

        card = self._group(lay, "화면 크기 (해상도)")
        self.width = QSpinBox()
        self.width.setRange(320, 7680)
        self.width.setValue(c.capture_width)
        self.height = QSpinBox()
        self.height.setRange(240, 4320)
        self.height.setValue(c.capture_height)
        res = QWidget()
        rl = QHBoxLayout(res)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("가로"))
        rl.addWidget(self.width)
        rl.addWidget(QLabel("×  세로"))
        rl.addWidget(self.height)
        rl.addStretch(1)
        self._option(card, "가로 × 세로", res,
                     "카메라에서 받을 화면 크기(픽셀)입니다. 보통 1920 × 1080 (Full HD), 4K 카메라는 3840 × 2160. "
                     "카메라가 지원하는 크기를 고르세요. 촬영 모드에 해상도가 들어 있으면 모드를 고를 때 그 값으로 바뀝니다.")

        card = self._group(lay, "소니 카메라 연결 방법")
        card.addWidget(self._desc(
            "소니 ZV-E10 II · FX3: 카메라 MENU → 설정 → USB → USB 연결 모드 → 'USB 스트리밍' 으로 바꾼 뒤 USB-C 로 연결하면 "
            "위 카메라 목록에 ★ 표시로 나타납니다. 4K 로 쓰려면 해상도를 3840 × 2160 으로 하세요."))
        lay.addStretch(1)

    def _source(self) -> str:
        return self.source_mode.currentData() or "device"

    def _sync_source_rows(self) -> None:
        self.row_test_file.setVisible(self._source() == "file")

    def _browse_test_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "시험용 동영상 파일 선택", self.test_file.text() or "",
                                           "동영상 (*.mp4 *.avi *.mov *.mkv);;모든 파일 (*)")
        if f:
            self.test_file.setText(f)

    # ================= 촬영 모드 =================
    def _build_preset_page(self) -> None:
        lay = self._page("촬영 모드",
                         "자주 쓰는 촬영 설정(해상도 · 밝기 · 노출 · 화이트밸런스 · 자동 촬영 · 피킹 · 분할 칸 수)을 이름을 붙여 "
                         "모드로 저장해 두면, '촬영' 화면의 '지금 촬영 모드' 에서 골라 바로 바꿀 수 있습니다.  ★ = 지금 모드")
        card = self._group(lay, "모드 목록 · 편집")
        card.addWidget(self._build_preset_tab())

    def _build_preset_tab(self) -> QWidget:
        from app.capture import camera_props, presets
        c = self.config
        self._presets = [dict(p, camera_props=dict(p["camera_props"])) for p in presets.all_presets(c)]
        self._active = c.active_preset or self._presets[0]["name"]
        self._loading = False
        w = QWidget()
        root = QHBoxLayout(w)
        root.setContentsMargins(0, 0, 0, 0)
        # 왼쪽: 목록
        left = QVBoxLayout()
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(180)
        self.preset_list.setMinimumHeight(220)
        left.addWidget(self.preset_list, 1)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        self.btn_p_add = QPushButton("지금 설정으로 추가")
        self.btn_p_add.setToolTip("지금 쓰는 카메라 · 촬영 설정을 새 모드로 저장합니다")
        self.btn_p_copy = QPushButton("복제")
        self.btn_p_copy.setToolTip("고른 모드를 복사해 새 모드를 만듭니다")
        self.btn_p_del = QPushButton("삭제")
        self.btn_p_use = QPushButton("★ 지금 모드로")
        self.btn_p_use.setToolTip("고른 모드를 지금 쓰는 모드로 정합니다 ('확인' 때 바뀜)")
        for b in (self.btn_p_add, self.btn_p_copy):
            row1.addWidget(b)
        for b in (self.btn_p_use, self.btn_p_del):
            row2.addWidget(b)
        left.addLayout(row1)
        left.addLayout(row2)
        root.addLayout(left, 2)
        # 오른쪽: 편집
        right = QVBoxLayout()
        form = QFormLayout()
        self.p_name = QLineEdit()
        form.addRow("이름", self.p_name)
        self.p_w = QSpinBox()
        self.p_w.setRange(320, 7680)
        self.p_h = QSpinBox()
        self.p_h.setRange(240, 4320)
        form.addRow("해상도", self._row(self.p_w, QLabel("×"), self.p_h))
        self.p_props: dict = {}
        for prop in camera_props.PROPS:
            chk = QCheckBox("지정")
            chk.setToolTip("끄면 이 항목은 카메라를 건드리지 않습니다 (자동 노출 · 자동 WB 는 자동)")
            if prop.toggle:
                val = QCheckBox("켜기")
                val.setChecked(bool(prop.default))
                val.toggled.connect(lambda _on: self._preset_edited())
            else:
                val = QSpinBox()
                val.setRange(int(prop.lo), int(prop.hi))
                val.setSingleStep(int(prop.step) or 1)
                val.setValue(int(prop.default))
                val.valueChanged.connect(lambda _v: self._preset_edited())
            val.setEnabled(False)
            chk.toggled.connect(val.setEnabled)
            chk.toggled.connect(lambda _on: self._preset_edited())
            self.p_props[prop.key] = (chk, val)
            form.addRow(prop.label, self._row(val, chk))
        self.p_smart = QComboBox()
        for code, text in (("off", "끄기"), ("freeze", "자동 정지"), ("snapshot", "자동 저장")):
            self.p_smart.addItem(text, code)
        self.p_peaking = QCheckBox("포커스 피킹 켜기")
        self.p_split = QComboBox()
        for n in (2, 4, 6, 8, 16):
            self.p_split.addItem(f"{n}분할", n)
        form.addRow("Smart 자동 촬영", self.p_smart)
        form.addRow("피킹", self.p_peaking)
        form.addRow("분할 칸 수", self.p_split)
        right.addLayout(form)
        self.btn_p_preview = QPushButton("카메라에 바로 적용 (미리보기)")
        self.btn_p_preview.setToolTip("체크한 카메라 값을 지금 화면에 바로 적용해 봅니다 (취소하면 되돌림)")
        self.btn_p_preview.setEnabled("camera_props" in self._actions)
        right.addWidget(self.btn_p_preview)
        hint = self._desc("'지정' 을 끄면 그 항목은 카메라를 건드리지 않습니다(자동 노출 · 자동 WB 는 자동). "
                          "카메라마다 지원하는 항목 · 범위가 다릅니다. '미리보기' 로 바꾼 값은 '취소' 하면 되돌아갑니다.")
        right.addWidget(hint)
        right.addStretch(1)
        root.addLayout(right, 3)

        self.preset_list.currentRowChanged.connect(lambda r: self._load_preset(r))
        self.p_name.textEdited.connect(lambda _t: self._preset_edited(rename=True))
        for wdg in (self.p_w, self.p_h):
            wdg.valueChanged.connect(lambda _v: self._preset_edited())
        self.p_smart.currentIndexChanged.connect(lambda _i: self._preset_edited())
        self.p_peaking.toggled.connect(lambda _on: self._preset_edited())
        self.p_split.currentIndexChanged.connect(lambda _i: self._preset_edited())
        self.btn_p_add.clicked.connect(lambda: self._add_preset(copy=False))
        self.btn_p_copy.clicked.connect(lambda: self._add_preset(copy=True))
        self.btn_p_del.clicked.connect(lambda: self._del_preset())
        self.btn_p_use.clicked.connect(lambda: self._use_preset())
        self.btn_p_preview.clicked.connect(lambda: self._preview())
        self._refresh_preset_list(select=next((i for i, p in enumerate(self._presets)
                                                if p["name"] == self._active), 0))
        return w

    def _sync_preset_combo(self) -> None:
        if not hasattr(self, "preset_combo"):
            return
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for p in self._presets:
            self.preset_combo.addItem(p["name"])
        names = [p["name"] for p in self._presets]
        self.preset_combo.setCurrentIndex(names.index(self._active) if self._active in names else 0)
        self.preset_combo.blockSignals(False)

    def _on_preset_combo(self, idx: int) -> None:
        if 0 <= idx < len(getattr(self, "_presets", [])):
            self._active = self._presets[idx]["name"]
            self._refresh_preset_list(select=idx)

    def _refresh_preset_list(self, select: int = 0) -> None:
        self._sync_preset_combo()
        self.preset_list.blockSignals(True)
        self.preset_list.clear()
        for p in self._presets:
            QListWidgetItem(("★ " if p["name"] == self._active else "   ") + p["name"], self.preset_list)
        self.preset_list.blockSignals(False)
        self.preset_list.setCurrentRow(max(0, min(select, len(self._presets) - 1)))
        self._load_preset(self.preset_list.currentRow())
        self.btn_p_del.setEnabled(len(self._presets) > 1)

    def _load_preset(self, row: int) -> None:
        if not (0 <= row < len(self._presets)):
            return
        p = self._presets[row]
        self._loading = True
        self.p_name.setText(p["name"])
        self.p_w.setValue(int(p["capture_width"]))
        self.p_h.setValue(int(p["capture_height"]))
        for key, (chk, val) in self.p_props.items():
            v = p["camera_props"].get(key)
            chk.setChecked(v is not None)
            if v is not None:
                if isinstance(val, QCheckBox):
                    val.setChecked(bool(v))
                else:
                    val.setValue(int(round(v)))
        self.p_smart.setCurrentIndex(max(0, self.p_smart.findData(p["smart_mode"])))
        self.p_peaking.setChecked(bool(p["smart_peaking"]))
        self.p_split.setCurrentIndex(max(0, self.p_split.findData(int(p["split_count"]))))
        self._loading = False

    def _editor_props(self) -> dict:
        out = {}
        for k, (chk, val) in self.p_props.items():
            if chk.isChecked():
                out[k] = (1.0 if val.isChecked() else 0.0) if isinstance(val, QCheckBox) else float(val.value())
        return out

    def _preview(self) -> None:
        self.previewed = True
        self._actions["camera_props"](self._editor_props())

    def _preset_edited(self, rename: bool = False) -> None:
        if self._loading:
            return
        row = self.preset_list.currentRow()
        if not (0 <= row < len(self._presets)):
            return
        p = self._presets[row]
        old_name = p["name"]
        new_name = self.p_name.text().strip() or old_name
        if rename and any(q["name"] == new_name for i, q in enumerate(self._presets) if i != row):
            return                                   # 이름이 겹치면 그대로 둔다
        p.update(name=new_name, capture_width=self.p_w.value(), capture_height=self.p_h.value(),
                 camera_props=self._editor_props(), smart_mode=self.p_smart.currentData() or "off",
                 smart_peaking=self.p_peaking.isChecked(), split_count=int(self.p_split.currentData() or 4))
        if self._active == old_name:
            self._active = new_name
        it = self.preset_list.item(row)
        if it is not None:
            it.setText(("★ " if new_name == self._active else "   ") + new_name)

    def _unique_name(self, base: str) -> str:
        names = {p["name"] for p in self._presets}
        if base not in names:
            return base
        for i in range(2, 100):
            if f"{base} {i}" not in names:
                return f"{base} {i}"
        return base + " *"

    def _add_preset(self, copy: bool) -> None:
        from app.capture import presets
        row = self.preset_list.currentRow()
        if copy and 0 <= row < len(self._presets):
            src = self._presets[row]
            p = dict(src, camera_props=dict(src["camera_props"]), name=self._unique_name(src["name"] + " 복사"))
        else:
            p = presets.from_config(self.config, self._unique_name("새 모드"))
        self._presets.append(p)
        self._refresh_preset_list(select=len(self._presets) - 1)
        self.p_name.setFocus()
        self.p_name.selectAll()

    def _del_preset(self) -> None:
        row = self.preset_list.currentRow()
        if len(self._presets) <= 1 or not (0 <= row < len(self._presets)):
            return
        gone = self._presets.pop(row)
        if gone["name"] == self._active:
            self._active = self._presets[0]["name"]
        self._refresh_preset_list(select=max(0, row - 1))

    def _use_preset(self) -> None:
        row = self.preset_list.currentRow()
        if 0 <= row < len(self._presets):
            self._active = self._presets[row]["name"]
            self._refresh_preset_list(select=row)

    # ================= 단축키 · USB 버튼 =================
    def _build_keys_page(self) -> None:
        lay = self._page("단축키 · USB 버튼",
                         "키보드 단축키와, 슬릿램프에 달린 USB 버튼 · 좌우(OD · OS) 신호가 어떤 일을 할지 정합니다.")
        card = self._group(lay, "키보드 단축키")
        self.btn_shortcuts = self._quick("단축키 설정", "keyboard", "사진 캡처·녹화·안구 선택 등의 키를 바꿉니다", "shortcuts")
        self._option(card, "단축키", self.btn_shortcuts,
                     "사진 캡처 · 녹화 · 정지영상 · 눈(OD · OS) 고르기 등에 쓸 키보드 키를 바꿉니다 (따로 창이 열립니다).")
        card = self._group(lay, "슬릿램프 USB 버튼 · 좌우 신호")
        self.usb_tab = UsbButtonsTab(self.config, raw_input=getattr(self.parent(), "usb_raw", None))
        card.addWidget(self.usb_tab)
        lay.addStretch(1)

    # ================= 저장 · 삭제 =================
    def _build_storage_page(self) -> None:
        c = self.config
        lay = self._page("저장 · 삭제",
                         "찍은 사진 · 동영상을 어떤 형식으로, 어디에 저장할지, 그리고 프로그램을 끌 때 지울지 정합니다.")
        card = self._group(lay, "저장 형식")
        self.photo_format = QComboBox()
        for code, text in _PHOTO_FORMATS:
            self.photo_format.addItem(text, code)
        fmt = (c.photo_format or "png").lower()
        if self.photo_format.findData(fmt) < 0:
            self.photo_format.addItem(fmt.upper(), fmt)
        self.photo_format.setCurrentIndex(self.photo_format.findData(fmt))
        self._option(card, "사진 형식", self.photo_format,
                     "PNG 는 찍은 그대로(손실 없이) 저장해 진료 기록에 좋습니다. JPG 는 조금 압축해 파일이 훨씬 작아 "
                     "저장 공간 · 전송에 유리합니다.")
        self.video_quality = QComboBox()
        self.video_quality.addItem("일반 — 가볍고 파일 작음 (기본)", "normal")
        self.video_quality.addItem("고화질 — 더 선명, 파일 약 2배", "high")
        self.video_quality.setCurrentIndex(max(0, self.video_quality.findData(c.video_quality)))
        self.video_quality.setToolTip("두 모드 모두 실시간(30fps)으로 녹화됩니다. 4K 는 보통 진료실 PC 에서도 끊기지 않게\n"
                                      "가장 빠른 인코더 설정을 쓰고, 화질(압축 정도)만 다릅니다.")
        self._option(card, "동영상 화질", self.video_quality,
                     "두 가지 모두 끊김 없이 실시간(30fps)으로 녹화되고, 압축 정도만 다릅니다. "
                     "고화질은 더 선명하지만 파일이 약 2배로 커집니다.")

        card = self._group(lay, "저장 위치")
        media = QLabel(c.media_root or "")
        media.setObjectName("SettingsPath")
        media.setWordWrap(True)
        media.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        b_open = QPushButton("폴더 열기")
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(c.media_root or "")))
        self._option(card, "기본 저장 폴더", self._row(media, b_open),
                     "사진 · 동영상이 환자별 · 날짜별 폴더로 저장되는 곳입니다 (여기서는 보기만 — 바꿀 수 없음).")
        self.export = QLineEdit(c.export_root)
        btn_browse = QPushButton("폴더 선택")
        btn_browse.clicked.connect(self._browse)
        self._option(card, "함께 저장할 폴더 (export)", self._row(self.export, btn_browse),
                     "찍을 때마다 기본 저장 폴더와 함께 여기에도 한 부 더 복사합니다 (예: 네트워크 공유 폴더 · 다른 드라이브). "
                     "프로그램에서 사진을 지워도 이 폴더의 사본은 남습니다.")

        card = self._group(lay, "프로그램을 끌 때 사진 지우기")
        self.delete_on_exit = QCheckBox("프로그램을 끌 때 촬영 사진 · 동영상 지우기")
        self.delete_on_exit.setChecked(bool(getattr(c, "delete_on_exit", False)))
        self._option(card, "끌 때 지우기", self.delete_on_exit,
                     "켜 두면 프로그램을 끌 때(창 닫기 · 종료) 몇 장을 지울지 알려 주는 안내 창이 나옵니다. "
                     "'삭제하고 종료' 를 누르면 지운 뒤 끄고, '남기고 종료' 를 누르면 지우지 않고 끕니다. "
                     "'취소' 를 누르면 끄지 않고 돌아갑니다. 꺼 두면 안내 없이 그냥 끕니다.")
        self.delete_scope = QComboBox()
        for code, text in _EXIT_SCOPES:
            self.delete_scope.addItem(text, code)
        self.delete_scope.setCurrentIndex(max(0, self.delete_scope.findData(getattr(c, "delete_on_exit_scope", "all"))))
        self._option(card, "지울 범위", self.delete_scope,
                     "모든 환자 = 이 PC 에 저장된 모든 환자의 사진 · 동영상 · 오늘 = 오늘 날짜로 찍은 것 · "
                     "이번에 켠 뒤 = 프로그램을 켠 다음 찍은 것만 (그 전에 찍은 사진은 남김).",
                     warn="⚠ 지운 사진은 되돌릴 수 없습니다. 환자 정보 · 검사 소견 · 샘플 사진 · 참조 영상 라이브러리는 지우지 않고, "
                          "'함께 저장할 폴더 (export)' 의 사본과 외장 하드 백업도 남습니다 "
                          "(백업 '프로그램을 닫을 때 남은 백업 마무리' 가 켜져 있으면 백업을 마친 뒤에 지웁니다).")
        self.delete_on_exit.toggled.connect(self.delete_scope.setEnabled)
        self.delete_scope.setEnabled(self.delete_on_exit.isChecked())
        if not self._standard:                       # Explorer 판은 촬영 프로그램이 아니라 쓰지 않음
            card.parentWidget().hide()
            lay.itemAt(lay.indexOf(card.parentWidget()) - 1).widget().hide()
        lay.addStretch(1)

    # ================= 전송 =================
    def _build_send_page(self) -> None:
        lay = self._page("전송",
                         "촬영 화면 도크의 '전송' 버튼(또는 사진 오른쪽 클릭 → 전송)은 오른쪽 목록에서 고른 사진을 "
                         "아래 방법 · 형식으로 보냅니다. 보내도 이 PC 의 사진은 그대로 남습니다 ('전송(삭제)' 제외).")
        card = self._group(lay, "보내는 곳 · 파일 형식")
        card.addWidget(self._build_send_tab())
        lay.addStretch(1)

    def _build_send_tab(self) -> QWidget:
        from app.services import send_service as ss
        c = self.config
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.send_method = QComboBox()
        for code, text in ss.METHODS:
            self.send_method.addItem(text, code)
        i = self.send_method.findData(getattr(c, "send_method", "folder"))
        self.send_method.setCurrentIndex(i if i >= 0 else 0)
        form.addRow("전송 방법", self.send_method)
        self.method_hint = self._desc("")
        form.addRow("", self.method_hint)

        # 폴더
        self.send_folder = QLineEdit(getattr(c, "send_folder", "") or "")
        self.send_folder.setPlaceholderText("예) D:\\환자사진  ·  \\\\서버\\공유폴더")
        b = QPushButton("폴더 선택")
        b.clicked.connect(lambda: self._pick_dir(self.send_folder, "전송할 폴더 선택"))
        self.row_folder = self._row(self.send_folder, b)
        form.addRow("전송 폴더", self.row_folder)
        # 클라우드
        self.send_cloud = QComboBox()
        self.send_cloud.setEditable(True)
        for p in ss.cloud_candidates():
            self.send_cloud.addItem(str(p))
        self.send_cloud.setCurrentText(getattr(c, "send_cloud_folder", "") or
                                       (self.send_cloud.itemText(0) if self.send_cloud.count() else ""))
        self.send_cloud.lineEdit().setPlaceholderText("OneDrive · Google Drive · Dropbox · iCloud 폴더")
        b2 = QPushButton("폴더 선택")
        b2.clicked.connect(lambda: self._pick_dir(self.send_cloud.lineEdit(), "클라우드 동기화 폴더 선택"))
        self.row_cloud = self._row(self.send_cloud, b2)
        form.addRow("클라우드 폴더", self.row_cloud)
        self.cloud_hint = self._desc("PC 에 설치된 클라우드 앱(OneDrive · Google Drive · Dropbox · iCloud)의 폴더에 저장하면 "
                                     "그 앱이 인터넷으로 올려 줍니다." + ("" if self.send_cloud.count()
                                                                  else "  (이 PC 에서 찾은 클라우드 폴더가 없습니다)"))
        form.addRow("", self.cloud_hint)
        # PACS
        self.pacs_host = QLineEdit(getattr(c, "send_pacs_host", "") or "")
        self.pacs_host.setPlaceholderText("예) 192.168.0.10  ·  pacs.hospital.local")
        self.pacs_port = QSpinBox()
        self.pacs_port.setRange(1, 65535)
        self.pacs_port.setValue(int(getattr(c, "send_pacs_port", 104) or 104))
        self.pacs_ae = QLineEdit(getattr(c, "send_pacs_ae", "PACS") or "PACS")
        self.pacs_ae.setMaxLength(16)
        self.pacs_calling = QLineEdit(getattr(c, "send_pacs_calling_ae", "SLITLAMP") or "SLITLAMP")
        self.pacs_calling.setMaxLength(16)
        self.btn_pacs_test = QPushButton("연결 확인")
        self.btn_pacs_test.clicked.connect(lambda: self._test_pacs())
        self.pacs_status = QLabel("")
        self.pacs_status.setObjectName("Muted")
        self.row_pacs = []
        for label, widget in (("PACS 주소", self.pacs_host), ("포트", self.pacs_port),
                              ("PACS AE Title", self.pacs_ae), ("이 PC AE Title", self.pacs_calling),
                              ("", self._row(self.btn_pacs_test, self.pacs_status))):
            form.addRow(label, widget)
            self.row_pacs.append(widget)
        self.pacs_hint = self._desc("주소 · 포트 · AE Title 은 병원 PACS 담당자에게 받은 값을 넣고 '연결 확인' 으로 시험하세요.")
        form.addRow("", self.pacs_hint)
        self.row_pacs.append(self.pacs_hint)
        form.addRow(self._rule())

        # 형식
        fmt_box = QWidget()
        fl = QHBoxLayout(fmt_box)
        fl.setContentsMargins(0, 0, 0, 0)
        self.send_formats: dict = {}
        chosen = set(getattr(c, "send_formats", ["original"]) or [])
        for code, text in ss.FORMATS:
            cb = QCheckBox(text)
            cb.setChecked(code in chosen)
            self.send_formats[code] = cb
            fl.addWidget(cb)
        fl.addStretch(1)
        form.addRow("파일 형식", fmt_box)
        self.fmt_hint = self._desc("여러 개를 고르면 고른 형식마다 한 부씩 보냅니다. PDF 는 고른 사진을 환자 정보 · 소견란과 함께 "
                                   "한 부로 만듭니다. PACS 로는 DICOM 으로만 보냅니다.")
        form.addRow("", self.fmt_hint)
        lay.addLayout(form)
        self._form_send = form
        self.send_method.currentIndexChanged.connect(lambda _i: self._sync_send_rows())
        self._sync_send_rows()
        return w

    _METHOD_HINTS = {
        "folder": "이 PC 의 다른 폴더나 네트워크 공유 폴더(다른 PC · NAS)에 환자별로 복사합니다.",
        "cloud": "클라우드 앱이 동기화하는 폴더에 넣어 인터넷 저장소로 올립니다.",
        "pacs": "병원 영상 서버(PACS)로 DICOM 네트워크 전송합니다.",
    }

    def _pick_dir(self, edit, title: str) -> None:
        d = QFileDialog.getExistingDirectory(self, title, edit.text() or "")
        if d:
            edit.setText(d)

    def _sync_send_rows(self) -> None:
        m = self.send_method.currentData()
        form = self._form_send
        self.method_hint.setText(self._METHOD_HINTS.get(m, ""))
        form.setRowVisible(self.row_folder, m == "folder")
        form.setRowVisible(self.row_cloud, m == "cloud")
        form.setRowVisible(self.cloud_hint, m == "cloud")
        for w in self.row_pacs:
            form.setRowVisible(w, m == "pacs")
        for code, cb in self.send_formats.items():
            cb.setEnabled(m != "pacs" or code == "dicom")

    def _send_values(self) -> dict:
        return {
            "send_method": self.send_method.currentData() or "folder",
            "send_folder": self.send_folder.text().strip(),
            "send_cloud_folder": self.send_cloud.currentText().strip(),
            "send_formats": [k for k, cb in self.send_formats.items() if cb.isChecked()],
            "send_pacs_host": self.pacs_host.text().strip(),
            "send_pacs_port": int(self.pacs_port.value()),
            "send_pacs_ae": self.pacs_ae.text().strip() or "PACS",
            "send_pacs_calling_ae": self.pacs_calling.text().strip() or "SLITLAMP",
        }

    def _test_pacs(self) -> None:
        from types import SimpleNamespace

        from app.services import send_service as ss
        self.pacs_status.setText("확인 중…")
        self.repaint()
        ok, msg = ss.test_pacs(SimpleNamespace(**self._send_values()))
        self.pacs_status.setText(("✔ " if ok else "✖ ") + msg)

    # ================= 백업 =================
    def _build_backup_page(self) -> None:
        lay = self._page("백업", "외장 하드나 다른 드라이브에 사진 · 동영상 · 환자 기록을 복사해 두어, "
                               "PC 가 고장 나도 영상을 잃지 않게 합니다.")
        card = self._group(lay, "백업 위치 · 방법")
        self.backup_tab = BackupTab(self.config, manager=getattr(self.parent(), "backup", None))
        card.addWidget(self.backup_tab)
        lay.addStretch(1)

    # ================= 참조 · 샘플 =================
    def _build_reference_page(self) -> None:
        lay = self._page("참조 · 샘플",
                         "질환별 샘플 사진과 참조 영상(대표 사진)을 다룹니다. 촬영 화면 위쪽의 질환 아이콘을 누르면 "
                         "그 질환 사진이 지금 환자 사진과 함께 멀티 화면에 놓입니다.")
        card = self._group(lay, "참조 · 샘플 사진")
        # v1.20.3: 화면 아래 참조 줄을 없애 라이브러리 입구를 여기로 (등록 · 정보 수정 · 샘플 · 지금 환자 사진과 비교)
        self.btn_references = self._quick("참조 영상 라이브러리", "image",
                                          "참조 영상 라이브러리 — 등록 · 정보 수정 · 샘플 사진 · 지금 환자 사진과 나란히 비교\n"
                                          "(설정 창을 닫고 엽니다 — 여기서 바꾼 설정은 저장되지 않으니 먼저 '확인'을 누르세요)",
                                          "references")
        row = self._option(card, "참조 영상 라이브러리", self.btn_references,
                           "질환별 참조 사진을 등록 · 수정하고, 지금 환자 사진과 나란히 비교합니다.",
                           warn="누르면 설정 창을 닫고 라이브러리를 엽니다 — 이 창에서 바꾼 설정은 저장되지 않으니 "
                                "먼저 '확인' 을 누르세요.")
        # standard=False (Explorer 판): 참조 라이브러리는 메뉴에 따로 있음
        if not self._standard:
            self.btn_references.hide()
            row.hide()
        self.btn_samples = self._quick("샘플 사진 가져오기", "image",
                                       "받아 둔 질환별 샘플 사진 폴더를 골라 샘플 환자로 등록합니다", "samples")
        self._option(card, "샘플 사진", self.btn_samples,
                     "따로 받아 둔 질환별 샘플 사진 폴더를 골라 '샘플 · 질환명' 환자로 등록합니다. "
                     "설치판에 들어 있는 샘플 사진은 처음 실행할 때 저절로 등록됩니다.")
        lay.addStretch(1)

    # ================= AI 에이전트 =================
    def _build_agent_page(self) -> None:
        c = self.config
        lay = self._page("AI 에이전트",
                         "AI 도우미가 촬영 장면을 알아보고 자동 촬영 · 추천을 돕는 선택 기능입니다. 쓰지 않으면 꺼 두세요.")
        card = self._group(lay, "AI 도우미")
        self.agent = QCheckBox("AI 에이전트 켜기")
        self.agent.setChecked(bool(c.agent_enabled))
        self._option(card, "AI 에이전트", self.agent,
                     "장면 인식 · 촬영 체크리스트 · 장면이 안정되고 초점이 맞으면 자동 촬영 · 추천 프레임을 씁니다.")
        self.agent_clips = QCheckBox("촬영 전후 클립(mp4) 도 환자 영상으로 저장")
        self.agent_clips.setChecked(bool(c.agent_save_clips))
        self._option(card, "전후 클립", self.agent_clips,
                     "AI 가 사진을 찍을 때 그 앞뒤 몇 초 동영상도 함께 저장합니다 (저장 공간을 더 씁니다).")
        self.agent_logging = QCheckBox("사용 기록 수집 (익명)")
        self.agent_logging.setChecked(bool(c.agent_logging))
        self._option(card, "사용 기록", self.agent_logging,
                     "AI 를 더 낫게 만들기 위해 어떤 기능을 썼는지만 익명으로 기록합니다. 환자 정보는 기록하지 않습니다.")
        lay.addStretch(1)

    # ================= 컴퓨터 사양 =================
    def _build_specs_page(self) -> None:
        """컴퓨터 사양 — 이 PC 확인 + 최소·권장 사양 + 동영상 화질 모드 설명 (app/system_specs.py)."""
        lay = self._page("컴퓨터 사양", "이 PC 가 촬영 · 녹화에 충분한지 알려 줍니다. 보기만 하는 화면이라 바꿀 설정은 없습니다.")
        info = system_specs.detect(getattr(self.config, "media_root", None))
        self.specs_label = QLabel(system_specs.html(info))
        self.specs_label.setWordWrap(True)
        self.specs_label.setTextFormat(Qt.TextFormat.RichText)
        self.specs_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.specs_label.setContentsMargins(0, 6, 0, 8)
        lay.addWidget(self.specs_label)
        lay.addStretch(1)

    # ================= 바로 실행 버튼 =================
    def _quick(self, text: str, icon_name: str, tip: str, key: str) -> QPushButton:
        b = QPushButton(icons.gap(text))
        b.setIcon(icons.icon(icon_name, ICON_ACCENT, 18))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setMinimumHeight(36)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setEnabled(key in self._actions)
        b.clicked.connect(lambda _=False, k=key: self.run_action(k))
        return b

    def run_action(self, key: str) -> None:
        fn = self._actions.get(key)
        if fn is None:
            return
        if key == "camera":
            fn()
            self.quick_status.setText("카메라를 다시 연결했습니다.")
        elif key == "references":
            # 설정(모달)을 먼저 닫고, 창이 닫힌 다음에 라이브러리를 연다 (모달에 막혀 누를 수 없는 창이 되지 않게)
            self.reject()
            QTimer.singleShot(0, fn)
        else:
            fn(self)                          # 단축키 창 · 폴더 선택 창은 이 설정 창 위에 뜬다

    def _fill_cameras(self) -> None:
        """연결된 카메라를 이름으로 나열한다 (소니 ★). 저장한 카메라가 안 보이면 안내를 띄운다."""
        cfg = self.config
        current = self.camera_index.currentData()
        if current is None:
            current = cfg.camera_index
        names = device_names()
        probe = self._source() == "device"      # 번호로 열어 보기는 실제 장비 모드에서만
        devs = list(range(len(names))) if names else (
            enumerate_devices(backend=cfg.camera_backend) if probe else [])
        self._names = names
        self.camera_index.clear()
        # 이름을 알면 목록에서 고르기만, 모르면 번호를 직접 칠 수 있게
        self.camera_index.setEditable(not names)
        for d in devs:
            label = f"{d}: {'★ ' if is_sony(names[d]) else ''}{names[d]}" if d < len(names) else str(d)
            self.camera_index.addItem(label, d)
        if not devs:
            self.camera_index.addItem(str(current), current)
        if cfg.camera_name and cfg.camera_name in names:
            current = names.index(cfg.camera_name)
        i = self.camera_index.findData(current)
        if i >= 0:
            self.camera_index.setCurrentIndex(i)
        if cfg.camera_name and names and cfg.camera_name not in names:
            self.camera_hint.setText(
                f"저장된 카메라 '{cfg.camera_name}' 가 지금 연결되어 있지 않습니다. "
                "카메라를 켜고 USB 를 연결한 뒤 '다시 찾기' 를 누르거나, 위 목록에서 다른 카메라를 고르세요.")
            self.camera_hint.setVisible(True)
        elif not devs and probe:
            self.camera_hint.setText("연결된 카메라를 찾지 못했습니다. 카메라를 연결한 뒤 '다시 찾기' 를 누르세요.")
            self.camera_hint.setVisible(True)
        else:
            self.camera_hint.setVisible(False)

    def _preview_sound(self) -> None:
        was = sounds.is_enabled()
        sounds.set_enabled(True)
        try:
            sounds.play("shutter", volume=self.volume.value())
        finally:
            sounds.set_enabled(was)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.export.text() or "")
        if d:
            self.export.setText(d)

    # ================= 확인 · 저장 =================
    def accept(self) -> None:
        bad = self.usb_tab.problem()
        if bad:
            self.show_tab("단축키 · USB 버튼")
            QMessageBox.warning(self, "USB 버튼", bad)
            return
        bad = self.backup_tab.problem()
        if bad:
            self.show_tab("백업")
            QMessageBox.warning(self, "백업", bad)
            return
        super().accept()

    def apply_to_config(self):
        c = self.config
        c.source_mode = self._source()
        data = self.camera_index.currentData()
        if data is None:
            try:
                data = int(self.camera_index.currentText() or 0)
            except ValueError:
                data = 0
        c.camera_index = int(data)
        c.camera_name = self._names[c.camera_index] if c.camera_index < len(self._names) else ""
        c.capture_width = self.width.value()
        c.capture_height = self.height.value()
        c.photo_format = self.photo_format.currentData() or "png"
        c.video_quality = self.video_quality.currentData() or "normal"
        c.export_root = self.export.text().strip()
        c.test_file_path = self.test_file.text().strip()
        c.sounds_enabled = self.sounds.isChecked()
        c.sound_volume = int(self.volume.value())
        c.agent_enabled = self.agent.isChecked()
        c.agent_save_clips = self.agent_clips.isChecked()
        c.agent_logging = self.agent_logging.isChecked()
        c.theme = self.theme.currentData() or "dark"
        c.start_screen = "home" if self.start_home.isChecked() else "capture"
        c.smart_bar_visible = self.smart_visible.isChecked()
        c.view_after_capture = self.view_after.isChecked()
        c.capture_in_view = self.capture_in_view.currentData() or "reset"
        c.live_multi = self.live_multi.isChecked()
        c.right_click_action = self.right_click.currentData() or "menu"
        c.right_click_items = [k for k, cb in self.menu_items.items() if cb.isChecked()] or ["send", "multi", "delete"]
        c.thumbs_split_eyes = self.split_eyes.isChecked()
        c.split_count = int(self.split_count.currentData() or 4)
        c.split_count_saved = not self.split_ask.isChecked()
        if self._standard:
            c.delete_on_exit = self.delete_on_exit.isChecked()
            c.delete_on_exit_scope = self.delete_scope.currentData() or "all"
        for k, v in self._send_values().items():
            setattr(c, k, v)
        c.usb_buttons = self.usb_tab.values()
        c.usb_eye_auto = self.usb_tab.eye_auto()
        c.usb_device_check = self.usb_tab.device_check()
        for k, v in self.backup_tab.values().items():
            setattr(c, k, v)
        c.capture_presets = [dict(p, camera_props=dict(p["camera_props"])) for p in self._presets]
        if any(p["name"] == self._active for p in self._presets):
            c.active_preset = self._active
        c.save()
        return c
