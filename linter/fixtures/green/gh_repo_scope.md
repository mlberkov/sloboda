# GREEN · gh_repo_scope

Тот же предмет: после ухода в каталог-черновик каждый `gh` несёт `--repo`.

## Ваши действия

1. Выгрузить ассеты релиза и прислать перечень файлов:

```bash
set -e
cd /tmp
gh release download v0.1.221 --repo theygrow/app -D /tmp/rel-0-1-221
gh run list --repo theygrow/app --workflow CI --event push --commit HEAD --json databaseId
```

Ожидаемо: перечень файлов и одна строка прогона — фактический вывод на предмете, измерено оркестратором 2026-08-31.

**Конец хода:** нужно слово владельца — прислать перечень выгруженного.
