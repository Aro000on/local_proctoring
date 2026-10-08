from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

BLOCKED_HTML = """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Доступ заблокирован</title><style>body{font:20px system-ui;margin:10vh 12vw;
background:#f4f6fa;color:#172033}a{color:#245bd8}</style>
<h1>Доступ заблокирован прокторингом</h1>
<p>Разрешены только ресурсы из белого списка экзамена.</p></html>"""

EXAM_HTML = """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Демонстрационный тест</title><style>body{font:18px system-ui;max-width:700px;
margin:50px auto;padding:20px;color:#172033}button{padding:12px;margin-top:20px}
textarea{width:95%;height:100px}a{display:block;margin:12px 0}</style>
<h1>Локальный тест</h1><p>Объясните разницу между списком и кортежем в Python.</p>
<textarea placeholder="Введите ответ"></textarea>
<p>Ответ остаётся на странице, отправка на сервер в MVP не реализована.</p>
<h2>Проверка фильтра</h2><a href="https://google.com">Google (будет заблокирован)</a>
<a href="https://chatgpt.com">ChatGPT (будет заблокирован)</a>
<a href="/exam">Вернуться к тесту</a></html>"""

READY_HTML = """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Подготовка к экзамену</title><style>body{font:16px system-ui;background:#fff;
color:#252932;display:grid;place-items:center;min-height:85vh;margin:0}
main{max-width:430px;padding:35px}h1{font-size:26px;font-weight:600}
p{color:#7b818b;line-height:1.7}.label{font-size:12px;letter-spacing:2px}</style>
<main><p class="label">ЛОКАЛЬНЫЙ ПРОКТОРИНГ</p><h1>Готовимся к экзамену</h1>
<p>Укажите адрес сайта экзамена в поле сверху. После проверки камеры и
калибровки взгляда нажмите «Начать тест».</p>
<p>Разрешены example.com и его поддомены.</p></main></html>"""


class LocalServer:
    def __init__(self, port=8080):
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?", 1)[0]
                pages = {"/exam": EXAM_HTML, "/ready": READY_HTML}
                body = pages.get(path, BLOCKED_HTML).encode("utf-8")
                self.send_response(200 if path in pages else 403)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = Thread(target=self.server.serve_forever, daemon=True, name="local-http")
        self.thread.start()

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None


def make_interceptor(policy, bus, parent):
    from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestInfo

    class AllowlistInterceptor(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            url = info.requestUrl().toString()
            if url == "about:blank" or policy.allows(url):
                return
            info.block(True)
            try:
                origin = str(policy.origin(url))
            except (ValueError, UnicodeError):
                origin = info.requestUrl().scheme() + ":<blocked>"
            main_frame = info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame
            bus.emit("URL_BLOCKED", {"origin": origin, "source": "request_interceptor", "main_frame": main_frame})

    return AllowlistInterceptor(parent)
