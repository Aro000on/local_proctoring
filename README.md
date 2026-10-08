# Локальный прокторинг MVP

Приложение для удалённого прокторинга онлайн-экзаменов с контролем
поведения студента в реальном времени. Работает локально, без
облачных сервисов и без сохранения видео.

> Учебный проект для хакатона. Демонстрирует возможность построения
> системы прокторинга на полностью локальном стеке: MediaPipe, YOLOv8,
> PyQt6 WebEngine, Win32 хуки.

---

## Возможности

| Категория | Что делает |
|-----------|-----------|
| Контроль взгляда | MediaPipe Face Mesh + оценка направления зрачка и позы головы. Эпизод "отведён взгляд" через 3 секунды |
| Контроль присутствия | Нет лица > 3 сек, несколько лиц > 0.5 сек |
| Детекция предметов | YOLOv8n - телефон (cell phone) и второй человек в кадре |
| Блокировка шорткатов | Win32 low-level hook: Alt+Tab, Alt+F4, Win+R, Win+E, Ctrl+C/V и др. |
| Буфер обмена | Очистка Qt-clipboard каждые 500 мс |
| Фильтр URL | Qt WebEngine interceptor + allowlist с wildcard-поддоменами |
| Контроль мониторов | Проверка перед стартом и каждые 2 секунды |
| Контроль системы | Детекция Проводника/Диспетчера задач в фокусе, восстановление окна |
| Полноэкранный режим | Окно нельзя свернуть, перевести в фон, закрыть во время сессии |
| Trust Score | Штрафной счётчик 0-100 с дедупликацией эпизодов (5 сек) |
| Отчёты | reports/proctoring_report.json + logs/emx-log-ддммгггг-ччммсс.json |

---

## Стек

- Python 3.11 (обязательно 3.10-3.12)
- PyQt6 6.8.1 + PyQt6-WebEngine 6.8.0 - GUI и изолированный браузер
- MediaPipe 0.10.21 - Face Mesh (468 ландмарков), оценка позы и взгляда
- Ultralytics YOLOv8n 8.3.203 - детекция объектов (CPU)
- OpenCV 4.11.0.86 (обычный + contrib)
- pynput 1.8.1 - low-level Windows keyboard hook
- screeninfo 0.8.1 - инвентаризация мониторов

### Критичные пины

    numpy==1.26.4          # numpy 2.x ломает mediapipe 0.10.21
    protobuf==4.25.3       # protobuf 5.x/6.x ломает mediapipe 0.10.21

Все пины уже зафиксированы в requirements.txt.

---

## Требования к системе

- Windows 10 1809+ / Windows 11 (x64)
- Microsoft Visual C++ Redistributable 2015-2022 x64:
  https://aka.ms/vs/17/release/vc_redist.x64.exe
- Python 3.10-3.12 x64 (рекомендуется 3.11):
  https://www.python.org/downloads/release/python-3119/
  (при установке отметить "Add python.exe to PATH")
- Веб-камера (встроенная или USB)
- Интернет - только один раз при первом запуске для скачивания
  модели YOLOv8n (~6 МБ)

---

## Быстрый старт

### Способ 1 - двойной клик (рекомендуется)

1. Установите VC++ Redistributable x64 и Python 3.11 x64.
2. Клонируйте репозиторий.
3. Двойной клик по RUN.bat.

Скрипт сам:

- найдёт Python 3.10-3.12
- создаст .venv
- установит зависимости (~5-15 минут при первом запуске)
- скачает модель YOLOv8n
- проверит MediaPipe FaceMesh
- запустит main.py

Повторные запуски - 3-5 секунд.

### Способ 2 - вручную

    git clone https://github.com/Aro000on/local_proctoring.git
    cd local_proctoring

    py -3.11 -m venv .venv
    .venv\Scripts\activate
    python -m pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt

    python download_models.py
    python main.py

Опционально - передать свой URL теста и config:

    python main.py --test-url https://example.com/exam --config config.json

---

## Использование

1. Запустите приложение (RUN.bat или python main.py).
2. Посмотрите в камеру 2 секунды - идёт калибровка взгляда
   (в правой панели под камерой видны события).
3. Введите адрес сайта экзамена в поле сверху. Он должен быть в
   allowlist (config.json) или передан через --test-url.
4. Нажмите "Начать тест", когда кнопка станет активной.
   Условия: камера даёт кадр, калибровка прошла, YOLO готов.
5. Приложение разворачивается на весь экран, активирует хуки клавиатуры,
   очистку буфера, фильтр URL.
6. По окончании - "Завершить тест" или Ctrl+Shift+Q (аварийный выход).
7. Отчёт сохраняется в reports/, лог событий - в logs/.

### Лог событий в UI

Под камерой - список последних событий в реальном времени:

    14:32:05  Калибровка завершена
    14:32:18  Тест начат
    14:32:24  Буфер обмена очищен
    14:32:31  Взгляд отведён от экрана - LEFT
    14:32:45  Обнаружен телефон - кол-во: 1
    14:33:02  Заблокирована горячая клавиша - Alt+Tab

---

## Структура проекта

    local_proctoring/
    ├── main.py                 # Точка входа. mediapipe импортируется ДО Qt
    ├── app_gui.py              # PyQt6-окно: браузер, камера, лог, кнопки
    ├── cv_engine.py            # MediaPipe Face Mesh + YoloWorker (в отдельном потоке)
    ├── security_engine.py      # Хук клавиатуры, буфер, мониторы, окна
    ├── security_rules.py       # blocked_shortcut(), ForegroundInspector (Win32)
    ├── dns_proxy.py            # LocalServer + allowlist interceptor для WebEngine
    ├── core.py                 # EventBus, Session, Trust Score, URLPolicy, ConditionGate
    ├── download_models.py      # Скачивание YOLOv8n
    ├── config.json             # Конфигурация: URL, allowlist, порт, пути
    ├── requirements.txt        # Пины зависимостей
    ├── RUN.bat                 # Запуск в один клик
    ├── tests/                  # Юнит-тесты (без камеры, Qt, интернета)
    │   ├── test_core.py
    │   └── test_security_rules.py
    ├── models/                 # yolov8n.pt (скачивается)
    ├── logs/                   # emx-log-*.json (создаётся при завершении)
    ├── reports/                # proctoring_report.json
    └── .gitignore

---

## Конфигурация - config.json

    {
      "test_url": "",
      "allowlist": [
        "http://127.0.0.1:8080",
        "https://example.com",
        "https://*.example.com"
      ],
      "local_port": 8080,
      "camera_index": 0,
      "yolo_weights": "models/yolov8n.pt",
      "report_path": "reports/proctoring_report.json"
    }

- test_url - адрес экзамена. Должен быть в allowlist.
- allowlist - точные origins (схема + host + порт) или wildcard-поддомены.
  Записи вида exam.org.evil.org и exam.org@evil.org НЕ пройдут.
- camera_index - 0 для встроенной камеры, 1, 2... для внешних.
- local_port - порт локального HTTP-сервера (страница блокировки и
  демонстрационный тест).

---

## Тесты

Юнит-тесты не требуют камеры, Qt, интернета и загрузки моделей.
Проверяют логику core.py и security_rules.py в изоляции.

    python -m unittest discover -s tests -v

Что покрыто:

- ConditionGate - таймеры эпизодов (отведён взгляд, нет лица)
- URLPolicy - точные origins, wildcard-поддомены, отказ от IDN-спуфинга
- Session - Trust Score, дедупликация событий, floor = 0
- EventBus - очередь событий
- LocalServer - HTTP-сервер с allowlist-логикой
- blocked_shortcut - таблица горячих клавиш
- restricted_window - Проводник/Диспетчер задач

---

## Что НЕ перехватывается

Системные secure shortcuts не перехватываются - это by design,
user-mode hook не имеет к ним доступа:

- Ctrl+Alt+Del - SAS, обрабатывается ядром Windows
- Win+L - блокировка рабочей станции

Это фиксируется как SECURE_SHORTCUT_ATTEMPT и не понижает Trust Score.

Также не контролируются:

- Системный DNS, hosts, прокси ОС - меняется только WebEngine
- История буфера обмена Windows (Win+V) - очистка только Qt-clipboard
- Виртуальные машины, внешние мониторы, screen capture

---

## Известные ограничения

- Только Windows - Win32 hook и ForegroundInspector используют user32/kernel32.
  На Linux/macOS клавиатура не блокируется (только Qt event filter).
- Один монитор - при нескольких экранах сессия не стартует (по требованию методики).
- CPU-only - YOLO работает на CPU (device="cpu"). На слабом железе FPS
  может падать до 5-10. На GPU надо править cv_engine.py.
- MediaPipe legacy solutions API - закреплён mediapipe==0.10.21.
  Более новые версии используют Tasks API, требующий переписывания cv_engine.py.
- Порядок импортов критичен - mediapipe должен импортироваться ДО
  PyQt6/QWebEngine, иначе Chromium переопределяет DLL search path и
  нативные библиотеки mediapipe падают с "DLL load failed".

---

## Диагностика

### DLL load failed при импорте mediapipe

1. Установите VC++ Redistributable x64 -> перезагрузите ПК.
2. Проверьте версии:

    pip show numpy protobuf

   Должно быть numpy 1.26.4, protobuf 4.25.3.

3. Если версии неверные:

    pip install numpy==1.26.4 protobuf==4.25.3
    pip install --force-reinstall --no-deps mediapipe==0.10.21

4. Если не помогло - удалите .venv, запустите RUN.bat заново.

### Кнопка "Начать тест" серая

Причины по порядку:

1. Не указан URL или он не в allowlist - подсказка под камерой скажет.
2. Не прошла калибровка - смотрите в камеру 2 секунды.
3. Нет models/yolov8n.pt - python download_models.py.
4. YOLO ещё грузится - подождите 5 секунд, подсказка изменится.

### Камера не работает

1. Закройте Zoom, Skype, Discord, Teams, OBS, браузер со встречами.
2. Параметры -> Конфиденциальность -> Камера - разрешить классическим приложениям.
3. Проверьте:

    python -c "import cv2; cap=cv2.VideoCapture(0); ret, f = cap.read(); print(ret); cap.release()"

   Должно быть True.
4. Если False - перезагрузка, проверка драйверов, другой индекс в config.json.

---

## Формат отчётов

### reports/proctoring_report.json

    {
      "schema_version": 1,
      "started_at": "2026-10-08T14:30:22.145+00:00",
      "finished_at": "2026-10-08T14:35:11.982+00:00",
      "duration_seconds": 289.84,
      "test_url": "https://example.com/exam",
      "finish_reason": "user_finished",
      "trust_score": 70,
      "capabilities": {
        "keyboard": "Windows selective global hook",
        "clipboard": "Qt clear every 500 ms; no clipboard history control",
        "monitors": "screeninfo before start and every 2 s"
      },
      "calibration": [0.01, -0.02, 0.5, -1.2, 0.3],
      "events": [
        {
          "timestamp": "2026-10-08T14:30:45.221+00:00",
          "type": "LOOK_AWAY",
          "details": { "direction": "LEFT", "gaze_delta": [-0.31, 0.04] },
          "penalty": 15,
          "trust_score": 85
        }
      ],
      "notice": "Эвристический MVP; события требуют проверки человеком. Видео не сохраняется."
    }

### logs/emx-log-ддммгггг-ччммсс.json

Сырой лог сессии - все события без агрегации. Формат совпадает с
секцией events отчёта. Используется для дебага и разбора инцидентов.

---

## Trust Score

Штрафной счётчик 0-100. НЕ является вероятностью обмана - это
демонстрационная эвристика.

| Событие | Штраф |
|---------|-------|
| MULTIPLE_PEOPLE, MULTIPLE_FACES, PHONE_DETECTED, MULTIPLE_MONITORS | 30 |
| LOOK_AWAY, NO_FACE, HOTKEY_BLOCKED, CLIPBOARD_CLEARED, URL_BLOCKED, FOCUS_LOST, SYSTEM_WINDOW_DETECTED, WINDOW_MINIMIZE_BLOCKED, WINDOW_CLOSE_BLOCKED | 15 |

Дедупликация: одно событие типа не штрафует чаще одного раза в 5 секунд.
Дополнительно MULTIPLE_FACES и MULTIPLE_PEOPLE объединяются в один
эпизод (EXTRA_SUBJECT).

Floor: счёт не уходит ниже 0.

---

## Приватность

- Видео НЕ сохраняется. Кадры обрабатываются в памяти и сразу удаляются.
- WebEngine в off-the-record режиме - куки, история и кэш не переезжают
  из обычного браузера.
- Локальный сервер только на 127.0.0.1 - недоступен из сети.
- Allowlist фильтрует URL - переходы на Google, ChatGPT и пр. блокируются
  на уровне interceptor.

---

## Архитектура

    main.py
      └── ProctoringWindow (app_gui.py)
           ├── ExamPage (QWebEnginePage) - allowlist навигации
           ├── LocalServer (dns_proxy.py) - /ready, /exam, /blocked
           ├── AllowlistInterceptor - блокировка URL на уровне запросов
           ├── CVEngine (cv_engine.py)
           │    ├── FaceMesh (MediaPipe) - в GUI-потоке
           │    └── YoloWorker (threading.Thread) - детекция объектов
           ├── SecurityEngine (security_engine.py)
           │    ├── pynput.keyboard.Listener - Win32 hook
           │    ├── QTimer(500ms) - очистка буфера
           │    └── ForegroundInspector - детекция Проводника/Диспетчера
           ├── EventBus (core.py) - SimpleQueue между потоками
           └── Session (core.py) - Trust Score, отчёт

Все обращения к Qt-виджетам - только из GUI-потока. Фоновые потоки
кладут данные в EventBus, QTimer их читает.

---

## Лицензия

MIT - см. файл LICENSE.

---

## Авторы

Учебный проект для хакатона.

- Aron - https://github.com/Aro000on

---

## Acknowledgments

- MediaPipe - Face Mesh: https://developers.google.com/mediapipe
- Ultralytics YOLOv8: https://github.com/ultralytics/ultralytics
- PyQt6: https://www.riverbankcomputing.com/software/pyqt/
- pynput: https://github.com/moses-palmer/pynput

---

## Ограничение ответственности

Это учебный проект для хакатона. Он демонстрирует техническую возможность
локального прокторинга, но НЕ является сертифицированной системой
контроля знаний.

События (LOOK_AWAY, PHONE_DETECTED, MULTIPLE_FACES) - эвристические
и требуют проверки человеком. Trust Score не является вероятностью обмана.

Не используйте проект для реальных экзаменов без:

- согласия экзаменуемых на обработку данных
- независимой валидации эвристик