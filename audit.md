# ClipboardHistory Audit

Дата актуализации: 2026-09-05

Назначение файла: forward-looking backlog по проекту. Здесь должны оставаться только реальные открытые баги, edge cases, риски и улучшения, которые еще нужно сделать. Закрытые задачи не переносить в этот файл; историю уже сделанного смотреть через `git log` / `git show`.

## Текущий фокус

1. Укрепить UX вокруг file clipboard и уведомлений об отмене paste.
2. Добавить privacy controls для clipboard manager сценариев.
3. Продолжить Windows smoke-проверки clipboard/paste и вынести значительную compaction большой image-history из критического пути.

## Открытые находки

### CH-AUDIT-020 - File clipboard сохраняется как текстовые пути, но не paste-ится обратно как файлы

Приоритет: P3.

Когда clipboard содержит `CF_HDROP` и нет text content, monitor сохраняет список путей как newline-joined text. При выборе такой записи paste engine кладет в clipboard `CF_UNICODETEXT`, а не `CF_HDROP`, поэтому в Explorer/файловых менеджерах это не восстановит исходный file-copy operation. README описывает ограничение; в popup отдельной маркировки file paths пока нет.

Что сделать:
- Решить продуктовую политику: поддерживать file entries как отдельный `content_type="files"` или не записывать `CF_HDROP`.
- Если поддерживать files, хранить структурированный список путей и paste-ить обратно через `CF_HDROP`/DROPFILES.
- Если оставлять как text paths, явно маркировать preview/status как "file paths" и не создавать ожидание file paste.
- Учесть privacy: file paths могут раскрывать имена проектов, пользователей и документов.

### CH-AUDIT-009 - Remaining app-aware privacy и настройки retention

Приоритет: P3.

Приложение сохраняет clipboard text/images в SQLite под `%APPDATA%`. Pause recording и Windows history opt-out markers предотвращают часть нежелательных записей, но приложения не обязаны предоставлять markers. Нет denylist приложений и настройки retention: сейчас unpinned expiry фиксирован на 30 днях, pinned записи не истекают. Очистка удаляет записи, но не обещает безопасное стирание свободных страниц, резервных и quarantined копий базы.

Что сделать:
- Обсудить retention settings для unpinned entries.
- Рассмотреть app/process denylist.
- Проверить, как privacy controls взаимодействуют с ignore-next при paste.

### CH-AUDIT-008 - Тестовый каркас еще не покрывает GUI/Win32 edge cases

Приоритет: P3.

Storage, image helper logic, popup preview positioning, autostart command handling, paste result flow, runtime status, clipboard retry, Python compatibility и single-instance startup уже имеют базовые `unittest` tests. Остаются более широкие GUI/manual smoke checks для реального Win32 clipboard/tray поведения.

Что сделать:
- Для Win32 clipboard оставить manual smoke checklist, если автоматизация окажется слишком тяжелой.
- Для branded autostart проверить имя/значок в заново открытом списке Windows
  Startup apps и вход в Windows. Native-тест проверяет FileDescription, извлечение
  иконки, запуск с Unicode/спецсимволами и отсутствие запуска в режиме проверки;
  он не заменяет настоящий logon.

### CH-AUDIT-021 - Значительная compaction большой image-history блокирует UI

Приоритет: P3.

На синтетической базе с 240 MiB BLOB удаление половины записей вызывает `VACUUM` около 1,6 секунды и задерживает конкурентный запрос истории примерно на 1,5 секунды. Compaction выполняется синхронно под database lock, не чаще раза в сутки, когда свободно минимум 32 MiB и 25% страниц. Небольшие удаления больше не запускают полную перезапись базы. Методика и точные значения находятся в `docs/audit-storage-2026-09-03.md`; пользовательская история не читалась.

Полный `PRAGMA integrity_check` при запуске сохранён. В той же синтетической проверке полный open занимал примерно 0,18 секунды; отдельного обоснования для ослабления проверки целостности нет.

Что сделать:
- Спроектировать значительную compaction вне UI/database-read critical path, включая отдельное соединение, конкурентные записи, busy timeout и корректный shutdown.
- Проверить массовую очистку и запись новых изображений во время compaction на синтетических данных.
- Сохранить integrity check, долговечность SQLite и возможность повторного использования свободных страниц.

### CH-AUDIT-022 - Поиск по большой текстовой истории выполняется в UI потоке

Приоритет: P2.

Поиск по большой истории все еще выполняется синхронно в Tk callback. На временной синтетической базе из 500 записей по ~47 500 кириллических символов единый Unicode-запрос page + total занимает около 266 мс; прежний ASCII-only поиск с двумя запросами — около 166 мс на тех же данных. Это крайний текстовый объем; пользовательская база не читалась. Методика и сравнение в `docs/audit-2026-09-05.md`.

Что сделать:
- Вынести поиск из Tk callback с generation/cancellation, чтобы устаревший результат не заменял новый.
- Сохранить единый snapshot page + total и metadata-only выдачу; проверить одновременную запись, удаление, закрытие и повторное открытие popup.
- Не заменять Unicode matching на ASCII-only поиск ради скорости.

### CH-AUDIT-023 - Неудачный или отмененный paste виден только в логе

Приоритет: P2.

Popup скрывается до попытки записи clipboard. Ошибка записи, отказ активации окна, смена clipboard/focus и удержание modifier keys отменяют автоматическую вставку безопасно, но пользователь не получает объяснение: `_on_item_click` и `_handle_paste_completion` только логируют отказ.

Что сделать:
- Добавить краткое не перехватывающее фокус уведомление с различием «не скопировано» и «скопировано, автоматическая вставка отменена».
- Не показывать содержимое clipboard в уведомлении и не открывать popup поверх нового активного окна.
- Согласовать причину отмены в результате paste worker с сообщением и тестами.

## Проверки для будущих правок

- `python -m unittest discover -s tests`
- `python -m compileall -q main.pyw app tests`
- `python -m ruff check .`
- `git diff --check`

## Сводка для следующей сессии

1. File clipboard policy: полноценный files support или честный text-path режим (`CH-AUDIT-020`).
2. Уведомления об отмененном paste (`CH-AUDIT-023`).
3. Remaining privacy controls: retention settings, app/process denylist, broader policy (`CH-AUDIT-009`).
4. Дополнительные GUI/manual smoke checks для реального Win32 clipboard/tray поведения (`CH-AUDIT-008`).
5. Устранение блокировки UI при значительной compaction (`CH-AUDIT-021`).
6. Асинхронный поиск большой текстовой истории (`CH-AUDIT-022`).
