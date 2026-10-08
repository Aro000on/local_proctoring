import json
import os
import time
from datetime import datetime
from pathlib import Path

import cv2
from PyQt6.QtCore import QEvent, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from core import EventBus, Session, URLPolicy, utc_now
from cv_engine import CVEngine
from dns_proxy import LocalServer, make_interceptor
from security_engine import SecurityEngine, monitor_inventory


class ExamPage(QWebEnginePage):
    def __init__(self, profile, parent, policy, bus, blocked_url):
        super().__init__(profile, parent)
        self.policy, self.bus, self.blocked_url = policy, bus, blocked_url
        self.featurePermissionRequested.connect(self.deny_permission)

    def deny_permission(self, origin, feature):
        self.setFeaturePermission(
            origin, feature,
            QWebEnginePage.PermissionPolicy.PermissionDeniedByUser,
        )

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.toString() == "about:blank" or self.policy.allows(url.toString()):
            return True
        self.bus.emit("URL_BLOCKED", {"host": url.host(), "source": "navigation"})
        if is_main_frame:
            QTimer.singleShot(0, lambda: self.setUrl(QUrl(self.blocked_url)))
        return False

    def createWindow(self, window_type):
        self.bus.emit("URL_BLOCKED", {"source": "new_window"})
        return None

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        return []  


class ProctoringWindow(QMainWindow):
    EVENT_LABELS = {
        "HOTKEY_BLOCKED":         "Заблокирована горячая клавиша",
        "CLIPBOARD_CLEARED":      "Буфер обмена очищен",
        "URL_BLOCKED":            "Заблокирован переход по URL",
        "FOCUS_LOST":             "Потеря фокуса окна",
        "MULTIPLE_MONITORS":      "Обнаружено несколько мониторов",
        "SYSTEM_WINDOW_DETECTED": "Открыт Проводник/Диспетчер",
        "WINDOW_MINIMIZE_BLOCKED": "Попытка свернуть окно",
        "WINDOW_CLOSE_BLOCKED":   "Попытка закрыть окно",
        "LOOK_AWAY":              "Взгляд отведён от экрана",
        "NO_FACE":                "Лицо не обнаружено",
        "MULTIPLE_FACES":         "Несколько лиц в кадре",
        "PHONE_DETECTED":         "Обнаружен телефон",
        "MULTIPLE_PEOPLE":        "В кадре несколько человек",
        "SECURE_SHORTCUT_ATTEMPT": "Системный шорткат",
        "CALIBRATED":             "Калибровка завершена",
        "MODEL_READY":            "Модель YOLO готова",
        "SESSION_STARTED":        "Тест начат",
        "SESSION_FINISHED":       "Тест завершён",
        "ENGINE_ERROR":           "Ошибка движка",
        "EMERGENCY_EXIT":         "Аварийный выход",
    }

    def __init__(self, config, root):
        super().__init__()
        self.root, self.config = Path(root), config
        self.bus = EventBus()
        self.session = None
        self.finishing = False
        self.finished = False
        self.closed = False
        self.last_frame_ok = False
        self.cv = None
        self.server = LocalServer(config["local_port"])
        self.security = SecurityEngine(self.bus, self)

        self.setWindowTitle("Экзамен")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, False)
        self.keepalive_timer = QTimer(self)
        self.keepalive_timer.setInterval(500)
        self.keepalive_timer.timeout.connect(self.enforce_fullscreen)

        monitors = monitor_inventory()
        if len(monitors) != 1:
            raise RuntimeError(f"Отключите дополнительные экраны. Обнаружено: {len(monitors)}")
        try:
            self.server.start()
            self.cv = CVEngine(self.bus, self.root / config["yolo_weights"], config["camera_index"])
            self.build_ui()
        except Exception:
            if self.cv:
                self.cv.close()
            self.server.stop()
            raise
        self.timer = QTimer(self)
        self.timer.setInterval(50)  
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.keepalive_timer.start()
        QApplication.instance().applicationStateChanged.connect(self.application_state)

    def build_ui(self):
        self.surface = QWidget()
        self.test_panel = QFrame(self.surface)
        self.test_panel.setObjectName("testPanel")
        exam_layout = QVBoxLayout(self.test_panel)
        exam_layout.setContentsMargins(0, 0, 0, 0)

        self.setup_bar = QWidget()
        setup_layout = QHBoxLayout(self.setup_bar)
        setup_layout.setContentsMargins(12, 12, 12, 6)
        self.address = QLineEdit(self.config["test_url"])
        self.address.setPlaceholderText("Адрес сайта экзамена: https://exam.zxteam.org/…")
        self.address.setClearButtonEnabled(True)
        self.address.textChanged.connect(self.refresh_start)
        setup_layout.addWidget(self.address)
        exam_layout.addWidget(self.setup_bar)

        self.web = QWebEngineView()
        self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.web.setAcceptDrops(False)

        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)

        local_origin = f"http://127.0.0.1:{self.server.port}"
        self.blocked_url = local_origin + "/blocked"
        self.policy = URLPolicy(self.config["allowlist"] + [local_origin])
        if self.config["test_url"] and not self.policy.allows(self.config["test_url"]):
            raise ValueError("test_url отсутствует в allowlist. Проверьте allowlist в config.json")

        self.interceptor = make_interceptor(self.policy, self.bus, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.downloadRequested.connect(lambda item: item.cancel())

        self.page = ExamPage(self.profile, self.web, self.policy, self.bus, self.blocked_url)
        self.web.setPage(self.page)

        settings = self.page.settings()
        for attribute in [
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
            QWebEngineSettings.WebAttribute.PluginsEnabled,
        ]:
            settings.setAttribute(attribute, False)

        self.web.setUrl(QUrl(local_origin + "/ready"))
        exam_layout.addWidget(self.web, 1)
        self.float_panel = QFrame(self.surface)
        self.float_panel.setObjectName("floatingPanel")
        floating_layout = QVBoxLayout(self.float_panel)
        floating_layout.setContentsMargins(16, 16, 16, 16)
        floating_layout.setSpacing(10)

        self.title = QLabel("Подготовка к тесту")
        self.title.setObjectName("title")
        floating_layout.addWidget(self.title)

        self.video = QLabel("Камера")
        self.video.setObjectName("camera")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumHeight(100)
        floating_layout.addWidget(self.video)
        self.event_log = QListWidget()
        self.event_log.setObjectName("eventLog")
        self.event_log.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.event_log.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.event_log.setMinimumHeight(120)
        self.event_log.setMaximumHeight(180)
        self.event_log.setWordWrap(True)
        floating_layout.addWidget(self.event_log)

        self.setup_hint = QLabel("Посмотрите в камеру и укажите адрес теста.")
        self.setup_hint.setObjectName("hint")
        self.setup_hint.setWordWrap(True)
        floating_layout.addWidget(self.setup_hint)

        self.start_button = QPushButton("Начать тест")
        self.start_button.setObjectName("primary")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_test)
        floating_layout.addWidget(self.start_button)

        self.return_button = QPushButton("К тесту")
        self.return_button.setEnabled(False)
        self.return_button.hide()
        self.return_button.clicked.connect(lambda: self.web.setUrl(QUrl(self.session.test_url)))
        floating_layout.addWidget(self.return_button)

        self.finish_button = QPushButton("Завершить тест")
        self.finish_button.setEnabled(False)
        self.finish_button.hide()
        self.finish_button.clicked.connect(lambda: self.finish("user_finished"))
        floating_layout.addWidget(self.finish_button)

        shadow = QGraphicsDropShadowEffect(self.float_panel)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(20, 25, 35, 25))
        self.float_panel.setGraphicsEffect(shadow)

        self.setCentralWidget(self.surface)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f4f5f7; color: #24272d; font-family: 'Segoe UI'; font-size: 13px; }
            QFrame#testPanel, QFrame#floatingPanel { background: white; border: 1px solid #e6e8ed; border-radius: 14px; }
            QLabel { background: transparent; border: none; }
            QLabel#title { font-size: 16px; font-weight: 600; }
            QLabel#hint { color: #7b818b; font-size: 12px; }
            QLabel#camera { background: #161a22; color: #abb1bc; border-radius: 9px; }
            QLineEdit { background: #fafbfc; border: 1px solid #e2e5eb; border-radius: 8px; padding: 10px; }
            QLineEdit:focus { border-color: #8a97ac; }
            QPushButton { background: white; border: 1px solid #e1e4ea; border-radius: 8px; padding: 11px; }
            QPushButton:hover { background: #f1f3f6; }
            QPushButton#primary { background: #24272d; color: white; border: none; }
            QPushButton#primary:hover { background: #404550; }
            QPushButton:disabled, QPushButton#primary:disabled { background: #eceef2; color: #a1a7b0; }
            QListWidget#eventLog {
                background: #161a22; color: #d8dde6; border: 1px solid #232a36;
                border-radius: 9px; padding: 6px; font-size: 12px;
            }
            QListWidget#eventLog::item { padding: 3px 4px; border-bottom: 1px solid #1f2632; }
        """)
        self.position_panels()

    def position_panels(self):
        if not hasattr(self, "surface"):
            return
        width, height = self.surface.width(), self.surface.height()
        self.test_panel.setGeometry(14, 14, max(100, int(width * .70) - 20), max(100, height - 28))
        panel_width = max(160, int(width * .28) - 14)
        self.video.setFixedHeight(max(100, int((panel_width - 32) * .75)))
        self.float_panel.setGeometry(int(width * .72), 14, panel_width,
                                     min(self.float_panel.sizeHint().height(), max(150, height - 28)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_panels()

    def append_log(self, kind, details=None):
        label = self.EVENT_LABELS.get(kind, kind)
        detail = ""
        if details:
            if isinstance(details, dict):
                parts = []
                if "combination" in details:
                    parts.append(str(details["combination"]))
                if "count" in details:
                    parts.append(f"кол-во: {details['count']}")
                if "host" in details:
                    parts.append(str(details["host"]))
                if "origin" in details:
                    parts.append(str(details["origin"]))
                if "direction" in details:
                    parts.append(str(details["direction"]))
                if "message" in details:
                    parts.append(str(details["message"]))
                if parts:
                    detail = " - " + ", ".join(parts)
            else:
                detail = " - " + str(details)
        text = f"{time.strftime('%H:%M:%S')}  {label}{detail}"
        item = QListWidgetItem(text)
        self.event_log.insertItem(0, item)
        while self.event_log.count() > 200:
            self.event_log.takeItem(self.event_log.count() - 1)

    def save_session_log(self):
        if self.session is None:
            return None
        logs_dir = self.root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%d%m%Y-%H%M%S")
        target = logs_dir / f"emx-log-{stamp}.json"
        payload = {
            "schema_version": 1,
            "started_at": self.session.started_at,
            "finished_at": utc_now(),
            "test_url": self.session.test_url,
            "trust_score_final": self.session.score,
            "capabilities": self.session.capabilities,
            "events": self.session.events,
        }
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target)
        except OSError as exc:
            self.bus.emit("ENGINE_ERROR", {"component": "log_writer", "message": str(exc)})
            return None
        return target
    
    def enforce_fullscreen(self):
        if self.finished or self.closed:
            return
        if not self.isFullScreen():
            self.showFullScreen()
        if self.isMinimized():
            self.bus.emit("WINDOW_MINIMIZE_BLOCKED")
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def restore_exam_window(self):
        if self.finished or self.closed:
            return
        self.enforce_fullscreen()

    def changeEvent(self, event):
        super().changeEvent(event)
        if self.finished or self.closed:
            return
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self.bus.emit("WINDOW_MINIMIZE_BLOCKED")
                QTimer.singleShot(0, self.restore_exam_window)
            elif not self.isFullScreen() and self.session and not self.finished:
                QTimer.singleShot(0, self.restore_exam_window)

    def refresh_start(self, *_):
        if not hasattr(self, "start_button") or self.finished or self.session:
            return
        allowed = self.policy.allows(self.address.text().strip())
        ready = (self.last_frame_ok and self.cv.baseline is not None
                 and self.cv.yolo.ready and not self.cv.yolo.failed)
        self.start_button.setEnabled(allowed and ready)
        if not self.address.text().strip():
            self.setup_hint.setText("Укажите адрес сайта экзамена.")
        elif not allowed:
            self.setup_hint.setText("Этот адрес не разрешён для экзамена.")
        elif not ready:
            self.setup_hint.setText("Посмотрите в камеру. Подготовка займёт несколько секунд.")
        else:
            self.setup_hint.setText("Можно начинать тест.")

    def start_test(self):
        if (self.finished or not self.last_frame_ok or self.cv.baseline is None
                or not self.cv.yolo.ready or self.cv.yolo.failed):
            return
        test_url = self.address.text().strip()
        if not self.policy.allows(test_url):
            QMessageBox.warning(self, "Адрес теста", "Этот адрес не входит в белый список.")
            return
        self.config["test_url"] = test_url
        try:
            monitors = self.security.start()
        except Exception as exc:
            QMessageBox.critical(self, "Нельзя начать тест", str(exc))
            return

        self.bus.drain(10000)
        capabilities = self.security.capabilities()
        capabilities["monitors_at_start"] = monitors
        capabilities["allowlist"] = list(self.config["allowlist"])
        self.session = Session(self.config["test_url"], capabilities)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.cv.activate()
        self.start_button.setEnabled(False)
        self.finish_button.setEnabled(True)
        self.address.setEnabled(False)
        self.return_button.setEnabled(True)
        self.setup_bar.hide()
        self.setup_hint.hide()
        self.start_button.hide()
        self.return_button.show()
        self.finish_button.show()
        self.title.setText("Тест идёт")
        self.position_panels()
        self.web.setUrl(QUrl(self.config["test_url"]))
        self.bus.emit("SESSION_STARTED")

    def tick(self):
        if self.finished:
            return
        try:
            raw_frame, status = self.cv.tick()
            frame = self.cv.preview_frame if self.cv.preview_frame is not None else raw_frame
        except Exception as exc:
            frame, status = None, "Ошибка CV"
            self.bus.emit("ENGINE_ERROR", {"component": "FaceMesh", "message": str(exc)})
        self.last_frame_ok = frame is not None
        if frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
            self.video.setPixmap(QPixmap.fromImage(image).scaled(
                self.video.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.video.setText(f"Камера\n{status}")
        if self.session is None:
            self.refresh_start()
        for event in self.bus.drain():
            kind = event["type"]
            self.append_log(kind, event.get("details"))  
            if kind == "EMERGENCY_EXIT":
                if self.session:
                    self.finish("emergency_exit")
                else:
                    self.close()
                return
            if self.session and not self.finished:
                self.session.record(event)
                if kind == "URL_BLOCKED" and event["details"].get("main_frame"):
                    self.web.setUrl(QUrl(self.blocked_url))
            if kind == "SYSTEM_WINDOW_DETECTED" and self.session and not self.finished:
                self.restore_exam_window()
            if kind in {"ENGINE_ERROR", "MULTIPLE_MONITORS"}:
                message = str(event["details"])
                if self.session:
                    self.finish(kind.lower())
                else:
                    self.finished = True
                    self.start_button.setEnabled(False)
                    self.timer.stop()
                    self.cv.close()
                self.title.setText("Тест остановлен")
                QMessageBox.critical(self, "Сессия остановлена", message)
                return

    def application_state(self, state):
        if self.session and not self.finished and state != Qt.ApplicationState.ApplicationActive:
            self.bus.emit("FOCUS_LOST")
            QTimer.singleShot(0, self.restore_exam_window)

    def finish(self, reason):
        if self.finishing or self.finished or self.session is None:
            return
        self.finishing = True
        self.finished = True
        self.timer.stop()
        self.security.stop()
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.showFullScreen()
        self.cv.close()
        self.web.setUrl(QUrl("about:blank"))
        self.finish_button.setEnabled(False)
        self.return_button.setEnabled(False)
        self.title.setText("Тест завершён")
        for event in self.bus.drain(10000):
            if event["type"] != "EMERGENCY_EXIT":
                self.session.record(event)
        self.session.record({
            "timestamp": utc_now(),
            "type": "SESSION_FINISHED",
            "details": {"reason": reason},
        })
        target = self.root / self.config["report_path"]
        log_path = None
        try:
            self.session.save(
                target, reason,
                self.cv.baseline.tolist() if self.cv.baseline is not None else None,
            )
            log_path = self.save_session_log()
            message = "Отчёт сохранён:\n" + str(target)
            if log_path is not None:
                message += "\n\nЛог сессии:\n" + str(log_path)
            message += "\n\nМожно закрыть приложение."
            QMessageBox.information(self, "Тест завершён", message)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось сохранить отчёт", str(exc))
            self.title.setText("Тест завершён")
        finally:
            self.finishing = False

    def cleanup(self):
        if self.closed:
            return
        if self.session and not self.finished:
            self.finish("application_quit")
        self.closed = True
        self.timer.stop()
        self.keepalive_timer.stop()
        self.security.stop()
        if self.cv and not self.finished:
            self.cv.close()
        self.server.stop()
        from PyQt6 import sip
        sip.delete(self.page)

    def closeEvent(self, event):
        if self.session and not self.finished:
            self.bus.emit("WINDOW_CLOSE_BLOCKED")
            event.ignore()
            return
        self.cleanup()
        event.accept()