from __future__ import annotations

import json
import os
import queue
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


PENALTIES = {
    "LOOK_AWAY": 15, "PHONE_DETECTED": 30, "MULTIPLE_PEOPLE": 30,
    "MULTIPLE_FACES": 30, "NO_FACE": 15, "HOTKEY_BLOCKED": 15,
    "CLIPBOARD_CLEARED": 15, "URL_BLOCKED": 15, "FOCUS_LOST": 15,
    "MULTIPLE_MONITORS": 30,
    "SYSTEM_WINDOW_DETECTED": 15, "WINDOW_MINIMIZE_BLOCKED": 15,
    "WINDOW_CLOSE_BLOCKED": 15,
}


class EventBus:
    def __init__(self):
        self.queue = queue.SimpleQueue()

    def emit(self, kind, details=None):
        self.queue.put({"timestamp": utc_now(), "type": kind,
                        "details": details or {}})

    def drain(self, limit=200):
        events = []
        for _ in range(limit):
            try:
                events.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return events


class ConditionGate:
    def __init__(self, duration):
        self.duration = duration
        self.since = None
        self.fired = False

    def update(self, active, now=None):
        now = time.monotonic() if now is None else now
        if not active:
            self.since, self.fired = None, False
            return False
        if self.since is None:
            self.since = now
        if not self.fired and now - self.since >= self.duration:
            self.fired = True
            return True
        return False


class Session:
    def __init__(self, test_url, capabilities):
        self.started_at = utc_now()
        self.started_monotonic = time.monotonic()
        self.test_url = test_url
        self.capabilities = capabilities
        self.score = 100
        self.events = []
        self.last_event = {}

    def record(self, event, now=None):
        now = time.monotonic() if now is None else now
        kind = event["type"]
        key = "EXTRA_SUBJECT" if kind in {"MULTIPLE_FACES", "MULTIPLE_PEOPLE"} else kind
        if kind in PENALTIES and now - self.last_event.get(key, -1e9) < 5:
            return None
        self.last_event[key] = now
        penalty = PENALTIES.get(kind, 0)
        actual_penalty = min(self.score, penalty)
        self.score -= actual_penalty
        saved = dict(event, penalty=actual_penalty, trust_score=self.score)
        self.events.append(saved)
        return saved

    def save(self, path, reason, calibration=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 1, "started_at": self.started_at,
            "finished_at": utc_now(),
            "duration_seconds": round(time.monotonic() - self.started_monotonic, 2),
            "test_url": self.test_url, "finish_reason": reason,
            "trust_score": self.score, "capabilities": self.capabilities,
            "calibration": calibration, "events": self.events,
            "notice": "Эвристический MVP; события требуют проверки человеком. Видео не сохраняется.",
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        return path


class URLPolicy:
    def __init__(self, origins):
        exact, wildcard = [], []
        for url in origins:
            if "://*." in url:
                rule = self.origin(url.replace("://*.", "://", 1))
                if "." not in rule[1]:
                    raise ValueError("Wildcard требует полного домена")
                wildcard.append(rule)
            else:
                exact.append(self.origin(url))
        self.origins = frozenset(exact)
        self.wildcards = tuple(wildcard)

    @staticmethod
    def origin(url):
        if any(ord(c) < 32 for c in url) or "\\" in url:
            raise ValueError("Недопустимый URL")
        p = urlsplit(url)
        if p.scheme.lower() not in {"http", "https"} or not p.hostname:
            raise ValueError("Разрешены только абсолютные HTTP(S) URL")
        if p.username is not None or p.password is not None:
            raise ValueError("URL с учётными данными запрещён")
        host = p.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if "*" in host:
            raise ValueError("Wildcard недопустим в URL запроса")
        return (p.scheme.lower(), host, p.port or (443 if p.scheme.lower() == "https" else 80))

    def allows(self, url):
        try:
            origin = self.origin(url)
            if origin in self.origins:
                return True
            scheme, host, port = origin
            return any(scheme == s and port == p and host.endswith("." + domain)
                       for s, domain, p in self.wildcards)
        except (ValueError, UnicodeError):
            return False
