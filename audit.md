# ClipboardHistory Audit

Дата актуализации: 2026-05-05

Назначение файла: forward-looking backlog по проекту. Здесь должны оставаться только реальные открытые баги, edge cases, риски и улучшения, которые еще нужно сделать. Закрытые задачи не переносить в этот файл; историю уже сделанного смотреть через `git log` / `git show`.

## Текущий фокус

1. Сделать Win32 listener/hotkey/clipboard retry failures видимыми, логируемыми и проверяемыми, чтобы приложение не выглядело рабочим при сломанном listener/hotkey.
2. Синхронизировать заявленную Python compatibility policy с реальным runtime syntax.
3. Укрепить UX вокруг destructive actions, file clipboard и truncated text.
4. Добавить privacy controls для clipboard manager сценариев.
5. Продолжить вынос чистой логики в тестируемые helpers без лишних дубликатов.

## Открытые находки

### CH-AUDIT-007 - Ошибки Win32 listener/hotkey не видны пользователю

Приоритет: P2.

Если global hotkey не зарегистрировался, приложение только пишет warning и продолжает работу в tray. Если `AddClipboardFormatListener()` вернет `False`, результат сейчас не поднимается в UI. Пользователь не понимает, почему hotkey не работает или история не пополняется.

Что сделать:
- Проверять return value `AddClipboardFormatListener()`.
- Логировать `GetLastError()` с контекстом.
- Пробросить compact status наверх: hotkey unavailable, clipboard listener unavailable.
- Отобразить status в tray menu/title или popup без тяжелого UX.
- Решить, нужен ли retry/backoff для listener registration.

### CH-AUDIT-013 - Clipboard read silently drops events при занятом clipboard

Приоритет: P2.

`ClipboardMonitor._read_clipboard()` делает 3 попытки `OpenClipboard()` с короткими паузами и на последней ошибке просто возвращается без visible status. Некоторые updates могут потеряться, если источник или другое приложение держит clipboard чуть дольше.

Что сделать:
- Логировать final failure с throttling.
- Добавить deferred retry через короткий timer/backoff, пока update еще актуален.
- Не блокировать message loop долгими retry.
- Рассмотреть общий status channel вместе с CH-AUDIT-007.

### CH-AUDIT-023 - README обещает Python 3.8+, но runtime уже требует новее

Приоритет: P2.

README заявляет `Python 3.8+`, но `app/paste_engine.py` использует PEP 604 annotations вида `str | None` без `from __future__ import annotations`. На Python 3.8/3.9 такой модуль упадет при импорте на вычислении annotations, то есть приложение не стартует несмотря на заявленную поддержку. В репозитории также нет `pyproject.toml`/CI matrix, которая фиксирует минимальную версию.

Что сделать:
- Выбрать политику: реально поддерживать Python 3.8/3.9 или поднять minimum до Python 3.10+.
- Если сохранять 3.8/3.9, заменить PEP 604 runtime annotations на `typing.Optional[...]` или включить postponed annotations там, где это безопасно.
- Если поднимать minimum, обновить README/badges и добавить явную проверку версии при startup или в dev docs.
- Добавить compatibility check в CI/manual checklist, чтобы новые language features не расходились с README.

### CH-AUDIT-024 - Single-instance mutex failure не обрабатывается как failure

Приоритет: P3.

`main.pyw` вызывает `CreateMutexW(...)` и проверяет только `GetLastError() == ERROR_ALREADY_EXISTS`. Если `CreateMutexW` вернет null handle по другой причине, например access denied к уже существующему named object или редкий Win32 failure, приложение продолжит старт без надежной single-instance защиты. На `quit()` затем вызывается `CloseHandle(_mutex)` даже если handle нулевой. Это edge case, но он находится в самом раннем startup path.

Что сделать:
- Задать `CreateMutexW.argtypes/restype`, проверить null handle сразу после вызова.
- Для `ERROR_ALREADY_EXISTS` оставить quiet exit; для других failures выбрать безопасное поведение: exit с минимальным user-visible/loggable сигналом или fallback lock.
- Не вызывать `CloseHandle` для null/invalid handle.
- Добавить unit-testable helper для single-instance decision logic, чтобы не тестировать это через настоящий глобальный mutex.

### CH-AUDIT-020 - File clipboard сохраняется как текстовые пути, но не paste-ится обратно как файлы

Приоритет: P3.

Когда clipboard содержит `CF_HDROP` и нет text content, monitor сохраняет список путей как newline-joined text. При выборе такой записи paste engine кладет в clipboard `CF_UNICODETEXT`, а не `CF_HDROP`, поэтому в Explorer/файловых менеджерах это не восстановит исходный file-copy operation. README при этом обещает text/images, а file-path capture не описан явно.

Что сделать:
- Решить продуктовую политику: поддерживать file entries как отдельный `content_type="files"` или не записывать `CF_HDROP`.
- Если поддерживать files, хранить структурированный список путей и paste-ить обратно через `CF_HDROP`/DROPFILES.
- Если оставлять как text paths, явно маркировать preview/status как "file paths" и не создавать ожидание file paste.
- Учесть privacy: file paths могут раскрывать имена проектов, пользователей и документов.

### CH-AUDIT-018 - Search не находит хвост truncated long text

Приоритет: P3.

Long text хранит `original_content_len` и `truncated`, но `content` остается обрезанным до `MAX_CONTENT_LENGTH`. `get_history(search_query=...)` ищет только по сохраненному `content`/image preview, поэтому фрагмент за пределом storage cap не находится.

Что сделать:
- Сделать UI-подсказку для truncated entries явнее: например "stored first 50,000 chars".
- Решить продуктовую политику: полный storage/search для long text или честное ограничение текущего cap.
- Если нужен полный поиск, спроектировать хранение хвоста/FTS без раздувания popup queries.
- Не обещать full-text search для truncated entries, пока хвост не хранится.

### CH-AUDIT-009 - Clipboard privacy controls отсутствуют

Приоритет: P3.

Приложение автоматически сохраняет clipboard text/images в SQLite под `%APPDATA%`. Для clipboard manager это ожидаемо, но без pause mode, denylist приложений, quick clear и настройки retention пользователь может случайно сохранить секреты.

Что сделать:
- Добавить tray action "Pause recording".
- Добавить clear all including pinned или отдельную stronger clear command.
- Обсудить retention settings для unpinned entries.
- Рассмотреть app/process denylist.
- Проверить, как privacy controls взаимодействуют с ignore-next при paste.

### CH-AUDIT-022 - `Clear all` удаляет не все записи

Приоритет: P3.

В popup кнопка называется `Clear all`, но `Database.clear_all()` делает `DELETE FROM clipboard_history WHERE pinned = 0`. Pinned записи остаются в базе и UI. Это может быть технически правильной retention policy для pinned items, но текущий текст опасно неоднозначен: пользователь может думать, что очистил всю историю, включая pinned secrets/images.

Что сделать:
- Переименовать текущую кнопку в явный `Clear unpinned` / `Clear history`.
- Добавить отдельную stronger action для удаления including pinned, желательно с более строгим подтверждением.
- После выбора policy обновить README и audit wording про pinned retention.
- Покрыть DB/UI helper tests для clear semantics.

### CH-AUDIT-008 - Тестовый каркас еще не покрывает GUI/Win32 edge cases

Приоритет: P3.

Storage, image helper logic, popup preview positioning, autostart command handling и paste result flow уже имеют базовые `unittest` tests, но остаются нетестированными hotkey/listener status, clipboard retry policy, Python minimum compatibility и single-instance startup edge cases.

Что сделать:
- Для Win32 clipboard оставить manual smoke checklist, если автоматизация окажется слишком тяжелой.
- Добавить lightweight compatibility/static checks для заявленной минимальной Python версии.
- Вынести single-instance startup decision в тестируемый helper.

## Проверки для будущих правок

- `python -m unittest discover -s tests`
- `python -m compileall -q main.pyw app tests`
- `python -m ruff check .`
- `git diff --check`

## Сводка для следующей сессии

1. Visible listener/hotkey status foundation (`CH-AUDIT-007`).
2. Clipboard retry/status (`CH-AUDIT-013`).
3. Python minimum compatibility policy и проверка версии (`CH-AUDIT-023`).
4. Single-instance mutex failure handling (`CH-AUDIT-024`).
5. Clear semantics/privacy controls (`CH-AUDIT-022`, `CH-AUDIT-009`).
6. File clipboard policy: полноценный files support или честный text-path режим (`CH-AUDIT-020`).
7. Truncated long-text policy (`CH-AUDIT-018`).
8. Дополнительные tests для hotkey/listener, clipboard retry, compatibility и startup edge cases (`CH-AUDIT-008`).
