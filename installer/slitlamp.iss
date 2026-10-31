; Inno Setup 스크립트 — 슬릿램프 영상 시스템 Windows 설치 마법사
;
; 사용 전제: 먼저 PyInstaller로 dist\SlitLampImaging\ 폴더가 생성되어 있어야 합니다.
;   pyinstaller --noconfirm build.spec
; 그 다음 Inno Setup 6의 ISCC.exe 로 이 파일을 컴파일하면 설치파일이 만들어집니다.
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\slitlamp.iss
; (repo 루트의 build_installer.bat 를 실행하면 위 과정을 한 번에 수행합니다.)

#define MyAppName "슬릿램프 영상 시스템"
#define MyAppNameEn "SlitLampImaging"
#define MyAppVersion "1.20.2"
#define MyAppPublisher "Kukje"
#define MyAppExeName "SlitLampImaging.exe"

[Setup]
; AppId 는 프로그램을 고유 식별하는 값입니다(업그레이드/제거에 사용). 변경하지 마세요.
AppId={{8F2A6C14-9B3D-4E7A-A1C5-2D6E9F0B1A33}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=SlitLampImaging-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 관리자 권한 없이 사용자 폴더에 설치(기본). 설치 시 모든 사용자/관리자 설치도 선택 가능.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 64비트 빌드(PySide6) 전용. 구버전 Inno Setup에서 오류 시 x64compatible -> x64 로 바꾸세요.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 설치 마법사/프로그램 아이콘
SetupIconFile=..\assets\icons\app.ico

[Languages]
; Inno Setup 기본 배포에 Korean.isl 이 포함되어 있어 한국어 마법사가 기본입니다.
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller one-folder 결과 전체를 설치 폴더로 복사
Source: "..\dist\{#MyAppNameEn}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
