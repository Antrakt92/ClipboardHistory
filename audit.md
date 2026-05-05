# ClipboardHistory Audit

Дата аудита: 2026-05-05

Цель: полный проход по проекту с фокусом на реальные баги, edge cases, ошибки логики, мертвый код, дублирование, надежность, UX и следующие инженерные шаги. В этот файл попали только находки, которые были проверены по коду или воспроизведены локально; спорные идеи вынесены в низкоприоритетные улучшения.

## Проверенный охват

- Точка входа и жизненный цикл: `main.pyw`.
- Мониторинг буфера: `app/clipboard_monitor.py`.
- SQLite-хранилище: `app/database.py`.
- Popup UI, поиск, навигация, pin/delete/clear/paste: `app/popup_window.py`.
- Paste engine и Win32 `SendInput`: `app/paste_engine.py`.
- Global hotkey: `app/hotkey_manager.py`.
- Tray icon и autostart: `app/tray_icon.py`, `app/autostart.py`.
- Конфигурация, миграция DB path, icon generation, README, requirements.

## Верификация

- `python -m compileall -q main.pyw app` - проходит.
- `python -m ruff check .` - проходит.
- Smoke-проверка `Database(temp_path)` на свежем файле - падает с `sqlite3.OperationalError: database table is locked`; это подтверждает находку CH-AUDIT-001.
- Расчет raw DIB размеров для типовых скриншотов подтверждает, что 1080p+ скриншоты превышают текущий 5 MiB лимит до PNG-конвертации; это подтверждает CH-AUDIT-003.

## Критичные и высокие находки

### CH-AUDIT-001 - Первый запуск/пересоздание базы может падать на `wal_checkpoint`

Статус: подтверждено локальным smoke-тестом.
Приоритет: P0.

`Database.__init__` выполняет `PRAGMA wal_checkpoint(TRUNCATE)` до `_create_tables()` (`app/database.py:21-26`). После `_open_or_recreate()` на свежем temp DB вызов `Database(path)` стабильно падает на строке 25 с `sqlite3.OperationalError: database table is locked`. Изолированная проверка показала, что создание таблицы перед checkpoint снимает проблему.

Пользовательский эффект: на чистой установке, при удаленной DB или после пересоздания поврежденной DB приложение может не стартовать вообще.

Рекомендуемое исправление:
- Перенести `wal_checkpoint(TRUNCATE)` после `_create_tables()`/`_migrate()` и commit.
- Обернуть startup checkpoint в безопасный `try/except sqlite3.DatabaseError` с логированием, как уже сделано в `close()`.
- Добавить регрессионный тест: `Database(temp_path)` должен стартовать, создать таблицу, принимать запись и закрываться.

### CH-AUDIT-002 - Текст из clipboard сохраняется не точно: теряются leading/trailing пробелы и переводы строк

Статус: подтверждено по цепочке чтения.
Приоритет: P1.

`ClipboardMonitor` передает исходный `text_content` без обрезки (`app/clipboard_monitor.py:179-202`), но `ClipboardHistoryApp._on_clipboard_change()` сохраняет `content.strip()` (`main.pyw:92-96`). В результате запись вроде `"  value  "`, текст с ведущим отступом или завершающей newline будет вставлен уже измененным.

Пользовательский эффект: clipboard history должен возвращать то, что было скопировано. Сейчас он ломает точность для кода, markdown, терминальных команд, паролей/токенов с пробелами, шаблонов с финальной newline и любых whitespace-sensitive фрагментов.

Рекомендуемое исправление:
- Использовать `content.strip()` только для проверки пустоты, а в `db.add_entry()` передавать исходный `content`.
- Проверить, что preview продолжает обрезать пробелы только для отображения, но full content остается неизменным.
- Добавить регрессионный тест на leading spaces, trailing spaces и trailing newline.

### CH-AUDIT-003 - Большинство полноэкранных скриншотов 1080p+ silently не сохраняются

Статус: подтверждено расчетом и кодом.
Приоритет: P1.

В `clipboard_monitor._read_clipboard()` лимит `MAX_IMAGE_BYTES = 5 MiB` применяется к raw CF_DIB до PNG-конвертации (`app/clipboard_monitor.py:190-196`, `app/config.py:39`). Raw DIB для 1920x1080 32-bit занимает примерно 7.91 MiB, для 2560x1440 - примерно 14.06 MiB, для 4K - примерно 31.64 MiB. Поэтому обычные скриншоты экрана на 1080p и выше будут пропущены еще до `_dib_to_png()`, хотя итоговый PNG часто может быть существенно меньше.

Пользовательский эффект: README обещает screenshots/copied images (`README.md:10-12`), но типичный fullscreen screenshot на современном мониторе не попадет в историю без ошибки в UI.

Рекомендуемое исправление:
- Разделить лимит на raw input safety limit и stored PNG limit.
- Конвертировать DIB в PNG, затем проверять размер сохраняемого PNG.
- Для защиты памяти добавить отдельный лимит по пикселям или более высокий raw limit с логированием причины пропуска.

## Средние находки

### CH-AUDIT-004 - UI показывает только 30 записей из 500, старые элементы фактически недоступны

Статус: подтверждено по коду и README.
Приоритет: P2.

База хранит до `MAX_HISTORY_SIZE = 500` (`app/config.py:36`), README обещает "up to 500 entries" (`README.md:21`), но popup всегда вызывает `self.db.get_history(limit=30, ...)` (`app/popup_window.py:357-374`). Пагинации, infinite scroll, "load more" или настройки лимита нет. Поиск тоже ограничен 30 результатами после фильтрации.

Пользовательский эффект: часть истории хранится, занимает место и участвует в cleanup, но пользователь не может нормально просмотреть и выбрать большую часть записей.

Рекомендуемое исправление:
- Добавить lazy loading через `offset` при скролле вниз или кнопку "load more".
- Показывать общий count отдельно от currently loaded count.
- Проверить UX для pinned entries: pinned должны оставаться сверху без скрытия остальной истории.

### CH-AUDIT-005 - Image preview может уходить за экран на узких/малых work area

Статус: подтверждено арифметикой позиционирования.
Приоритет: P2.

`_show_image_preview()` выбирает preview справа от popup, иначе слева (`app/popup_window.py:605-626`). Если popup шириной 520 и preview около 300 не помещаются одновременно в work area, код выбирает левую позицию `popup_x - pw - 8` без clamp к `ml`. На work area около 800 px это легко дает отрицательный `px` или выход за левую границу.

Пользовательский эффект: на маленьких экранах, remote desktop, portrait/vertical layouts или при крупном scaling hover-preview может быть частично или полностью недоступен.

Рекомендуемое исправление:
- После выбора стороны clamp-ить `px` в диапазон `[ml + margin, mr - pw - margin]`.
- Если места мало, показывать preview поверх popup/ниже курсора с тем же clamp.
- Добавить ручную проверку на малой ширине и multi-monitor с отрицательными координатами.

### CH-AUDIT-006 - Autostart menu считает любой registry value валидным

Статус: подтверждено по коду.
Приоритет: P2.

`is_autostart_enabled()` возвращает `True`, если значение `AUTOSTART_NAME` существует (`app/autostart.py:19-24`), но не проверяет, что оно указывает на текущие `pythonw` и `SCRIPT_PATH` (`app/autostart.py:28-33`). После переноса папки проекта, смены Python или ручного изменения registry меню будет показывать включенный autostart, хотя запуск может быть сломан.

Пользовательский эффект: пользователь видит checked "Start with Windows", но приложение не стартует после логина. Первый toggle выключит stale entry вместо исправления.

Рекомендуемое исправление:
- Сравнивать registry command с ожидаемой командой или хотя бы проверять наличие текущего `SCRIPT_PATH`.
- При stale command считать autostart disabled/needs repair и при включении перезаписывать значение.

### CH-AUDIT-007 - Ошибки Win32 listener/hotkey видны только в логах или вообще не видны пользователю

Статус: подтверждено по коду.
Приоритет: P2.

Если global hotkey не зарегистрировался, приложение только пишет warning (`main.pyw:73-77`) и продолжает работу в tray. Если `AddClipboardFormatListener()` вернет `False`, результат игнорируется (`app/clipboard_monitor.py:138`), и приложение может выглядеть запущенным, но не записывать clipboard changes.

Пользовательский эффект: пользователь не понимает, почему `Ctrl+Shift+V` не работает или история не пополняется.

Рекомендуемое исправление:
- Проверять return value `AddClipboardFormatListener()`, логировать `GetLastError()` и пробрасывать состояние наверх.
- В tray menu/title или popup показывать compact status для "hotkey unavailable" и "clipboard listener unavailable".
- Добавить retry/backoff для listener registration, если это безопасно.

## Низкоприоритетные проблемы и улучшения

### CH-AUDIT-008 - Нет тестового каркаса для критичной логики

Статус: подтверждено по структуре repo.
Приоритет: P3.

В репозитории нет `tests/`, CI или минимальных unit tests. При этом есть логика, которую можно тестировать без GUI: SQLite lifecycle, deduplication, cleanup, expiration, text preservation, DIB header conversion, autostart command building. Предыдущие аудиты уже правили много x64/Win32 edge cases, значит regression coverage здесь особенно ценна.

Рекомендуемое исправление:
- Добавить `pytest` и первые тесты для `Database`.
- Вынести чистые функции для autostart command normalization и preview positioning, чтобы тестировать без реального tray/Tk.
- Для Win32 clipboard оставить smoke/manual checklist, если автоматизация слишком тяжелая.

### CH-AUDIT-009 - Clipboard privacy controls отсутствуют

Статус: продуктовый риск, подтверждено по функциональности.
Приоритет: P3.

Приложение сохраняет clipboard history в SQLite BLOB/TEXT под `%APPDATA%` (`app/config.py:10-13`) и автоматически пишет все текстовые и image clipboard events. Для clipboard manager это ожидаемо, но без pause mode, denylist приложений, "do not save passwords", quick clear и настройки retention пользователь легко сохранит секреты случайно.

Рекомендуемое улучшение:
- Добавить tray action "Pause recording".
- Добавить clear all including pinned или отдельную команду secure wipe.
- Рассмотреть app/process denylist и/или короткий retention для unpinned entries.

### CH-AUDIT-010 - Мелкий мертвый/лишний код

Статус: подтверждено ручным просмотром; `ruff` текущей конфигурацией не ругается.
Приоритет: P3.

- `ACCENT_DIM` объявлен, но не используется (`app/popup_window.py:33`).
- `WM_DESTROY` объявлен, но не используется (`app/clipboard_monitor.py:48`).
- `self._master` сохраняется, но дальше не читается (`app/popup_window.py:118`).

Рекомендуемое исправление: удалить при ближайшем touching этих файлов, чтобы не делать отдельный шумный commit.

## Проверено и не подтвердилось как текущая проблема

- x64 HWND/LRESULT truncation: основные `restype`/`argtypes` для HWND/LRESULT в `main.pyw`, `clipboard_monitor.py`, `popup_window.py`, `paste_engine.py` уже выставлены.
- SQLite wildcard injection в search: `%`, `_` и `\` экранируются в `Database.get_history()` (`app/database.py:175-185`).
- Duplicate paste race: `PasteEngine.paste()` ставит `monitor.set_ignore_next()` до записи clipboard и сбрасывает флаг при ошибке записи (`app/paste_engine.py:73-91`).
- Tray icon file handle leak: icon загружается через `BytesIO` и `image.load()` до передачи в `pystray` (`app/tray_icon.py:22-53`).
- DB access из разных потоков: публичные операции `Database` используют общий `threading.Lock`; явных unlocked writes вне `_cleanup_unlocked()`/`_maybe_expire()` не найдено, а эти методы вызываются из уже удерживаемого lock.

## Задачи на следующую сессию

1. Исправить CH-AUDIT-001: перенести/защитить startup `wal_checkpoint`, добавить тест `Database(temp_path)` на свежей DB и на recreated DB.
2. Исправить CH-AUDIT-002: сохранять исходный текст clipboard без `.strip()`, добавить регрессионные тесты на whitespace-sensitive content.
3. Исправить CH-AUDIT-003: переработать image size limits так, чтобы 1080p/1440p screenshots сохранялись, но память была защищена.
4. Спроектировать доступ к полной истории: pagination/lazy load для 500 записей, корректный count, проверка pinned + search UX.
5. Укрепить Win32 status handling: visible warning для hotkey conflict и listener registration failure.
6. Исправить preview positioning clamp для маленьких work area и multi-monitor layouts.
7. Починить stale autostart detection: проверять registry value против текущего `SCRIPT_PATH`/команды.
8. Добавить минимальный `pytest` каркас для Database и чистых helper-функций.
9. Обсудить/добавить privacy controls: pause recording, stronger clear, retention настройки, возможно app denylist.
10. При ближайших правках удалить мелкий мертвый код из CH-AUDIT-010.
