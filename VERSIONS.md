# 슬릿램프 디지털 영상 시스템 — 버전 목록

각 버전은 아래 커밋 시점의 소스 전체(데스크탑 앱 + `web/` 웹 버전)입니다. "소스 다운로드" 로 해당 버전의 zip 을
바로 받을 수 있습니다(GitHub 로그인이 필요할 수 있음). 웹 버전만 필요하면 zip 안의 `web/` 폴더를 사용합니다.

| 버전 | 날짜 | 주요 변경 | 커밋 | 소스 다운로드 | 웹 버전 폴더 |
|---|---|---|---|---|---|
| v1.0 | 2026-10-24 | 초기 버전 — 캡처·녹화·정지영상·4분할·Pro 모드·영상 편집·Clinical Graphite 디자인·Keeler 브랜드·맥 지원·인터넷 배포 | `f3afbe0` | [zip](https://github.com/kukje1980/slitlamp-imaging/archive/f3afbe0f0ae4f1c0db753e75c5b8beb905b9e2c4.zip) | [web/](https://github.com/kukje1980/slitlamp-imaging/tree/f3afbe0f0ae4f1c0db753e75c5b8beb905b9e2c4/web) |
| v1.1 | 2026-10-24 | 촬영 저장 속도 개선, '이 PC에만 저장' 모드, Keeler 전용 데스크탑 버전(윈도우·맥) + 설치파일 자동 빌드 | `26a226a` | [zip](https://github.com/kukje1980/slitlamp-imaging/archive/26a226ab7d450a635ccf10f5f8e602d7894b3e07.zip) | [web/](https://github.com/kukje1980/slitlamp-imaging/tree/26a226ab7d450a635ccf10f5f8e602d7894b3e07/web) |
| v1.2 | 2026-10-24 | 웹 실시간 화면 확대 — 환자 정보 상단바 통합, 환자 목록(표), 전체 화면 | `b8d08f7` | [zip](https://github.com/kukje1980/slitlamp-imaging/archive/b8d08f7903868515e1dad0fde15456e46cd5b561.zip) | [web/](https://github.com/kukje1980/slitlamp-imaging/tree/b8d08f7903868515e1dad0fde15456e46cd5b561/web) |
| v1.3 | 2026-10-24 | 소니 카메라(ZV-E10 II · FX3) USB 스트리밍 연동 — 자동 인식·해상도 선택·연결 안내 | `150942c` | [zip](https://github.com/kukje1980/slitlamp-imaging/archive/150942ccfee5cd90ddd110b17d7880e6ac7c6654.zip) | [web/](https://github.com/kukje1980/slitlamp-imaging/tree/150942ccfee5cd90ddd110b17d7880e6ac7c6654/web) |
| v1.4 | 2026-10-24 | 단축키 사용자 지정 — 캡처·녹화·일시정지·정지영상·4분할·OD/OS/OU·전체 화면·모드·환자 | `1b93ab0` | [zip](https://github.com/kukje1980/slitlamp-imaging/archive/1b93ab00750a93704f7aed03aba2031c625225db.zip) | [web/](https://github.com/kukje1980/slitlamp-imaging/tree/1b93ab00750a93704f7aed03aba2031c625225db/web) |
| v1.5 | 2026-10-24 | 설정(톱니) 창으로 상단 정리 + 마이봄샘(meibography) 촬영·분석 모드 | `546db8d` | [zip](https://github.com/kukje1980/slitlamp-imaging/archive/546db8d7e65007686f5c2b1a2d0bd76dd388ce9b.zip) | [web/](https://github.com/kukje1980/slitlamp-imaging/tree/546db8d7e65007686f5c2b1a2d0bd76dd388ce9b/web) |
| v1.6 | 2026-10-24 | 전체 점검 버그 수정(웹·Pro·서버·데스크탑), 맥 dmg 생성 재시도, 녹화 길이 정확도 수정, 윈도우 설치파일 릴리스 자동 게시 | `f5c8cee` | [zip](https://github.com/kukje1980/slitlamp-imaging/archive/f5c8cee630621f4f9ecf100dc32545c27fcaa0fe.zip) | [web/](https://github.com/kukje1980/slitlamp-imaging/tree/f5c8cee630621f4f9ecf100dc32545c27fcaa0fe/web) |

- 설치파일(윈도우 Setup.exe · 맥 dmg · 웹 zip, 최신 배포판): https://github.com/kukje1980/slitlamp-imaging/releases/latest
- 터미널에서 특정 버전으로 되돌리기: `git checkout <커밋>` (예: `git checkout 150942c`)

## v1.7 — 2026-10-25
촬영 효과음 추가 (`bdcd9a7`).

## v1.8.0 통합판 — 2026-10-25
- 효과음 + DB 무결성 검사·일일 백업·쓰기 실패 롤백 + 녹화 종료·카메라 재연결 안정성 통합.
- 촬영 화면 Smart 도구줄(ROI·Best Shot·자동 정지/저장·피킹)과 Pro 스마트 탭을 모두 보존.
- Pro 분석 탭: LOCS III 수동 입력·수정체 혼탁 지수·경과 그래프.
- Pro 정밀 분석 탭: 형광염색·충혈·마이봄샘·수정체 참고 지표·TBUT·DICOM 내보내기.
- 웹은 웹에 구현된 촬영·편집·효과음·PC 저장 기능과 DB 안정성 변경을 포함합니다. 데스크톱 전용 분석 기능이 웹에 추가된 것은 아닙니다.
- Windows 기본/Keeler 설치파일과 macOS 기본/Keeler Apple Silicon/Intel 설치파일을 같은 커밋에서 빌드합니다.
- `v1.8.0` 릴리스에 설치파일, 웹 게시용 소스 ZIP, SHA256 체크섬을 모읍니다.

영상 분석 결과는 촬영 조건에 영향을 받는 참고 지표이며 실장비·실영상 검증은 별도입니다.

## v1.9.0 통합판 — 2026-10-25
v1.8.0 이후 `main` 에 들어온 변경을 모두 합친 최종판입니다 (`e1d66a6` 기준).
- 효과음: 사진 캡처·녹화 효과음을 실제 카메라 녹음으로 교체 (셔터, 녹화 시작/중지) — 데스크탑·웹 공통 ([#19](https://github.com/kukje1980/slitlamp-imaging/pull/19)).
- 웹 Pro **스마트** 탭: 초점 판정 · Best Shot · Smart Freeze / Auto Save · 포커스 피킹 · 실시간 화질 점검 ([#18](https://github.com/kukje1980/slitlamp-imaging/pull/18)).
- 웹 Pro **분석** 탭: 조명 태그 · 형광 염색 면적 · 충혈 지수 · 경과 비교(고스트·정렬·깜빡임) · 백내장 핵/피질 혼탁 지수 · LOCS III 입력 · 경과 그래프 · 측정 기록 API.
  (v1.8.0 설명의 "데스크톱 전용 분석 기능은 웹에 포함되지 않음" 은 이 버전부터 해당하지 않습니다. 단, TBUT·DICOM·마이봄샘 정밀 분석은 데스크탑 전용입니다.)
- 이후 `main` 에 병합될 때마다 같은 버전 릴리스의 설치파일이 다시 만들어지므로, 내용이 바뀌는 병합에는 버전을 올립니다.

## v1.9.1 — 2026-10-25
- 데스크탑 Pro 사이드 패널: 탭 6개(보정·필터·편집·스마트·분석·정밀 분석)가 한 줄에 들어가지 않아 **스마트·분석·정밀 분석 탭이 작은 ◀▶ 화살표 뒤로 숨던 문제** 수정 → 3×2 버튼으로 항상 모두 보임 (맥·윈도우 공통).

## v1.11.0 — 2026-10-25
- **AI 에이전트 에디션**(SlitLampAgent) 신설 — 실시간 장면 인식(7종), 눈·장면당 자동 촬영, 검사 프로토콜 체크리스트,
  촬영 전후 클립(mp4) 저장, 초점 상위 추천 프레임, 익명 사용 이벤트 로그(`agent_events`), 학습 데이터 내보내기.
- 학습 파이프라인 `tools/agent_train/` — 공개 데이터 → 로지스틱 회귀(JSON, 앱에서 numpy 추론), 병원 내보내기 데이터로 재학습,
  CNN(ONNX) 경로. 기본 에디션에서도 설정 → AI 에이전트 로 켤 수 있음.
- 설치파일: `SlitLampAgent-Setup-1.11.0.exe`, `SlitLampAgent-1.11.0-mac-arm64/x86_64.dmg` 추가 (기본·Keeler 는 그대로).

## v1.10.0 — 2026-10-25
- 웹 Pro: 편집·분석 탭 메뉴(보정·필터·편집·마이봄·분석·참조)를 **상단 도구줄**로 이동, **스마트**는 LIVE 바로 옆 (누르면 LIVE 로 전환) — 오른쪽 패널은 내용만.
- 웹 Pro **참조** 탭(새 기능): 부위(안검·결막·각막·전방·홍채·수정체·마이봄샘·기타)별 **정상/비정상(질환명)** 대표 영상을 참조 샘플로 저장하고,
  **현재 영상 ↔ 샘플**, **정상 샘플 ↔ 현재 영상(또는 비정상 샘플)** 을 비교 화면에 나란히 표시. 1화면 영상 또는 PC 영상 파일로 등록.
  서버 모드는 `ref_samples` 테이블 + `media/_reference`, '이 PC에만 저장' 모드는 브라우저 저장소(IndexedDB v3). API `/api/v1/refs`.
- 데스크탑 앱은 v1.9.1 과 같음.


## v1.12.0 — 2026-10-25
- 데스크탑 앱(맥·윈도우): **질환별 샘플 사진 가져오기** — 상단 **샘플** 버튼으로 받아 둔 샘플 사진 폴더(질환별 하위 폴더 + `metadata.json`, zip 도 가능)를 고르면
  질환마다 `샘플 · 질환명 (SAMPLE-NN)` 환자가 생기고 사진이 그 환자의 영상으로 바로 보임. 원본 그대로 복사, 원문 진단·출처·라이선스·저해상도·외상을 영상 코멘트에 기록,
  다시 가져오면 중복 건너뜀, `metadata.json` SHA-256 과 다른 파일 제외. 화면 없이 `--import-samples <폴더|zip>` 로도 실행.
- 샘플 사진 자체는 설치파일에 들어 있지 않음(대부분 CC BY-NC-ND). 수집 목록·라이선스: `docs/SAMPLE_PHOTOS.md`, `sample_images/external_outpatient_catalog.csv`.
- AI 에이전트 에디션(SlitLampAgent)에도 같은 버튼이 있음 (데이터는 각 에디션 폴더에 따로 저장). 웹 버전은 v1.11.0 과 같음.

## v1.12.1 — 2026-10-26
- 데스크탑 설정 창: 목록 칸(소스 모드·카메라·사진 형식)에 **▼ 화살표**, 숫자 칸에 ▲▼ 가 보이게 함 — 어두운 테마에서 화살표가 안 보여 글자 칸처럼 보이던 문제.
- 카메라는 이름 목록에서 **고르기만** 하도록 바꿈(글자 입력 칸 아님), **다시 찾기** 버튼으로 카메라를 연결한 뒤 목록 새로 고침.
  저장한 카메라(예: Sony Camera (Imaging Edge))가 안 보이면 설정 창에 바로 안내. 소스 모드가 synthetic·file 이어도 카메라 목록이 보임.

## v1.13.0 — 2026-10-26
- **아이콘 메뉴 인터페이스**(데스크탑·웹): 상단 홈 | 촬영 | Pro 검토 | 검사 기록 | 신규 환자 | 정보 수정 + 카메라·단축키·샘플·설정·테마. 촬영 도크도 아이콘 버튼, 사진 캡처는 원형 셔터.
- **홈(시작) 화면**: 큰 아이콘 타일로 바로 가기, "다음부터 촬영 화면으로 바로 시작" 옵션.
- **검사 기록 화면**: 촬영일별 영상 모음 + 검사 소견(서식·자동 저장) + 환자 정보, 인쇄/PDF 보고서에 소견 포함. 새 테이블 `exam_notes`, 웹 API `/api/v1/patients/{id}/exams`.
- **생년월일**: 달력 선택 또는 숫자 입력(19800315 · 800315 · 1980.3.15) 자동 정리.

## v1.14.0 — 2026-10-26
- **Explorer 판(SlitLampExplorer)** 추가 (데스크탑 윈도우·맥): 메뉴바 · 툴바 · 환자 › 검사 트리 · 썸네일 목록 화면.
  겹쳐보기(투명도·차이·바둑판·좌우 분할) · 2~4장 비교 · 참조 영상 라이브러리 · 슬라이드쇼 · Word(.docx)/PDF 보고서 ·
  카메라 속성(노출·게인·WB) · 영상 가져오기/내보내기 · DB 백업/복원. 새 테이블 `exams`, `ref_images` (자동 추가).
- 설치파일: `SlitLampExplorer-Setup-1.14.0.exe`, `SlitLampExplorer-1.14.0-mac-arm64/x86_64.dmg` 추가. 다른 판은 그대로, 웹은 v1.13.0 과 같음.

## v1.15.0 — 2026-10-27
- 기본 · Keeler · 에이전트 판 촬영 화면: 위쪽 · 도크 · 홈 버튼을 QPushButton 기반으로 다시 만들어 일부 PC 에서 클릭이 먹지 않던 문제 수정, 버튼 오류는 안내창 + 로그.
- 카메라 재연결 · 단축키 · 샘플 · 테마를 설정 창으로 이동(일반 · 카메라·저장 · 전송 · 촬영 모드 탭). 환자 검색 · 선택은 상단바로, 환자 정보 줄 삭제. 환자 목록 화면.
- 사진 목록 한 번 클릭 선택 · 두 번 클릭 크게 보기 · OD/OS 나눠 보기. **멀티**(모아 보기 · 저장 · 초기화), **분할** 2·4·6·8·16칸(저장),
  **전송**(폴더 · 클라우드 동기화 폴더 · PACS C-STORE / 원본 · JPG · PDF · DICOM), **촬영 모드**(카메라 · 촬영 설정 저장 · 바로 바꾸기).
- 도크 가운데 버튼 위치 고정, 일시정지 제거(정지영상으로 통합), OU · 동영상 분류 · 녹화 옆 실시간 안내 제거, Smart 줄 숨기기.
- 새 의존성 `pynetdicom` (PACS 전송). Explorer 판은 v1.14.0 과 같음.

## v1.15.1 — 2026-10-27
- Explorer 판: 가져온 **샘플 사진**이 참조 영상 라이브러리에 질환별로 보임(원본 그대로 · 출처·라이선스, 현재 영상과 비교),
  라이브러리 · 파일 메뉴 · 설정에서 샘플 사진 가져오기.
- Explorer 판: 촬영 막대에 **정지영상**(멈춤 ↔ 라이브)과 **분할 정지영상**(2·4·6·8·16칸, 칸별 + 합성 저장).
- Explorer 판: **영상 목록 세로 (오른쪽)** 배치 (보기 메뉴 · 칸 끌어 옮기기, 설정에 기억). 정지영상 단축키(X) 수정.
- **Keeler Explorer 판(KeelerExplorer)** 추가: Explorer 화면 + Keeler 로고·색·아이콘. 설치파일 `KeelerExplorer-Setup-1.15.1.exe`,
  `KeelerExplorer-1.15.1-mac-arm64/x86_64.dmg`.
- 다른 판·웹은 v1.15.0 과 같음.

## v1.16.0 — 2026-10-27
- 기본판 · Keeler 판 · AI 에이전트 판(윈도우·맥 앱): 위쪽 **참조 영상** 버튼 — 질환별 샘플 사진 · 등록한 참조 영상 보기,
  지금 환자 사진과 비교, 현재 영상 참조 등록, 샘플 사진 가져오기.
- Keeler 판 상단바: 테마와 상관없이 Keeler 네이비 + 흰 글자 로고 + 파랑 선, 셔터 주황 링.
- Explorer · Keeler Explorer 판: 참조 영상 창에 '현재 영상 참조 등록' 버튼. 웹은 v1.15.0 과 같음.

## v1.17.0 — 2026-10-27
- Keeler 판 · Keeler Explorer 판: 로고 K 4색(하늘 · 파랑 · 네이비 · 주황) 디자인 — 상단바 아래 4색 줄무늬, OD 하늘 · OS 주황,
  홈 타일 아이콘 · 띠 색, 'KEELER · SLIT LAMP IMAGING' 제목.
- 홈 화면 큰 사진 칸 (Keeler 판 기본: 슬릿 빛줄기 그림 → '홈 사진 바꾸기' 로 장비 사진). `assets/brand/keeler/home.jpg` 를 두면 기본 사진.
- 작은 화면 글자 · 아이콘 겹침 수정: 창 최소 폭 1455 → 약 1000px, 좁으면 상단 메뉴 · 촬영 도크 아이콘만, Smart 안내 접기, 긴 도구줄 옆으로 밀기.
- 촬영 도크: 분할 · 멀티 '저장' 을 정지영상 바로 옆으로 ('비우기' 는 녹화 오른쪽, 셔터 위치 고정).
- 분할: 처음 고른 칸 수를 저장해 다음부터 묻지 않고 바로 시작 (오른쪽 클릭 · 설정 · Explorer 카메라 메뉴로 바꾸기, '누를 때마다 묻기' 선택 가능).
- 참조 영상 비교 창 '화면 저장'(환자 기록) · '파일로 저장…', 라이브러리 참조 영상 '파일로 저장…'. Explorer '비교' 창도 화면 저장. 웹은 v1.15.0 과 같음.

## v1.17.1 — 2026-10-27
- v1.17.0 은 게시되지 않음 (Windows 테스트 기준 조정) — v1.17.1 이 v1.17.0 변경을 포함한 첫 배포판.
- Keeler 판 홈 화면: 배경을 떼어 낸 Keeler 슬릿램프 장비 사진(`assets/brand/keeler/home.png`, 원본 `home.jpg`)을 화면 바탕 위에,
  문구 없이. 흰 배경 사진은 '홈 사진 바꾸기' 때 배경을 자동으로 떼어 냄 (`app/services/photo_cutout.py`).

## v1.17.2 — 2026-10-27
- Keeler 판 홈 화면: Keeler 사진 3장(슬릿램프 · 슬릿램프 + 영상 시스템 · 접안렌즈, 배경 제거)을 '홈 사진 바꾸기' 에서 고름, 기본은 슬릿램프.
  장비 사진은 사진 칸 70% 지점(가운데보다 오른쪽)에 두어 균형. `tools/make_home_cutout.py` 로 다시 만들 수 있음.

## v1.18.0 — 2026-10-27
- 멀티 화면 = Pro 작업공간의 grid 모드(`ProWorkspace(embedded=True)`): 보정 · 필터 · 편집 · 스마트 · 분석 · 정밀 분석 메뉴가 누른 칸에 적용,
  기본 도구 돋보기, '모든 칸에 같이 적용', 편집본 저장은 편집한 칸마다. 멀티 버튼은 '촬영 이미지' 제목 줄(OD · OS 위).
- 참조 줄(`app/ui/ref_bar.py`): 도크 '참조' 로 열고 닫기, 고른 참조 영상 · 샘플 사진을 멀티 화면에 환자 사진과 같이 (참조 칸은 저장 · 기록에서 빠짐).
  상단바 '참조 영상' 버튼은 없앰. 상단바 높이 고정.
- 차트번호 중복 시 등록된 환자를 보여 주고 '기존 환자로 계속' / '차트번호 다시 입력'. 검사 기록: 사진 골라 선택 저장 · 인쇄 · PDF.
- 촬영 도크: [분할 · 저장 · 정지영상] 묶음 → 셔터 → 녹화 → 전송. 촬영 모드 고르기는 설정 → 일반으로. 웹은 v1.17.2 와 같음.
- 첫 빌드는 맥 테스트에서 멈춰 게시되지 않았음 (HScroll 이 만드는 도중 resizeEvent 로 on_compact 를 불러 검사 기록 화면이 열리지 않음) —
  `app/ui/fit.py` 에서 다 만든 뒤에만 부르게 고쳐 같은 v1.18.0 으로 다시 배포.

## v1.18.1 — 2026-10-27
- 슬릿램프 USB 버튼 2개(`app/usb_buttons.py`): Ctrl+Shift+Alt+F9 = 녹화 시작/정지, Ctrl+Shift+Alt+F10 = 스틸 정지/해제(사진 저장 안 함).
  누름 한 번에 한 번(자동 반복 · 뗌 없는 재누름 · 0.3초 안 겹침 무시), 촬영 화면에서만, 입력칸 · 설정 · 다른 프로그램 제외.
- 설정 → USB 버튼 탭(`app/ui/usb_buttons_tab.py`): 키 조합 · 기능 변경, 신호 확인, 단축키 충돌 검사. `config.usb_buttons`.
- 확인한 장치 VID 04D9 / PID 1203 (MI_00 키보드). 배포 시점에 실제 장치 시험은 사용자 확인 전. 웹은 v1.18.0 과 같음.

## v1.18.2 — 2026-10-27
- 멀티 화면(Pro 작업공간 embedded)에서 '영상 정보' 칸을 숨김 (Pro 검토 화면은 그대로).
- 멀티에서 참조 줄을 열면 창 최소 높이가 719px 로 늘어 1280×720(1920×1080 · 150%) 화면을 넘던 문제: 영상 · 분할 칸 최소 높이 360 → 270px,
  참조 줄 90 → 72px. 참조 줄을 열어도 약 610px. 웹은 v1.18.1 과 같음.

## v1.18.3 — 2026-10-27
- 창이 화면보다 넓어지던 문제: 코멘트가 긴 사진(샘플 사진 등)이 있으면 숨은 Pro 검토 MULTI 칸 글자가 창 최소 폭을 2007px 까지 늘림 →
  칸 글자를 첫 줄 40자로 줄이고 폭을 늘리지 않게(`app/ui/pro/multi_grid.py`), 같은 경우 996px.
- 멀티 '초기화' 버튼(촬영 도크) · 사진 목록 / 라이브 영상 오른쪽 클릭 = 멀티 · 상단바 '환자 목록' 버튼.
- 보기 창(두 번 클릭)을 Pro 캔버스로: 돋보기(누르고 있으면 확대) · 휠 확대 · 이동 · 맞춤 (`app/ui/image_viewer.py`).
- 분석 탭 '충혈 민감도' 1~10 (기본 5 = 예전 기준, `config.redness_sensitivity`): 혈관 화소 판정 기준만 바뀌고 충혈 지수 숫자는 그대로.
  웹은 v1.18.2 와 같음.

## v1.18.4 — 2026-10-28
- 멀티 화면(ProWorkspace embedded) 오른쪽 메뉴를 보정 · 편집 · 분석 셋으로: 편집 = 보기 → 도구 → 필터 → 회전 · 반전,
  편집 이력(실행 취소 · 다시 실행 · 원본 · 주석 지우기 · 편집본 저장)은 메뉴 아래 늘 보이는 줄(`edit_bar`). 스마트 탭 없음.
- 분석 = `app/ui/pro/multi_analysis.py` MultiAnalysisPanel(AnalysisPanel 상속): 측정 종류 하나(정밀 5종 + 형광 염색 면적 ·
  충혈 지수 · 백내장 핵 · 피질) → 측정 실행 → 코멘트에 기록 / 결과 영상 저장. 종류별로 필요한 칸만 표시.
- smart_panel: RednessSlider · locs_inputs 공용, 분석 탭 제목 줄 바꿈(폭 넘침 수정). Pro 검토 화면 탭은 그대로. 웹은 v1.18.3 과 같음.

## v1.18.5 — 2026-10-28
- 슬릿램프 좌우 신호(VID 04D9 / PID 1203 · MI_00): Ctrl+Shift+Alt+F6 → OD · F7 → OS (토글 아님, `ControlBar.set_eye` 공용), 버튼 3
  Ctrl+Shift+Alt+F8 → 사진 캡처. F9 녹화 · F10 스틸은 그대로 (`app/usb_buttons.py`).
- 눈 상태(미확인 · 자동 · 수동 · 확인 필요 · 정지 화면)를 촬영 도크 칩 · HUD 에 표시. 처음 · 재연결은 미확인, 비활성 복귀는 확인 필요,
  입력칸 · 다른 화면 · 다른 창의 신호는 보류 후 적용. 사진은 찍은 순간의 눈(정지 화면은 정지 순간), 녹화는 시작 눈 + 전환은 코멘트.
- `app/usb_raw_input.py`: Windows Raw Input 으로 감시 키의 장치 확인 · 재연결 알림 (실장비 미확인, 맥 · 실패 시 모두 받음).
  설정 → USB 버튼: `usb_eye_auto` · `usb_device_check`. 웹은 v1.18.4 와 같음.

## v1.19.0 — 2026-10-30
- 저장소를 `kukje1980/slitlamp-imaging` 으로 옮긴 뒤 첫 정식 배포판 (v1.18.5 이후 main 변경 전부 포함).
- 극한 상황 안전 수정(#45): 같은 순간 저장 덮어쓰기 금지(`reserve_unique`, 'x' 모드), 녹화 중 환자 전환 시 녹화 종료, 녹화 길이 monotonic,
  인코더 마무리 뒤에만 DB · 내보내기(Explorer 판 포함), 4K 대기 큐 약 800MB 제한, 긴 / 한글 / 장치 이름 폴더 처리, config BOM · 범위 검사,
  웹 차트번호 공백 · 검색 와일드카드 · 삭제 범위 · 카메라 끊김 · 새로고침 경고. `tests/test_extreme.py` · `web/tests/test_extreme.py` · `web/tests/e2e_extreme.js`.
- 동영상 화질 `video_quality` normal / high (#45): 앱 CRF 23 / 18 + 해상도별 preset(1080p veryfast · 4K ultrafast), 웹 비트레이트 고정.
- 설정 → 컴퓨터 사양 탭(#1, `app/system_specs.py`): 최소 · 권장 표, 화질 모드 설명, 이 PC 판정. 웹 설정 → 컴퓨터 사양 설명 창. README 표.
- 카메라 목록 파서 수정 · 설정 창 카메라 선택 (#28). macOS 최소 버전 14 명시 (`LSMinimumSystemVersion`).
- **질환별 샘플 사진 기본 포함**: `assets/samples/일반외래_슬릿램프_질환순` (26장 · 11개 질환 · metadata.json, 원본 그대로) 을 설치판에 넣고,
  처음 실행 때 한 번만 질환별 샘플 환자(SAMPLE-00 ~ 10)로 등록 (`ensure_bundled_samples`, 설정 `bundled_samples`).
  예전에 '샘플' 로 가져온 사진은 내용 해시로 건너뜀, 지운 샘플은 되살리지 않음. 출처 · 이용 조건: `assets/samples/출처_및_이용조건.txt`
  (24장 CC BY-NC-ND 3.0 → 비영리 내부 교육 · 참고용 배포 전제).
- 설정 → 동영상 화질 일반 / 고화질 (앱 H.264 CRF 23 / 18, 해상도에 맞춰 인코더 속도, 웹은 비트레이트 지정).

## v1.19.1 — 2026-10-30
- **선택 사진 삭제**: Pro 검토 툴바 '삭제' (Delete 키) — MULTI 에서 선택한 영상(최대 2장) / 1화면의 현재 영상.
  촬영 화면 썸네일 보기 창에도 '삭제' (Delete). 웹 Pro · 보기 창 동일 (참조 사진 제외).
  DB 기록 + 기본 저장 파일 + 썸네일을 지우고 지정 폴더(export) 사본은 백업으로 남김 (`CaptureService.delete_capture`).

## v1.19.2 — 2026-10-30
- **촬영일 단위 삭제**: 썸네일 촬영일 제목 줄의 쓰레기통 · 검사 기록 '날짜 삭제' · 웹 날짜 줄 쓰레기통 — 그 날 촬영한
  사진 · 동영상을 모두 삭제 (`CaptureService.captures_on` / `delete_day`). 검사 소견 · export 사본은 보존.

## v1.20.0 — 2026-10-30
- **최종 통합판**: 새 저장소 v1.19.0(안전 수정 · 동영상 화질 · 컴퓨터 사양 · 카메라 목록) + 옛 저장소 v1.19.0 ~ v1.19.2 · v1.20.0 을 하나로.
- **외장 하드 백업**(`app/services/backup_service.py`, 설정 → 백업 · 상태줄 백업 버튼), **효과음 크기**(`config.sound_volume`, 앱 · 웹),
  **촬영 목록 120장씩 그리기**(`ThumbnailPanel.PAGE`), **USB 버튼 진단 도구**(`tools/usb_buttons_probe.py`).
- 넣지 않음: 썸네일 오른쪽 클릭 메뉴(`photo-list-select-send`), 끊김 방지 대형 수정 3e96f16(v1.6 기준) — docs/RELEASE-1.20.0.md 참고.
- **상단 질환 아이콘 줄**(`app/ui/dx_bar.py`): 상단바 환자 검색 오른쪽 빈 공간에 샘플 질환(설치판 11개) + 등록한 참조 분류의 작은 아이콘.
  누르면 참조 줄을 열어 그 분류를 보이고(`RefBar.show_category`), 그 사진 최대 4장을 고르며(`choose_only_current`),
  고른 환자 사진이 없으면 가장 최근 사진을 골라 멀티 화면에 같이 놓는다. 다시 누르면 그 질환 사진을 뺌. 참조 사진을 다 빼면 아이콘도 꺼짐.
  창이 좁아 상단 메뉴가 아이콘만 남으면 '질환 ▾' 메뉴 버튼 하나로. 샘플 가져오기 · 참조 등록 · 라이브러리 창 닫힘 뒤 다시 만든다.
- **멀티 화면 오른쪽 메뉴(보정 · 편집 · 분석) 접기**: 가는 손잡이(`ProSideToggle`)로 펴고 접음, 기본 접힘 (`config.multi_side_visible`).
- **촬영 직후 큰 화면 보기**: 사진을 찍으면 보기 창(`ImageViewerDialog(after_capture=True)`)이 최대화로 뜸. '다시 촬영'(Enter · Esc) 으로 촬영 화면 복귀,
  촬영 단축키(기본 C)는 창을 닫고 바로 다시 찍음(`retakeRequested`), USB 버튼도 창을 닫고 바로 동작(`_usb_context`). 전체 화면에서는 뜨지 않음.
  설정 → 일반 → '촬영 후 보기' (`config.view_after_capture`, 기본 켬).
- 웹: 촬영 직후 큰 화면 보기만 같음 (`#viewer-retake`, 촬영 키 = 닫고 다시 찍기, 설정 '촬영 후 보기' `slitlamp-view-after`). 질환 아이콘 · 멀티 메뉴 접기는 앱 전용.

## v1.20.1 — 2026-10-31
- **백업 · 삭제 극한 테스트 안전 수정** (`tests/test_extreme_v120.py` 25개 · `web/tests/test_extreme_v120.py` 7개 · e2e 삭제 시나리오 2개).
- 백업 위치가 사진 · 참조 영상 폴더 안이면 실행 시점에도 거부 (`BackupManager._run` 의 `describe_target` 검사, `plan_tree` 는 backup_info.json 이
  있는 폴더를 건너뜀) — 자기 자신 복사로 두 배씩 커지던 것.
- 복사 도중 하드가 빠지면 내장 디스크에 같은 경로의 폴더를 다시 만들던 것 → `copy_file` · `_atomic_write` · `snapshot_db` 의 `anchor`
  (백업 폴더의 backup_info.json) 확인, `_mkdir_guarded` 가 만든 빈 폴더를 되돌리고 `DriveMissing`.
- 사진 폴더가 없으면 '백업 ✓' 대신 경고 (`res["source_missing"]`, dirty 유지).
- `CapturesDAO.delete` · `ExamsDAO.delete`: 다른 기록이 아직 가리키는 file_path · thumbnail_path 는 None 으로 돌려 지우지 않음.
- `ensure_bundled_samples`: 모든 예외를 잡아 시작을 막지 않음. 웹 촬영일 삭제 확인 창에 환자 이름.
