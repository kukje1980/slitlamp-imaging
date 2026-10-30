#!/usr/bin/env bash
# 슬릿램프 영상 시스템 - macOS 앱(.app) + 설치 디스크 이미지(.dmg) 빌드
#   사용:  ./build_mac.sh            (기본)      → dist/SlitLampImaging.app + .dmg
#          ./build_mac.sh keeler     (Keeler 전용) → dist/KeelerSlitLamp.app + .dmg
#          ./build_mac.sh explorer   (Explorer 판) → dist/SlitLampExplorer.app + .dmg
#          ./build_mac.sh keeler_explorer (Keeler Explorer 판) → dist/KeelerExplorer.app + .dmg
#          ./build_mac.sh agent      (AI 에이전트 에디션) → dist/SlitLampAgent.app + .dmg
# 필요:   macOS 14 이상 (동봉 OpenCV·numpy 요구), Python 3.11 이상 (python.org 설치본 또는 Homebrew)
set -euo pipefail
cd "$(dirname "$0")"

if [ "$(uname)" != "Darwin" ]; then
  echo "[오류] 이 스크립트는 macOS 에서 실행해야 합니다." >&2
  exit 1
fi

# Python 3.11+ 찾기
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[오류] Python 3.11 이상이 필요합니다. https://www.python.org/downloads/macos/ 에서 설치하세요." >&2
  exit 1
fi
VARIANT="${1:-${SLITLAMP_BUILD_BRAND:-default}}"
if [ "$VARIANT" = "agent" ]; then
  export SLITLAMP_BUILD_BRAND="default" SLITLAMP_BUILD_EDITION="agent"
  APP="SlitLampAgent"; VOL="SlitLamp AI Agent"
else
  export SLITLAMP_BUILD_BRAND="$VARIANT" SLITLAMP_BUILD_EDITION="standard"
  case "$VARIANT" in
    keeler)   APP="KeelerSlitLamp";   VOL="Keeler SlitLamp" ;;
    explorer) APP="SlitLampExplorer"; VOL="SlitLamp Explorer" ;;
    keeler_explorer) APP="KeelerExplorer"; VOL="Keeler Explorer" ;;
    *)        APP="SlitLampImaging";  VOL="SlitLamp Imaging" ;;
  esac
fi
BRAND="$VARIANT"
VERSION="$("$PY" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
ARCH="$(uname -m)"

echo "===================================================="
echo " 슬릿램프 영상 시스템 - macOS 빌드  ($BRAND, v$VERSION, $ARCH, $("$PY" --version))"
echo "===================================================="

echo "[1/4] 빌드용 가상환경 + 의존성 설치..."
"$PY" -m venv .venv-build
# shellcheck disable=SC1091
source .venv-build/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt pyinstaller

echo "[2/4] PyInstaller 로 .app 번들 생성..."
rm -rf "build/build_mac" "dist/$APP" "dist/$APP.app"
pyinstaller --noconfirm build_mac.spec

echo "[3/4] 임시(ad-hoc) 서명..."
# 개발자 인증서가 없어도 Apple Silicon 에서 실행되도록 ad-hoc 서명
codesign --force --deep --sign - "dist/$APP.app"

echo "[4/4] 설치 디스크 이미지(.dmg) 생성..."
STAGE="dist/dmg"
DMG="dist/$APP-$VERSION-mac-$ARCH.dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "dist/$APP.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
# macOS 의 디스크 이미지 도구가 가끔 "Resource busy" 로 실패한다 → 잠시 쉬었다가 최대 5번 다시 시도
for try in 1 2 3 4 5; do
  if hdiutil create -volname "$VOL $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null; then break; fi
  if [ "$try" = 5 ]; then echo "dmg 생성 실패" >&2; exit 1; fi
  echo "  dmg 생성 재시도 ($try/5)..."; sleep $((try * 5))
done
rm -rf "$STAGE"

echo
echo "===================================================="
echo " 완료!"
echo "   앱:   dist/$APP.app"
echo "   설치: $DMG"
echo "   (dmg 를 열고 앱을 Applications 폴더로 끌어다 놓으세요)"
echo "===================================================="
