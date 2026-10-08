import os
from pathlib import Path


def main():
    from ultralytics import YOLO
    models = Path(__file__).resolve().parent / "models"
    models.mkdir(exist_ok=True)
    previous = Path.cwd()
    try:
        os.chdir(models)
        model = YOLO("yolov8n.pt")
        model.predict(source=__import__("numpy").zeros((480, 640, 3), dtype="uint8"),
                      device="cpu", verbose=False)
        print(f"Модель загружена и проверена: {models / 'yolov8n.pt'}")
    finally:
        os.chdir(previous)


if __name__ == "__main__":
    main()
