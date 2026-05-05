# ClipboardHistory Audit

Дата актуализации: 2026-05-05

Назначение файла: forward-looking backlog по проекту. Здесь должны оставаться только реальные открытые баги, edge cases, риски и улучшения, которые еще нужно сделать. Закрытые задачи не переносить в этот файл; историю уже сделанного смотреть через `git log` / `git show`.

## Текущий фокус

1. Дать пользователю доступ ко всей сохраненной истории, а не только к первым 30 элементам.
2. Сделать Win32/paste failures видимыми и проверяемыми, чтобы приложение не выглядело рабочим при сломанном listener/hotkey/paste.
3. Укрепить UX вокруг изображений, file clipboard, truncated text и preview positioning.
4. Добавить privacy controls для clipboard manager сценариев.
5. Продолжить вынос чистой логики в тестируемые helpers без лишних дубликатов.

## Открытые находки

### CH-AUDIT-004 - UI показывает только 30 записей из 500

Приоритет: P2.

База хранит до `MAX_HISTORY_SIZE = 500`, README обещает "up to 500 entries", но popup запрашивает только `get_history(limit=30, ...)`. Пагинации, infinite scroll, "load more" или настройки лимита нет. Поиск тоже ограничен 30 результатами после фильтрации.

Что сделать:
- Добавить lazy loading через `offset` при скролле вниз или кнопку "Load more".
- Показывать общий count отдельно от currently loaded count.
- Проверить UX для pinned entries: pinned должны оставаться сверху без скрытия остальной истории.
- Покрыть хотя бы чистую query/pagination часть тестами; UI flow можно оставить manual checklist.

### CH-AUDIT-005 - Image preview может уходить за экран

Приоритет: P2.

`_show_image_preview()` выбирает preview справа от popup, иначе слева. Если popup и preview не помещаются одновременно в work area, левая позиция может уйти за границу экрана. Риск выше на маленьких экранах, remote desktop, portrait layouts, крупном scaling и multi-monitor layouts с отрицательными координатами.

Что сделать:
- Вынести расчет позиции preview в чистый helper.
- После выбора стороны clamp-ить `px` в диапазон work area с margin.
- Если места мало, показывать preview поверх popup/ниже курсора с тем же clamp.
- Добавить тесты helper-а на narrow work area, отрицательные координаты и preview шире доступного места.

### CH-AUDIT-007 - Ошибки Win32 listener/hotkey не видны пользователю

Приоритет: P2.

Если global hotkey не зарегистрировался, приложение только пишет warning и продолжает работу в tray. Если `AddClipboardFormatListener()` вернет `False`, результат сейчас не поднимается в UI. Пользователь не понимает, почему hotkey не работает или история не пополняется.

Что сделать:
- Проверять return value `AddClipboardFormatListener()`.
- Логировать `GetLastError()` с контекстом.
- Пробросить compact status наверх: hotkey unavailable, clipboard listener unavailable.
- Отобразить status в tray menu/title или popup без тяжелого UX.
- Решить, нужен ли retry/backoff для listener registration.

### CH-AUDIT-012 - Paste считается использованным до фактического успеха

Приоритет: P2.

`PopupWindow._on_item_click()` вызывает `touch_entry()` до `paste(...)`. `PasteEngine._focus_and_press()` вызывает `SendInput(...)`, но не проверяет возвращенное количество input events. `paste()` не возвращает success/failure наверх.

Что сделать:
- Сделать paste pipeline возвращающим результат хотя бы для clipboard write и `SendInput` count.
- Проверять, что `SendInput()` вернул `4`; иначе логировать `GetLastError()`.
- Не делать `touch_entry()` до подтвержденного paste attempt.
- Показать compact failure state, если paste не удалось.
- Продумать elevated apps/UAC/secure desktop как expected failure scenarios.

### CH-AUDIT-013 - Clipboard read silently drops events при занятом clipboard

Приоритет: P2.

`ClipboardMonitor._read_clipboard()` делает 3 попытки `OpenClipboard()` с короткими паузами и на последней ошибке просто возвращается без visible status. Некоторые updates могут потеряться, если источник или другое приложение держит clipboard чуть дольше.

Что сделать:
- Логировать final failure с throttling.
- Добавить deferred retry через короткий timer/backoff, пока update еще актуален.
- Не блокировать message loop долгими retry.
- Рассмотреть общий status channel вместе с CH-AUDIT-007.

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

### CH-AUDIT-008 - Тестовый каркас еще не покрывает GUI/Win32 edge cases

Приоритет: P3.

Storage, image helper logic и autostart command handling уже имеют базовые `unittest` tests, но остаются нетестированными preview positioning, paste result flow, hotkey/listener status и clipboard retry policy.

Что сделать:
- Вынести preview positioning helper и покрыть geometry tests.
- Для paste/status flow добавить тестируемые result objects без реального `SendInput`.
- Для Win32 clipboard оставить manual smoke checklist, если автоматизация окажется слишком тяжелой.

### CH-AUDIT-010 - Оставшийся мелкий мертвый/лишний код

Приоритет: P3.

Что удалить при ближайшем touching соответствующих файлов:
- `ACCENT_DIM` объявлен, но не используется.
- `self._master` сохраняется, но дальше не читается.

## Проверки для будущих правок

- `python -m unittest discover -s tests`
- `python -m compileall -q main.pyw app tests`
- `python -m ruff check .`
- `git diff --check`

## Сводка для следующей сессии

1. Full history pagination/lazy loading (`CH-AUDIT-004`).
2. Paste result flow и visible status foundation (`CH-AUDIT-012`, затем `CH-AUDIT-007`).
3. Preview positioning helper (`CH-AUDIT-005`).
4. Clipboard retry/status (`CH-AUDIT-013`).
5. File clipboard policy: полноценный files support или честный text-path режим (`CH-AUDIT-020`).
6. Truncated long-text policy (`CH-AUDIT-018`).
7. Privacy controls (`CH-AUDIT-009`).
8. Дополнительные tests и cleanup (`CH-AUDIT-008`, `CH-AUDIT-010`).
