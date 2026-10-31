"""v1.20.4 — 촬영한 사진을 확대한 뒤 왼쪽 버튼으로 끌면 사진이 움직인다 (기본 도구 '돋보기' 그대로).

맞춤 크기(창에 다 보임)에서는 예전처럼 누르는 동안 확대 렌즈, 휠로 확대해 사진이 창보다 커지면
왼쪽 버튼 끌기 = 사진 이동(손 모양 커서). 촬영 직후 큰 화면 · 썸네일 보기 창 · 멀티 화면 칸이 모두 같은 캔버스를 쓴다.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.image_viewer import ImageViewerDialog
from app.ui.multi_view import MultiView
from app.ui.pro.image_canvas import ImageCanvas

L = Qt.MouseButton.LeftButton
NO = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def _photo(tmp_path, name="p.jpg", w=640, h=480):
    img = np.zeros((h, w, 3), np.uint8)
    cv2.rectangle(img, (w // 4, h // 4), (3 * w // 4, 3 * h // 4), (40, 160, 220), -1)
    path = os.path.join(str(tmp_path), name)
    cv2.imwrite(path, img)
    return SimpleNamespace(id=name, file_path=path, thumbnail_path=None, media_type="photo", eye="OD",
                           captured_at="2026-10-31 10:00:00", note="")


def _scroll(cv: ImageCanvas):
    return cv.horizontalScrollBar().value(), cv.verticalScrollBar().value()


def _drag(cv: ImageCanvas, start: QPoint, end: QPoint, steps: int = 4):
    vp = cv.viewport()
    QTest.mousePress(vp, L, NO, start)
    for k in range(1, steps + 1):
        QTest.mouseMove(vp, start + (end - start) * k / steps)
    QTest.mouseRelease(vp, L, NO, end)


def _zoom_in(qapp, cv: ImageCanvas, times: int = 4):
    for _ in range(times):
        cv.zoom_by(1.25)
    _pump(qapp)
    assert cv.can_pan()


def test_after_capture_viewer_zoom_then_left_drag_moves_photo(qapp, tmp_path):
    """촬영 직후 큰 화면: 확대 → 왼쪽 클릭 끌기 = 사진 이동, 맞춤으로 돌아가면 다시 확대 렌즈."""
    dlg = ImageViewerDialog(_photo(tmp_path), None, after_capture=True)
    dlg.show()
    _pump(qapp)
    cv, vp = dlg.canvas, dlg.canvas.viewport()
    assert cv.tool() == "magnify" and dlg.tool_buttons["magnify"].isChecked()
    c = vp.rect().center()
    # 맞춤 크기: 옮길 곳이 없으니 누르는 동안 확대 렌즈 (예전 그대로)
    assert not cv.can_pan() and vp.cursor().shape() == Qt.CursorShape.CrossCursor
    QTest.mousePress(vp, L, NO, c)
    assert cv._lens is not None and cv._pan_from is None
    QTest.mouseRelease(vp, L, NO, c)
    assert cv._lens is None
    # 휠로 확대하면 손 모양 → 왼쪽 버튼으로 끌면 사진이 따라 움직인다
    _zoom_in(qapp, cv)
    assert vp.cursor().shape() == Qt.CursorShape.OpenHandCursor
    cv.horizontalScrollBar().setValue(cv.horizontalScrollBar().maximum() // 2)
    cv.verticalScrollBar().setValue(cv.verticalScrollBar().maximum() // 2)
    x0, y0 = _scroll(cv)
    vp_press = c
    QTest.mousePress(vp, L, NO, vp_press)
    assert cv._pan_from is not None and cv._lens is None                   # 렌즈 대신 이동
    assert vp.cursor().shape() == Qt.CursorShape.ClosedHandCursor
    QTest.mouseMove(vp, vp_press + QPoint(60, 40))
    QTest.mouseMove(vp, vp_press + QPoint(120, 80))
    x1, y1 = _scroll(cv)
    assert (x1, y1) == (x0 - 120, y0 - 80)                                 # 오른쪽 · 아래로 끌면 사진도 오른쪽 · 아래로
    QTest.mouseRelease(vp, L, NO, vp_press + QPoint(120, 80))
    assert cv._pan_from is None and vp.cursor().shape() == Qt.CursorShape.OpenHandCursor
    QTest.mouseMove(vp, vp_press)                                          # 놓은 뒤 움직임은 이동 아님
    assert _scroll(cv) == (x1, y1)
    _drag(cv, c, c + QPoint(-90, -50))                                     # 반대로 끌면 반대로
    assert _scroll(cv) == (x1 + 90, y1 + 50)
    assert not cv.has_annotations() and not cv.is_modified()               # 끌기는 보기만 바꾼다 (사진 · 주석 그대로)
    # '맞춤' → 다시 창에 다 보이고, 누르면 확대 렌즈
    dlg.b_fit.click()
    _pump(qapp)
    assert not cv.can_pan() and vp.cursor().shape() == Qt.CursorShape.CrossCursor
    QTest.mousePress(vp, L, NO, c)
    assert cv._lens is not None and cv._pan_from is None
    QTest.mouseRelease(vp, L, NO, c)
    dlg.close()


def test_thumbnail_viewer_and_pan_tool_also_drag(qapp, tmp_path):
    """썸네일을 눌러 연 보기 창도 같고, '이동' 도구도 확대한 사진을 끌어 옮긴다."""
    dlg = ImageViewerDialog(_photo(tmp_path), None)
    dlg.show()
    _pump(qapp)
    cv = dlg.canvas
    c = cv.viewport().rect().center()
    _zoom_in(qapp, cv)
    x0, y0 = _scroll(cv)
    _drag(cv, c, c + QPoint(-70, -30))
    assert _scroll(cv) == (x0 + 70, y0 + 30)
    dlg.tool_buttons["pan"].click()
    assert cv.tool() == "pan" and cv.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
    x0, y0 = _scroll(cv)
    _drag(cv, c, c + QPoint(-50, -20))
    x1, y1 = _scroll(cv)
    assert x1 > x0 and y1 > y0                                             # 이동 도구도 그대로 끌기
    dlg.close()


def test_missed_release_stops_pan(qapp, tmp_path):
    """창 밖에서 버튼을 놓아 놓음을 못 받아도, 버튼 없이 움직이면 이동이 끝난다 (사진이 저절로 따라오지 않게)."""
    cv = ImageCanvas()
    cv.resize(500, 400)
    cv.show()
    cv.set_image(cv2.imread(_photo(tmp_path).file_path))
    cv.set_tool("magnify")
    _pump(qapp)
    _zoom_in(qapp, cv)
    vp = cv.viewport()
    c = vp.rect().center()
    QTest.mousePress(vp, L, NO, c)
    assert cv._pan_from is not None
    before = _scroll(cv)
    p = QPointF(c + QPoint(80, 80))
    QApplication.sendEvent(vp, QMouseEvent(QEvent.Type.MouseMove, p, cv.mapToGlobal(p), Qt.MouseButton.NoButton,
                                           Qt.MouseButton.NoButton, NO))
    assert cv._pan_from is None and _scroll(cv) == before
    assert vp.cursor().shape() == Qt.CursorShape.OpenHandCursor
    cv.close()


def test_other_tools_and_live_frames(qapp, tmp_path):
    """확대해도 그리기 도구(화살표 등)는 그대로 그리고, 라이브처럼 새 프레임이 와도 끌던 이동이 이어진다."""
    cv = ImageCanvas()
    cv.resize(500, 400)
    cv.show()
    img = cv2.imread(_photo(tmp_path).file_path)
    cv.set_image(img)
    cv.set_tool("arrow")
    _pump(qapp)
    _zoom_in(qapp, cv)
    assert cv.viewport().cursor().shape() == Qt.CursorShape.CrossCursor
    c = cv.viewport().rect().center()
    before = _scroll(cv)
    _drag(cv, c, c + QPoint(40, 30))
    assert cv.annotation_kinds() == ["arrow"] and _scroll(cv) == before    # 화살표를 그리고 사진은 그대로
    cv.set_tool("magnify")
    assert cv.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
    vp = cv.viewport()
    cv.horizontalScrollBar().setValue(cv.horizontalScrollBar().maximum() // 2)
    cv.verticalScrollBar().setValue(cv.verticalScrollBar().maximum() // 2)
    x0, y0 = _scroll(cv)
    QTest.mousePress(vp, L, NO, c)
    QTest.mouseMove(vp, c + QPoint(20, 10))
    cv.set_image(img.copy(), keep_view=True, keep_annotations=True)        # 라이브 새 프레임
    QTest.mouseMove(vp, c + QPoint(40, 20))
    QTest.mouseRelease(vp, L, NO, c + QPoint(40, 20))
    assert _scroll(cv) == (x0 - 40, y0 - 20)
    cv.set_image(img.copy())                                               # 다른 사진 = 맞춤 · 이동 끝
    _pump(qapp)
    assert cv._pan_from is None and not cv.can_pan()
    cv.close()


def test_multi_view_cell_zoom_drag_and_double_click(qapp, tmp_path):
    """멀티 화면 칸: 확대한 칸만 끌어 옮기고, 두 번 누르면 예전처럼 큰 화면."""
    mv = MultiView()
    mv.resize(1000, 500)
    mv.show()
    mv.set_captures([_photo(tmp_path, "a.jpg"), _photo(tmp_path, "b.jpg")])
    _pump(qapp)
    cv0, cv1 = mv.canvases()
    assert mv.tool() == "magnify"
    _zoom_in(qapp, cv1)
    assert not cv0.can_pan()
    c = cv1.viewport().rect().center()
    x0, y0 = _scroll(cv1)
    _drag(cv1, c, c + QPoint(-40, -25))
    assert _scroll(cv1) == (x0 + 40, y0 + 25) and mv.active_canvas() is cv1
    opened = []
    mv.cellActivated.connect(opened.append)
    QTest.qWait(QApplication.doubleClickInterval() + 50)
    QTest.mouseDClick(cv1.viewport(), L, NO, c)
    assert [o.id for o in opened] == ["b.jpg"] and cv1._pan_from is None
    mv.close()
