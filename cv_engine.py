from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np

try:
    import mediapipe as mp
except ImportError as exc:
    raise ImportError(
        "mediapipe не загрузился (DLL load failed).\n"
        "1) Установите VC++ Redistributable: https://aka.ms/vs/17/release/vc_redist.x64.exe\n"
        "2) pip install numpy==1.26.4 protobuf==4.25.3\n"
        "3) Удалите .venv и запустите RUN.bat заново.\n"
        f"Деталь: {exc}"
    ) from exc

from core import ConditionGate


class YoloWorker(threading.Thread):
    def __init__(self, weights, bus):
        super().__init__(daemon=True, name="yolo-inference")
        self.weights, self.bus = weights, bus
        self.condition = threading.Condition()
        self.pending = None
        self.result = (0.0, [])
        self.stopping = False
        self.ready = False
        self.failed = False
        self.active_since = float("inf")
        self.phone_gate = ConditionGate(0.0)
        self.people_gate = ConditionGate(0.5)

    def submit(self, frame, timestamp):
        with self.condition:
            self.pending = (timestamp, frame.copy())
            self.condition.notify()

    def snapshot(self):
        with self.condition:
            return self.result[0], list(self.result[1])

    def stop(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()
        if self.is_alive():
            self.join(timeout=3) 

    def run(self):
        try:
            from ultralytics import YOLO
            if not Path(self.weights).is_file():
                raise FileNotFoundError(
                    "Нет весов YOLO. Сначала: python download_models.py"
                )
            model = YOLO(str(self.weights))
            model.predict(
                np.zeros((480, 640, 3), np.uint8),
                imgsz=640, classes=[0, 67], conf=0.4, device="cpu", verbose=False,
            )
            self.ready = True
            self.bus.emit("MODEL_READY", {"model": Path(self.weights).name})
            while True:
                with self.condition:
                    self.condition.wait_for(
                        lambda: self.stopping or self.pending is not None
                    )
                    if self.stopping:
                        break
                    captured, frame = self.pending
                    self.pending = None
                pred = model.predict(
                    frame, imgsz=640, classes=[0, 67],
                    conf=0.4, device="cpu", verbose=False,
                )[0]
                boxes = []
                for b in pred.boxes:
                    cls = int(b.cls.item())
                    boxes.append({
                        "class": cls,
                        "label": model.names[cls],
                        "confidence": round(float(b.conf.item()), 3),
                        "xyxy": [int(v) for v in b.xyxy[0].tolist()],
                    })
                with self.condition:
                    self.result = (captured, boxes)
                now = time.monotonic()
                fresh = now - captured < 2.0
                with self.condition:
                    if captured < self.active_since:
                        continue
                    if self.phone_gate.update(
                        fresh and any(b["class"] == 67 for b in boxes), now
                    ):
                        self.bus.emit("PHONE_DETECTED", {"source": "YOLO", "boxes": boxes})
                    count = sum(b["class"] == 0 for b in boxes)
                    if self.people_gate.update(fresh and count > 1, now):
                        self.bus.emit(
                            "MULTIPLE_PEOPLE", {"count": count, "source": "YOLO"}
                        )
        except Exception as exc:
            self.failed = True
            self.bus.emit("ENGINE_ERROR", {"component": "YOLO", "message": str(exc)})


class CVEngine:
    MODEL_POINTS = np.array([
        (0, 0, 0), (0, -330, -65), (-225, 170, -135),
        (225, 170, -135), (-150, -150, -125), (150, -150, -125),
    ], dtype=np.float64)
    POSE_INDICES = [1, 152, 33, 263, 61, 291]

    def __init__(self, bus, weights, camera_index=0):
        self.bus = bus
        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(
                "Веб-камера недоступна. Проверьте разрешения и индекс камеры."
            )
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        try:
            self.mesh = mp.solutions.face_mesh.FaceMesh(
                max_num_faces=3, refine_landmarks=True,
                min_detection_confidence=0.5, min_tracking_confidence=0.5,
            )
        except Exception:
            self.cap.release()
            raise
        self.yolo = YoloWorker(weights, bus)
        self.look_gate = ConditionGate(3.0)
        self.no_face_gate = ConditionGate(3.0)
        self.faces_gate = ConditionGate(0.5)
        self.baseline = None
        self.samples = []
        self.calibration_started = None
        self.active = False
        self.last_submit = 0.0
        self.camera_failures = 0
        self.closed = False
        self.preview_frame = None
        self.yolo.start()

    def activate(self):
        self.active = True
        self.look_gate = ConditionGate(3.0)
        self.no_face_gate = ConditionGate(3.0)
        self.faces_gate = ConditionGate(0.5)
        with self.yolo.condition:
            self.yolo.active_since = time.monotonic()
            self.yolo.phone_gate = ConditionGate(0.0)
            self.yolo.people_gate = ConditionGate(0.5)

    @staticmethod
    def eye_features(points, iris, corners, lids):
        a, b = points[corners[0]], points[corners[1]]
        horizontal = b - a
        width = np.linalg.norm(horizontal)
        if width < 4:
            return None
        horizontal /= width
        if horizontal[0] < 0:
            horizontal = -horizontal
        vertical = np.array([-horizontal[1], horizontal[0]])
        center = (a + b) / 2
        height = abs(float(np.dot(points[lids[1]] - points[lids[0]], vertical)))
        if height / width < 0.12:
            return None 
        offset = points[iris] - center
        return np.array([
            np.dot(offset, horizontal) / width,
            np.dot(offset, vertical) / height,
        ])

    def features(self, points, width, height):
        focal = float(width)
        camera = np.array(
            [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]],
            dtype=np.float64,
        )
        ok, rotation, _ = cv2.solvePnP(
            self.MODEL_POINTS, points[self.POSE_INDICES].astype(np.float64),
            camera, np.zeros((4, 1)), flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok:
            return None
        matrix, _ = cv2.Rodrigues(rotation)
        pitch, yaw, roll = cv2.RQDecomp3x3(matrix)[0]
        left = self.eye_features(points, 468, (33, 133), (159, 145))
        right = self.eye_features(points, 473, (362, 263), (386, 374))
        if left is None or right is None:
            return None
        gaze = (left + right) / 2
        return np.array([gaze[0], gaze[1], yaw, pitch, roll], dtype=float)

    def calibrate(self, features, now):
        if self.calibration_started is None:
            self.calibration_started = now
        self.samples.append(features)
        if now - self.calibration_started >= 2 and len(self.samples) >= 30:
            samples = np.array(self.samples)
            samples[:, 2:] = (
                samples[0, 2:] + (samples[:, 2:] - samples[0, 2:] + 180) % 360 - 180
            )
            if (
                np.any(np.std(samples[:, 2:], axis=0) > 8)
                or np.any(np.std(samples[:, :2], axis=0) > 0.12)
            ):
                self.samples = []
                self.calibration_started = None
                return
            self.baseline = np.median(samples, axis=0)
            self.bus.emit("CALIBRATED", {"baseline": self.baseline.tolist()})

    def tick(self):
        ok, frame = self.cap.read()
        if not ok:
            self.camera_failures += 1
            self.look_gate.update(False)
            if self.camera_failures == 10:
                self.bus.emit(
                    "ENGINE_ERROR",
                    {"component": "camera", "message": "Поток камеры потерян"},
                )
            return None, "Нет кадра"
        self.camera_failures = 0
        self.preview_frame = None
        now = time.monotonic()
        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        result = self.mesh.process(rgb)
        faces = result.multi_face_landmarks or []
        if now - self.last_submit >= 0.15:
            self.yolo.submit(frame, now)
            self.last_submit = now
        stamp, boxes = self.yolo.snapshot()
        if now - stamp < 2:
            for box in boxes:
                x1, y1, x2, y2 = box["xyxy"]
                color = (0, 0, 255) if box["class"] == 67 else (255, 180, 0)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame, f'{box["label"]} {box["confidence"]:.2f}',
                    (x1, max(20, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1,
                )
        for face in faces:
            points = np.array(
                [(lm.x * width, lm.y * height) for lm in face.landmark]
            )
            x1, y1 = np.min(points, axis=0).astype(int)
            x2, y2 = np.max(points, axis=0).astype(int)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (60, 210, 60), 2)
        status = "Смотрите в центр экрана: калибровка 2 секунды"
        valid = False
        direction = "CENTER"
        detail = {}
        if len(faces) == 1:
            points = np.array(
                [(lm.x * width, lm.y * height) for lm in faces[0].landmark]
            )
            features = self.features(points, width, height)
            if features is not None:
                valid = True
                if self.baseline is None:
                    self.calibrate(features, now)
                else:
                    delta = features - self.baseline
                    delta[2:] = (delta[2:] + 180) % 360 - 180
                    gx, gy, yaw, pitch, roll = delta
                    away = (
                        abs(gx) > .18 or gy > .22
                        or abs(yaw) > 20 or abs(pitch) > 18
                    )
                    if gy > .22:
                        direction = "DOWN"
                    elif abs(gx) > .18:
                        direction = "RIGHT" if gx > 0 else "LEFT"
                    elif away:
                        direction = "HEAD_AWAY"
                    detail = {
                        "direction": direction,
                        "gaze_delta": [round(float(gx), 3), round(float(gy), 3)],
                        "yaw_delta": round(float(yaw), 1),
                        "pitch_delta": round(float(pitch), 1),
                        "roll_delta": round(float(roll), 1),
                    }
                    status = (
                        f"{direction} | Yaw {yaw:+.0f} "
                        f"Pitch {pitch:+.0f} Roll {roll:+.0f}"
                    )
                    origin = tuple(points[1].astype(int))
                    end = (int(origin[0] + gx * 250), int(origin[1] + gy * 150))
                    cv2.arrowedLine(
                        frame, origin, end, (0, 200, 255), 3, tipLength=.25
                    )
                    if self.active and self.look_gate.update(away, now):
                        self.bus.emit("LOOK_AWAY", detail)
        if not valid:
            self.look_gate.update(False, now)
            if self.baseline is None:
                self.samples = []
                self.calibration_started = None
            status = "Нужно одно видимое лицо и открытые глаза"
        if self.active:
            if self.no_face_gate.update(len(faces) == 0, now):
                self.bus.emit("NO_FACE")
            if self.faces_gate.update(len(faces) > 1, now):
                self.bus.emit(
                    "MULTIPLE_FACES", {"count": len(faces), "source": "MediaPipe"}
                )
            self.preview_frame = frame.copy()
        return frame, status

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.active = False
        try:
            self.yolo.stop()
        finally:
            self.cap.release()
            self.mesh.close()