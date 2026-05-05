# ClipboardHistory Audit

Дата актуализации: 2026-05-06

Назначение файла: forward-looking backlog по проекту. Здесь должны оставаться только реальные открытые баги, edge cases, риски и улучшения, которые еще нужно сделать. Закрытые задачи не переносить в этот файл; историю уже сделанного смотреть через `git log` / `git show`.

## Текущий фокус

1. Укрепить UX вокруг file clipboard и truncated text.
2. Добавить privacy controls для clipboard manager сценариев.
3. Продолжить вынос чистой логики в тестируемые helpers без лишних дубликатов.

## Открытые находки

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

Приложение автоматически сохраняет clipboard text/images в SQLite под `%APPDATA%`. Для clipboard manager это ожидаемо, но без denylist приложений, настройки retention и более широкой privacy policy пользователь может случайно сохранить секреты. Runtime pause recording и явная сильная очистка уже покрывают базовые сценарии, но не заменяют app-aware controls.

Что сделать:
- Обсудить retention settings для unpinned entries.
- Рассмотреть app/process denylist.
- Проверить, как privacy controls взаимодействуют с ignore-next при paste.

### CH-AUDIT-008 - Тестовый каркас еще не покрывает GUI/Win32 edge cases

Приоритет: P3.

Storage, image helper logic, popup preview positioning, autostart command handling, paste result flow, runtime status, clipboard retry, Python compatibility и single-instance startup уже имеют базовые `unittest` tests. Остаются более широкие GUI/manual smoke checks для реального Win32 clipboard/tray поведения.

Что сделать:
- Для Win32 clipboard оставить manual smoke checklist, если автоматизация окажется слишком тяжелой.

## Проверки для будущих правок

- `python -m unittest discover -s tests`
- `python -m compileall -q main.pyw app tests`
- `python -m ruff check .`
- `git diff --check`

## Сводка для следующей сессии

1. File clipboard policy: полноценный files support или честный text-path режим (`CH-AUDIT-020`).
2. Truncated long-text policy (`CH-AUDIT-018`).
3. Remaining privacy controls: retention settings, app/process denylist, broader policy (`CH-AUDIT-009`).
4. Дополнительные GUI/manual smoke checks для реального Win32 clipboard/tray поведения (`CH-AUDIT-008`).
