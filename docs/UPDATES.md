# UPDATES — обновление и откат

## Обновление upstream

```bash
python scripts/update_upstream.py            # fetch + checkout pinned SHA + патчи
python scripts/update_upstream.py --ref XYZ  # попытать другую версию
python scripts/check_compatibility.py        # записать новый статус
```

Что делает `update_upstream.py` по каждому проекту:

1. `git fetch origin --tags`;
2. `git reset --hard` + `git clean -fd` (предыдущие патчи снимаются с рабочего дерева);
3. `git checkout --detach <ref>` (по умолчанию pinned commit из manifest);
4. заново применяет патчи;
5. печатает результат по каждому файлу.

После `--ref` на непроверенной версии `check_compatibility.py` вернёт
exit 2 (`untested`) — такую сборку нельзя публиковать как совместимую,
пока не пройдены `tests/` (регрессия маршрутов/вкладок сверяется с
`git show HEAD:` нового коммита).

## Конфликты

- `apply_patches.py` различает состояния: `clean → applied`,
  `already-applied` (skip), `conflict` (требует ручного вмешательства или `--force`).
- Бэкапы исходных файлов создаются перед первым применением:
  `.backups/<timestamp>/<project>/<file>`.

## Откат

```bash
python scripts/uninstall.py                    # reverse-патчи → pristine upstream
python scripts/uninstall.py --restore-backups  # при конфликте взять бэкап
python scripts/uninstall.py --uninstall-package
```

Гарантии (§12):

- восстанавливаются ровно изменённые upstream-файлы (5 файлов, см. manifest);
- `~/.tradingagents/` (настройки, отчёты, журналы, кэш) **не удаляется**;
- расширение не добавляло файлов внутрь upstream-деревьев — после отката
  `git status` чистый, дерево байт-в-байт как на pinned SHA.

Проверка после отката:

```bash
git -C upstream/tradingagents status --porcelain          # пусто
git -C upstream/TradingAgents-GUI status --porcelain      # пусто
```
