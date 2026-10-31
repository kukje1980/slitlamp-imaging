@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 사용:  build_installer.bat          기본 버전
rem        build_installer.bat keeler   Keeler 전용 버전
rem        build_installer.bat explorer Explorer 판 (메뉴·툴바·환자/검사 트리 화면)
rem        build_installer.bat keeler_explorer  Keeler Explorer 판 (Keeler 로고·색 + Explorer 화면)
set "BRAND=%~1"
if /i "%BRAND%"=="keeler" (
  set "SLITLAMP_BUILD_BRAND=keeler"
  set "ISS=installer\keeler.iss"
  set "OUTNAME=KeelerSlitLamp"
) else if /i "%BRAND%"=="keeler_explorer" (
  set "SLITLAMP_BUILD_BRAND=keeler_explorer"
  set "ISS=installer\keeler_explorer.iss"
  set "OUTNAME=KeelerExplorer"
) else if /i "%BRAND%"=="explorer" (
  set "SLITLAMP_BUILD_BRAND=explorer"
  set "ISS=installer\explorer.iss"
  set "OUTNAME=SlitLampExplorer"
) else (
  set "SLITLAMP_BUILD_BRAND=default"
  set "ISS=installer\slitlamp.iss"
  set "OUTNAME=SlitLampImaging"
)

echo ====================================================
echo  슬릿램프 영상 시스템 - Windows 설치파일 빌드 (!SLITLAMP_BUILD_BRAND!)
echo ====================================================
echo.

echo [1/3] 의존성 설치 (requirements + pyinstaller)...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 ( echo. & echo [오류] 의존성 설치 실패 & pause & exit /b 1 )
echo.

echo [2/3] PyInstaller 로 실행파일 번들 생성...
pyinstaller --noconfirm build.spec
if errorlevel 1 ( echo. & echo [오류] PyInstaller 빌드 실패 & pause & exit /b 1 )
echo.

echo [3/3] Inno Setup 으로 설치파일 생성...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "!ISCC!" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "!ISCC!" (
  echo.
  echo [오류] Inno Setup 6 이 설치되어 있지 않습니다.
  echo        https://jrsoftware.org/isdl.php 에서 설치한 뒤 다시 실행하세요.
  pause & exit /b 1
)
"!ISCC!" "!ISS!"
if errorlevel 1 ( echo. & echo [오류] 설치파일 생성 실패 & pause & exit /b 1 )

echo.
echo ====================================================
echo  완료! 설치파일 위치:
echo    dist\installer\!OUTNAME!-Setup-1.20.4.exe
echo ====================================================
pause
