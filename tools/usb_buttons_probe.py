#!/usr/bin/env python3
"""슬릿램프 USB 버튼 보드 진단 도구 — 버튼을 누르면 PC 에 어떤 신호가 오는지 찾아낸다.

사용법 (Windows 명령 프롬프트 / 맥 터미널, 저장소 폴더에서):
    pip install hidapi pyserial
    python tools/usb_buttons_probe.py            # 1) 장치 목록 → 2) 버튼을 누르며 신호 감시 (Ctrl+C 로 종료)
    python tools/usb_buttons_probe.py --diff     # USB 를 뽑았다 꽂으면서 '새로 나타난 장치' 만 찾기
    python tools/usb_buttons_probe.py --baud 9600   # 시리얼(COM) 장치의 속도를 바꿔 감시

무엇을 보는가
  • HID 장치: 키보드가 아닌 '사용자 정의 HID'(게임패드·버튼박스처럼) 로 잡히면 메모장엔 아무것도 안 찍히지만
    여기서는 보고서(report) 바이트가 바뀌는 것이 보인다 → 바뀐 바이트·비트 번호가 곧 버튼 번호.
  • 시리얼(COM) 장치: 버튼을 누를 때 문자·바이트가 오면 그대로 보인다.
  • 아무것도 안 보이면: 보드에 전원이 안 들어왔거나(슬릿램프 전원 ON 필요), 전용 드라이버가 필요한 장치.

출력 전체를 복사해 보내 주면 프로그램에 버튼을 연결할 수 있다.
"""
from __future__ import annotations

import sys
import time

try:
    import hid                                  # pip install hidapi
except ImportError:
    hid = None
try:
    import serial                               # pip install pyserial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

KNOWN_VENDORS = {0x054C: "Sony", 0x046D: "Logitech", 0x045E: "Microsoft", 0x8087: "Intel", 0x0BDA: "Realtek",
                 0x2109: "VIA Labs (USB 허브)", 0x0424: "Microchip/SMSC (USB 허브)", 0x05E3: "Genesys (USB 허브)",
                 0x0451: "TI (USB 허브)", 0x04D8: "Microchip (MCU)", 0x16C0: "VOTI/오픈 MCU", 0x2341: "Arduino",
                 0x1A86: "QinHeng CH340 (시리얼)", 0x0403: "FTDI (시리얼)", 0x10C4: "Silicon Labs (시리얼)",
                 0x1D6B: "Linux Foundation"}


def vendor_name(vid: int) -> str:
    return KNOWN_VENDORS.get(vid, "")


def hid_kind(d: dict) -> str:
    up, u = d.get("usage_page", 0), d.get("usage", 0)
    if up == 1 and u == 6:
        return "키보드"
    if up == 1 and u == 2:
        return "마우스"
    if up == 1 and u in (4, 5):
        return "게임패드/조이스틱"
    if up == 0x0C:
        return "소비자 제어(미디어 키)"
    if up >= 0xFF00:
        return "제조사 정의(Vendor) ← 버튼 보드일 가능성 높음"
    return f"usage_page=0x{up:04X} usage=0x{u:02X}"


def list_hid() -> list[dict]:
    if hid is None:
        print("  (hidapi 가 없습니다: pip install hidapi)")
        return []
    devs = hid.enumerate()
    seen, out = set(), []
    for d in sorted(devs, key=lambda x: (x["vendor_id"], x["product_id"], x.get("interface_number", 0))):
        key = (d["vendor_id"], d["product_id"], d.get("interface_number", 0), d.get("usage_page", 0), d.get("usage", 0))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
        print(f"  VID:PID {d['vendor_id']:04X}:{d['product_id']:04X}  {vendor_name(d['vendor_id']):22s} "
              f"{(d.get('manufacturer_string') or '-')[:22]:22s} {(d.get('product_string') or '-')[:30]:30s} "
              f"if={d.get('interface_number', -1)}  {hid_kind(d)}")
    if not out:
        print("  (HID 장치 없음)")
    return out


def list_serial() -> list:
    if list_ports is None:
        print("  (pyserial 이 없습니다: pip install pyserial)")
        return []
    ports = list(list_ports.comports())
    for p in ports:
        vid = f"{p.vid:04X}:{p.pid:04X}" if p.vid else "-"
        print(f"  {p.device:10s} VID:PID {vid:10s} {vendor_name(p.vid or 0):22s} {p.description}")
    if not ports:
        print("  (시리얼 포트 없음)")
    return ports


def snapshot() -> set:
    s = set()
    if hid is not None:
        for d in hid.enumerate():
            s.add(("hid", d["vendor_id"], d["product_id"], d.get("interface_number", 0), d.get("product_string")))
    if list_ports is not None:
        for p in list_ports.comports():
            s.add(("serial", p.vid or 0, p.pid or 0, p.device, p.description))
    return s


def diff_mode() -> None:
    print("1) 슬릿램프 USB 를 뽑은 상태에서 Enter 를 누르세요.")
    input()
    before = snapshot()
    print("2) 슬릿램프 전원을 켜고 USB 를 꽂은 뒤 5초 기다렸다가 Enter 를 누르세요.")
    input()
    after = snapshot()
    new = after - before
    print("\n새로 나타난 장치:")
    if not new:
        print("  (없음) → 보드에 전원이 안 들어왔거나, PC 가 장치를 인식하지 못했습니다.\n"
              "  Windows 장치 관리자(devmgmt.msc) 에서 '알 수 없는 장치' 나 느낌표(!) 가 있는지 확인하세요.")
    for n in sorted(new, key=str):
        print("  ", n)


def watch(baud: int, seconds: float | None) -> None:
    handles = []
    if hid is not None:
        for d in hid.enumerate():
            if hid_kind(d) in ("키보드", "마우스"):
                continue                        # OS 가 독점하므로 열리지 않음 (버튼 보드가 키보드라면 메모장에 찍혔을 것)
            try:
                h = hid.device()
                h.open_path(d["path"])
                h.set_nonblocking(True)
                handles.append(("hid", f"{d['vendor_id']:04X}:{d['product_id']:04X} if={d.get('interface_number', -1)} "
                                f"{d.get('product_string') or ''}", h))
            except Exception as e:  # noqa: BLE001
                print(f"  HID {d['vendor_id']:04X}:{d['product_id']:04X} 열기 실패: {e}")
    if serial is not None and list_ports is not None:
        for p in list_ports.comports():
            try:
                s = serial.Serial(p.device, baud, timeout=0)
                handles.append(("serial", f"{p.device} ({p.description})", s))
            except Exception as e:  # noqa: BLE001
                print(f"  {p.device} 열기 실패: {e}")
    if not handles:
        print("감시할 장치가 없습니다.")
        return
    print(f"\n감시 중인 장치 {len(handles)}개:")
    for kind, name, _ in handles:
        print(f"  [{kind}] {name}")
    print("\n이제 슬릿램프의 버튼을 하나씩 눌렀다 떼세요. (종료: Ctrl+C)\n")
    last: dict[int, bytes] = {}
    hits: dict[str, int] = {}
    t0 = time.time()
    try:
        while seconds is None or time.time() - t0 < seconds:
            for i, (kind, name, h) in enumerate(handles):
                try:
                    data = bytes(h.read(64)) if kind == "hid" else h.read(256)
                except Exception as e:  # noqa: BLE001
                    print(f"  [{name}] 읽기 오류: {e}")
                    continue
                if not data:
                    continue
                stamp = f"{time.time() - t0:7.2f}s"
                if kind == "serial":
                    print(f"{stamp} [시리얼 {name}] {data!r}  hex={data.hex(' ')}")
                    hits[name] = hits.get(name, 0) + 1
                    continue
                prev = last.get(i)
                last[i] = data
                if prev == data:
                    continue
                changed = []
                if prev is not None and len(prev) == len(data):
                    for b, (x, y) in enumerate(zip(prev, data)):
                        for bit in range(8):
                            was, now = (x >> bit) & 1, (y >> bit) & 1
                            if was != now:
                                changed.append(f"바이트{b} 비트{bit} {'눌림' if now else '뗌'}")
                print(f"{stamp} [HID {name}] hex={data.hex(' ')}" + (f"   → {', '.join(changed)}" if changed else ""))
                hits[name] = hits.get(name, 0) + 1
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        for _, _, h in handles:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
    print("\n요약: 신호가 온 장치")
    if not hits:
        print("  (없음) — 버튼이 이 PC 로 신호를 보내지 않았습니다. 전원·케이블·장치 관리자를 확인하세요.")
    for name, n in hits.items():
        print(f"  {name}: {n}회")


def main(argv: list[str]) -> int:
    baud = 115200
    seconds = None
    if "--baud" in argv:
        baud = int(argv[argv.index("--baud") + 1])
    if "--seconds" in argv:
        seconds = float(argv[argv.index("--seconds") + 1])
    print(f"Python {sys.version.split()[0]} · hidapi {'있음' if hid else '없음'} · pyserial {'있음' if serial else '없음'}\n")
    if "--diff" in argv:
        diff_mode()
        return 0
    print("== HID 장치 ==")
    list_hid()
    print("\n== 시리얼(COM) 포트 ==")
    list_serial()
    if "--list" in argv:
        return 0
    watch(baud, seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
