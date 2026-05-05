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

- `python -m unittest discover -s tests` - проходит.
- `python -m compileall -q main.pyw app tests` - проходит.
- `python -m ruff check .` - проходит.
- `git diff --check` - проходит.
- Историческая smoke-проверка `Database(temp_path)` на свежем файле падала с `sqlite3.OperationalError: database table is locked`; это подтвердило CH-AUDIT-001, исправлено 2026-05-05.
- Расчет raw DIB размеров для типовых скриншотов подтверждает, что 1080p+ скриншоты превышают текущий 5 MiB лимит до PNG-конвертации; это подтверждает CH-AUDIT-003.
- Исторический deep probe с объектом `Database`, созданным в обход startup-багa, подтвердил silent truncation + false dedup для разных текстов длиннее `MAX_CONTENT_LENGTH`; это подтвердило CH-AUDIT-011, исправлено 2026-05-05.
- Static/order probe подтвердил, что `touch_entry()` вызывается до paste, а результат `SendInput()` игнорируется; это подтверждает CH-AUDIT-012.
- Исторический probe `_maybe_vacuum()` с failing connection подтвердил, что retry flag сбрасывался до успешного `VACUUM`; это подтвердило CH-AUDIT-014, исправлено 2026-05-05.
- Реализация 2026-05-05 добавила `unittest` regression suite для fresh DB startup, legacy migration, corrupt DB quarantine/recreate, exact whitespace preservation, long-text hash dedup, failed/successful `VACUUM` и side-effect-free `config` import.
- Новый probe 2026-05-05 подтвердил, что hourly `_maybe_expire()` удаляет старые rows, но не выставляет `_needs_vacuum`; это подтверждает CH-AUDIT-017.
- Новый probe 2026-05-05 подтвердил, что search по хвосту truncated long text не находит запись, хотя `content_len` показывает исходную длину; это подтверждает CH-AUDIT-018.

## Критичные и высокие находки

### CH-AUDIT-001 - Первый запуск/пересоздание базы может падать на `wal_checkpoint`

Статус: исправлено 2026-05-05; покрыто `tests/test_database.py`.
Приоритет: P0.

`Database.__init__` выполняет `PRAGMA wal_checkpoint(TRUNCATE)` до `_create_tables()` (`app/database.py:21-26`). После `_open_or_recreate()` на свежем temp DB вызов `Database(path)` стабильно падает на строке 25 с `sqlite3.OperationalError: database table is locked`. Изолированная проверка показала, что создание таблицы перед checkpoint снимает проблему.

Пользовательский эффект: на чистой установке, при удаленной DB или после пересоздания поврежденной DB приложение может не стартовать вообще.

Рекомендуемое исправление:
- Перенести `wal_checkpoint(TRUNCATE)` после `_create_tables()`/`_migrate()` и commit.
- Обернуть startup checkpoint в безопасный `try/except sqlite3.DatabaseError` с логированием, как уже сделано в `close()`.
- Добавить регрессионный тест: `Database(temp_path)` должен стартовать, создать таблицу, принимать запись и закрываться.

### CH-AUDIT-002 - Текст из clipboard сохраняется не точно: теряются leading/trailing пробелы и переводы строк

Статус: исправлено 2026-05-05; покрыто `tests/test_database.py`.
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

### CH-AUDIT-011 - Длинный текст тихо обрезается и может ложно дедуплицироваться

Статус: исправлено 2026-05-05; покрыто `tests/test_database.py`.
Приоритет: P1.

`Database.add_entry()` сначала делает `content = content[:MAX_CONTENT_LENGTH]`, а уже потом сравнивает запись с последним элементом (`app/database.py:114-125`). Probe с двумя разными строками длиной `MAX_CONTENT_LENGTH + 1`, где различается только последний символ, дал `add a True`, `add b False`, `rows 1`, `stored_len 50000`.

Пользовательский эффект: если пользователь копирует большой текст, приложение без предупреждения сохранит только первые 50k символов. Если затем скопировать другой большой текст с тем же первым 50k-префиксом, он вообще не попадет в историю как "duplicate". При paste из истории пользователь получит неполные данные, хотя UI не показывает, что запись была усечена.

Рекомендуемое исправление:
- Хранить `original_content_len` и `truncated` flag.
- Делать dedup по hash исходного content до truncation или хранить hash отдельно.
- Показывать в UI явный индикатор truncated content и не обещать paste полной записи, если она была обрезана.
- Рассмотреть повышение лимита или настройку лимита для power users.

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

### CH-AUDIT-012 - Paste считается использованным до фактического успеха, а `SendInput` failure игнорируется

Статус: подтверждено по порядку вызовов.
Приоритет: P2.

`PopupWindow._on_item_click()` вызывает `self.db.touch_entry(entry_id)` до `self.paste_engine.paste(...)` (`app/popup_window.py:743-759`). При этом `PasteEngine._focus_and_press()` вызывает `user32.SendInput(...)`, но не проверяет возвращенное количество вставленных input events (`app/paste_engine.py:107-121`). `paste()` также не возвращает success/failure наверх.

Пользовательский эффект: запись двигается наверх как использованная даже если clipboard write, foreground switch или actual `Ctrl+V` не сработали. Для elevated apps, secure desktops, UAC prompts, blocked focus changes или любых SendInput failures пользователь видит "история сработала", но paste мог не произойти.

Рекомендуемое исправление:
- Сделать paste pipeline возвращающим результат хотя бы для clipboard write и `SendInput` count.
- Проверять, что `SendInput()` вернул `4`; если нет, логировать `GetLastError()` и показывать compact failure state.
- Вызывать `touch_entry()` после подтвержденного paste attempt или хранить отдельное `last_selected_at` вместо перемещения по timestamp.

### CH-AUDIT-013 - Clipboard read silently drops events, если clipboard занят дольше примерно 100 ms

Статус: подтверждено по коду retry loop.
Приоритет: P2.

`ClipboardMonitor._read_clipboard()` делает 3 попытки `OpenClipboard()` с двумя паузами по 50 ms и на третьей ошибке просто `return` без логирования и без deferred retry (`app/clipboard_monitor.py:164-173`). Это отличается от paste-side `_open_clipboard_retry()`, где хотя бы пишется warning (`app/paste_engine.py:59-69`).

Пользовательский эффект: некоторые clipboard updates будут потеряны полностью, если источник или другое приложение держит clipboard чуть дольше. Такое бывает при больших изображениях, Office/Adobe apps, remote desktop, clipboard sync tools. Пользователь не увидит ошибку и может думать, что история ненадежна случайным образом.

Рекомендуемое исправление:
- Логировать final failure с throttling, чтобы не зашумлять логи.
- Добавить deferred retry через короткий timer/backoff, пока update еще актуален.
- Рассмотреть сохранение "missed clipboard update" status в tray/popup diagnostics.

## Низкоприоритетные проблемы и улучшения

### CH-AUDIT-008 - Нет тестового каркаса для критичной логики

Статус: частично исправлено 2026-05-05: добавлен `unittest` каркас для `Database`; GUI/Win32 tests еще отсутствуют.
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

### CH-AUDIT-014 - `VACUUM` retry flag сбрасывается до успешного выполнения

Статус: исправлено 2026-05-05; покрыто `tests/test_database.py`.
Приоритет: P3.

`Database._maybe_vacuum()` делает `self._needs_vacuum = False` до входа в lock и до `self.conn.execute("VACUUM")` (`app/database.py:297-313`). Если `VACUUM` не выполнится, exception логируется как debug, но `_needs_vacuum` уже потерян. Probe с connection, где `execute("VACUUM")` бросает exception, завершился `needs_vacuum_after False`.

Пользовательский эффект: после удаления image blobs база может не уменьшиться и больше не попробует reclaim space до следующего delete/cleanup, который снова выставит `_needs_vacuum = True`.

Рекомендуемое исправление:
- Сбрасывать `_needs_vacuum` только после успешного `VACUUM`.
- При ошибке оставлять flag включенным, но обновлять `_last_vacuum_time`, чтобы не retry-ить слишком часто.

### CH-AUDIT-015 - Corruption recovery может вернуть все еще corrupt DB, если удаление файла не удалось

Статус: исправлено 2026-05-05; покрыто `tests/test_database.py`.
Приоритет: P2.

В `_open_or_recreate()` при `sqlite3.DatabaseError` код пытается удалить DB/WAL/SHM, но проглатывает любые `OSError` (`app/database.py:43-55`). После этого он всегда делает `return sqlite3.connect(db_path, check_same_thread=False)`, не проверяя, были ли файлы реально удалены и прошел ли новый `integrity_check`.

Пользовательский эффект: если corrupt DB не удаляется из-за lock/permission/AV scanner, приложение может продолжить с тем же поврежденным файлом и упасть позже в `_create_tables()`, `_migrate()` или при первом запросе. Лог при этом будет говорить "recreating", хотя пересоздание могло не состояться.

Рекомендуемое исправление:
- После удаления проверять, что основной файл отсутствует или был успешно replaced.
- Если удалить не удалось, поднимать понятную ошибку или переименовывать corrupt DB в quarantine path.
- После recreate обязательно повторять `PRAGMA integrity_check`.

### CH-AUDIT-016 - `app.config` выполняет миграцию и filesystem writes уже при import

Статус: исправлено 2026-05-05; покрыто `tests/test_database.py`.
Приоритет: P3.

`app/config.py` на top-level создает `%APPDATA%/ClipboardHistory` (`os.makedirs(...)`) и может переместить старую DB из repo root (`shutil.move(...)`) просто при импорте модуля (`app/config.py:10-30`). Этот модуль импортируется многими частями приложения и любыми будущими tests/tools.

Пользовательский эффект: тесты, линтеры, debug snippets и вспомогательные скрипты могут менять пользовательскую файловую систему или мигрировать DB без явного запуска приложения. Это повышает риск неожиданных side effects и усложняет тестирование.

Рекомендуемое исправление:
- Оставить в `config.py` только вычисление путей.
- Вынести `ensure_data_dir()` и `migrate_legacy_db()` в явный startup step из `main.pyw`.
- В тестах подменять data dir без риска реальной миграции.

### CH-AUDIT-017 - Hourly expiration удаляет rows без vacuum retry marker

Статус: подтверждено локальным probe 2026-05-05.
Приоритет: P3.

После исправления CH-AUDIT-014 startup `_expire_old_entries()` уже выставляет `_needs_vacuum`, если удалил старые rows. Но hourly path `_maybe_expire()` (`app/database.py`) все еще выполняет `DELETE FROM clipboard_history WHERE pinned = 0 AND timestamp < ?` и `commit()`, не проверяя `rowcount` и не выставляя `_needs_vacuum`. Probe: старая row была удалена (`remaining_old=0`), но `needs_vacuum=False`.

Пользовательский эффект: при долгой работе приложения старые unpinned entries могут удаляться по retention, но SQLite-файл не будет помечен на reclaim до следующего delete/cleanup, который выставит `_needs_vacuum`.

Рекомендуемое исправление:
- Повторить startup pattern: сохранить cursor из `DELETE`, после commit при `cursor.rowcount > 0` выставить `_needs_vacuum = True`.
- Добавить unit test на hourly `_maybe_expire()` path, отдельно от startup expiration.

### CH-AUDIT-018 - Search не находит хвост truncated long text

Статус: подтверждено локальным probe 2026-05-05.
Приоритет: P3.

Новая модель long-text storage честно хранит `original_content_len` и `truncated`, но `content` остается обрезанным до `MAX_CONTENT_LENGTH`. `get_history(search_query=...)` ищет только по сохраненному `content`/image preview. Probe с `content = "a" * 50000 + "needle"` показал: `truncated=1`, `content_len=50006`, поиск по prefix находит запись, поиск по `"needle"` возвращает 0.

Пользовательский эффект: пользователь может помнить и искать фрагмент из скопированного большого текста, но запись не найдется, потому что этот фрагмент находится за пределом storage cap.

Рекомендуемое улучшение:
- Сделать UI-подсказку для truncated entries более явной: "stored first 50,000 chars" или аналогичный короткий статус.
- Рассмотреть отдельную настройку лимита/полное хранение large text, если точный поиск и paste важнее размера DB.
- Не обещать full-text search для truncated entries, пока хвост не хранится.

## Проверено и не подтвердилось как текущая проблема

- x64 HWND/LRESULT truncation: основные `restype`/`argtypes` для HWND/LRESULT в `main.pyw`, `clipboard_monitor.py`, `popup_window.py`, `paste_engine.py` уже выставлены.
- SQLite wildcard injection в search: `%`, `_` и `\` экранируются в `Database.get_history()` (`app/database.py:175-185`).
- Duplicate paste race: `PasteEngine.paste()` ставит `monitor.set_ignore_next()` до записи clipboard и сбрасывает флаг при ошибке записи (`app/paste_engine.py:73-91`).
- Tray icon file handle leak: icon загружается через `BytesIO` и `image.load()` до передачи в `pystray` (`app/tray_icon.py:22-53`).
- DB access из разных потоков: публичные операции `Database` используют общий `threading.Lock`; явных unlocked writes вне `_cleanup_unlocked()`/`_maybe_expire()` не найдено, а эти методы вызываются из уже удерживаемого lock.

## Задачи на следующую сессию

1. Исправить CH-AUDIT-003: переработать image size limits так, чтобы 1080p/1440p screenshots сохранялись, но память была защищена.
2. Исправить CH-AUDIT-017: hourly `_maybe_expire()` должен выставлять `_needs_vacuum` при удалении rows; добавить unit test.
3. Спроектировать доступ к полной истории: pagination/lazy load для 500 записей, корректный count, проверка pinned + search UX.
4. Укрепить Win32 status handling: visible warning для hotkey conflict, listener registration failure, missed clipboard reads и failed paste.
5. Исправить paste result flow: проверять `SendInput` count, не делать `touch_entry()` до подтвержденного paste attempt.
6. Исправить preview positioning clamp для маленьких work area и multi-monitor layouts.
7. Починить stale autostart detection: проверять registry value против текущего `SCRIPT_PATH`/команды.
8. Улучшить truncated long-text UX/search policy: явно показывать storage cap и решить, нужен ли full storage/search для длинных текстов.
9. Обсудить/добавить privacy controls: pause recording, stronger clear, retention настройки, возможно app denylist.
10. Расширить тесты за пределы `Database`: autostart command normalization, preview positioning helper, clipboard/paste smoke checklist.
11. При ближайших правках удалить мелкий мертвый код из CH-AUDIT-010.
