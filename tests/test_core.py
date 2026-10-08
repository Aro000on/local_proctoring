import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import ConditionGate, EventBus, Session, URLPolicy
from dns_proxy import LocalServer


class CoreTests(unittest.TestCase):
    def test_look_away_after_three_seconds_and_rearm(self):
        gate = ConditionGate(3)
        self.assertFalse(gate.update(True, 10))
        self.assertFalse(gate.update(True, 12.99))
        self.assertTrue(gate.update(True, 13))
        self.assertFalse(gate.update(True, 100))
        self.assertFalse(gate.update(False, 101))
        self.assertFalse(gate.update(True, 102))
        self.assertTrue(gate.update(True, 105))

    def test_brief_return_to_center_resets_episode(self):
        gate = ConditionGate(3)
        gate.update(True, 0)
        gate.update(False, 2.9)
        self.assertFalse(gate.update(True, 3.1))
        self.assertFalse(gate.update(True, 5.9))
        self.assertTrue(gate.update(True, 6.2))

    def test_url_policy_exact_origin(self):
        policy = URLPolicy(["https://exam.example", "http://127.0.0.1:8080"])
        for url in ["https://exam.example/test?q=1", "https://EXAM.EXAMPLE:443/a", "http://127.0.0.1:8080/exam"]:
            self.assertTrue(policy.allows(url), url)
        for url in ["https://exam.example.evil.org", "https://exam.example@evil.org",
                    "https://evil.org@exam.example", "https://sub.exam.example",
                    "http://exam.example", "https://exam.example:8080",
                    "file:///etc/passwd", "javascript:alert(1)", "data:text/html,hello",
                    "http://127.0.0.1:9999", "https://exam.example\\@evil.org",
                    "https://exam.example:invalid", "https://chatgpt.com", "https://google.com",
                    "https://yandex.ru", "https://claude.ai"]:
            self.assertFalse(policy.allows(url), url)

    def test_unicode_hostname(self):
        policy = URLPolicy(["https://тест.рф"])
        self.assertTrue(policy.allows("https://xn--e1aybc.xn--p1ai/a"))

    def test_wildcard_iaron_subdomains(self):
        policy = URLPolicy(["https://*.example.com", "https://example.com", "http://*.example.com"])
        for url in ["https://exam.example.com/test", "https://api.exam.example.com", "https://example.com", "http://exam.example.com"]:
            self.assertTrue(policy.allows(url), url)
        for url in ["https://evilexample.com", "https://example.com.evil.org", "https://exam.example.com.evil.org",
                    "https://exam.example.com@evil.org", "https://evil.org@exam.example.com",
                    "https://exam.example.com:8443", "https://*.example.com", "http://example.com"]:
            self.assertFalse(policy.allows(url), url)
        self.assertFalse(URLPolicy(["https://*.example.com"]).allows("https://example.com"))

    def test_score_dedup_and_floor(self):
        session = Session("http://localhost", {})
        def event(kind):
            return {"timestamp": "now", "type": kind, "details": {}}
        session.record(event("MULTIPLE_PEOPLE"), now=0)
        self.assertIsNone(session.record(event("MULTIPLE_FACES"), now=1))
        self.assertEqual(session.score, 70)
        session.record(event("PHONE_DETECTED"), now=6)
        session.record(event("LOOK_AWAY"), now=7)
        session.record(event("PHONE_DETECTED"), now=12)
        session.record(event("PHONE_DETECTED"), now=18)
        self.assertEqual(session.score, 0)
        self.assertEqual(sum(e["penalty"] for e in session.events), 100)

    def test_report_roundtrip(self):
        session = Session("http://localhost", {"keyboard": "test"})
        session.record({"timestamp": "now", "type": "LOOK_AWAY", "details": {"direction": "LEFT"}})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "reports/proctoring_report.json"
            session.save(path, "user_finished", [0, 0, 0, 0, 0])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["trust_score"], 85)
            self.assertEqual(data["events"][0]["details"]["direction"], "LEFT")
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_bus(self):
        bus = EventBus()
        bus.emit("PHONE_DETECTED", {"confidence": 0.8})
        self.assertEqual(bus.drain()[0]["type"], "PHONE_DETECTED")
        self.assertEqual(bus.drain(), [])

    def test_local_server_start_and_stop(self):
        server = LocalServer(0)  # ОС выбирает свободный порт для теста.
        server.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.port}/exam", timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("Локальный тест", response.read().decode())
            with self.assertRaises(HTTPError) as caught:
                urlopen(f"http://127.0.0.1:{server.port}/blocked", timeout=3)
            self.assertEqual(caught.exception.code, 403)
            self.assertIn("Доступ заблокирован", caught.exception.read().decode())
            caught.exception.close()
        finally:
            server.stop()
        self.assertFalse(server.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
