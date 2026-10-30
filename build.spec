# PyInstaller 스펙 (Windows, one-folder).
#   빌드:  pyinstaller build.spec                         → dist/SlitLampImaging/SlitLampImaging.exe
#   Keeler: set SLITLAMP_BUILD_BRAND=keeler && pyinstaller build.spec → dist/KeelerSlitLamp/KeelerSlitLamp.exe
#   Explorer 판: set SLITLAMP_BUILD_BRAND=explorer && pyinstaller build.spec → dist/SlitLampExplorer/SlitLampExplorer.exe
#   Keeler Explorer 판: set SLITLAMP_BUILD_BRAND=keeler_explorer && pyinstaller build.spec → dist/KeelerExplorer/KeelerExplorer.exe
#   AI 에이전트 에디션: set SLITLAMP_BUILD_EDITION=agent && pyinstaller build.spec → dist/SlitLampAgent/SlitLampAgent.exe
#
# imageio-ffmpeg 의 ffmpeg 바이너리와 PySide6 Qt 플러그인을 함께 수집한다.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

BRAND = os.environ.get("SLITLAMP_BUILD_BRAND", "default").lower()
EDITION = os.environ.get("SLITLAMP_BUILD_EDITION", "standard").lower()
AGENT = EDITION == "agent"
# 판별 실행파일 이름 · 아이콘 · 런타임 훅 (훅이 SLITLAMP_BRAND 를 정해 설치본이 항상 그 판으로 실행됨)
BRANDS = {
    "default": ("SlitLampImaging", "assets/icons/app.ico", []),
    "keeler": ("KeelerSlitLamp", "assets/brand/keeler/app.ico", ["tools/rthooks/brand_keeler.py"]),
    "explorer": ("SlitLampExplorer", "assets/brand/explorer/app.ico", ["tools/rthooks/brand_explorer.py"]),
    "keeler_explorer": ("KeelerExplorer", "assets/brand/keeler/app.ico", ["tools/rthooks/brand_keeler_explorer.py"]),
}
APP_NAME, APP_ICON, RTHOOKS = BRANDS.get(BRAND, BRANDS["default"])
RTHOOKS = list(RTHOOKS)
if AGENT:
    APP_NAME = "SlitLampAgent"
    RTHOOKS.append("tools/rthooks/edition_agent.py")

datas = []
binaries = []
hiddenimports = ["imageio_ffmpeg"]

# 번들 ffmpeg 바이너리 동봉 (mp4 H.264 보장)
binaries += collect_dynamic_libs("imageio_ffmpeg")
datas += collect_data_files("imageio_ffmpeg")
# Word 보고서(python-docx) 기본 서식 파일
datas += collect_data_files("docx")

# DICOM 내보내기 (pydicom 은 일부 모듈을 실행 중에 불러오므로 하위 모듈을 모두 포함, 테스트·예제 제외)
hiddenimports += collect_submodules("pydicom", filter=lambda n: ".tests" not in n and ".examples" not in n)
# PACS 전송 (pynetdicom — 전송 기능에서만 불러오므로 명시적으로 포함)
hiddenimports += collect_submodules("pynetdicom", filter=lambda n: ".tests" not in n and ".apps" not in n)

# DB 스키마 파일 동봉
datas += [("app/db/schema.sql", "app/db")]

# 앱 아이콘 동봉
datas += [("assets/icons/app.ico", "assets/icons"),
          ("assets/icons/app.png", "assets/icons")]
# 브랜드 로고·아이콘 (Keeler 등)
datas += [("assets/brand", "assets/brand")]
# 효과음 (캡처 찰칵 · 정지영상 · 녹화 삐)
datas += [("assets/sounds", "assets/sounds")]
datas += [("assets/samples", "assets/samples")]      # 질환별 샘플 사진 (원본 그대로 · 출처/이용 조건)
# AI 에이전트 장면 모델 (있을 때만 — 없으면 규칙 기반)
if os.path.isdir("assets/models") and os.listdir("assets/models"):
    datas += [("assets/models", "assets/models")]

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=RTHOOKS,
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=APP_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name=APP_NAME,
)
