import argparse
import json
import os
import sys
from pathlib import Path


def main():
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        raise SystemExit(
            "Эта сборка с legacy MediaPipe Face Mesh требует Python 3.10–3.12 "
            "(рекомендуется 3.11)."
        )

    try:
        import mediapipe as _mp_probe
        del _mp_probe
    except ImportError as exc:
        msg = (
            "MediaPipe не загрузился.\n\n"
            "1. Установите Microsoft Visual C++ Redistributable x64:\n"
            "   https://aka.ms/vs/17/release/vc_redist.x64.exe\n"
            "   и перезагрузите ПК.\n\n"
            "2. Убедитесь, что установлены правильные версии:\n"
            "   pip install numpy==1.26.4 protobuf==4.25.3\n\n"
            "3. Проверьте из активированного .venv:\n"
            "   python -c \"import mediapipe\"\n\n"
            f"Техническая деталь: {exc}"
        )
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.critical(None, "Ошибка запуска", msg)
        except Exception:
            print(msg, file=sys.stderr)
        return 1

    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-quic --force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    )

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    from app_gui import ProctoringWindow
    from core import URLPolicy

    parser = argparse.ArgumentParser(description="Локальный прокторинг MVP")
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("config.json")
    )
    parser.add_argument("--test-url", help="URL теста; его точный origin будет разрешён")
    args = parser.parse_args()

    app = QApplication(sys.argv[:1])
    window = None
    try:
        config_path = args.config.resolve()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if args.test_url:
            URLPolicy.origin(args.test_url)  # Проверка формата до старта.
            config["test_url"] = args.test_url
            config["allowlist"].append(args.test_url)
        URLPolicy(config["allowlist"])
        if not (config_path.parent / config["yolo_weights"]).is_file():
            raise FileNotFoundError(
                "Нет models/yolov8n.pt. Выполните python download_models.py до запуска."
            )
        window = ProctoringWindow(config, config_path.parent)
        app.aboutToQuit.connect(window.cleanup)
        window.show()
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(None, "Ошибка запуска", str(exc))
        return 1
    finally:
        if window:
            window.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())