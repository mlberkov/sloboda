# RED · run_id_filter

Пакет закрыт, владельцу выдан блок сверки прогона на ветке, где живут и push,
и `workflow_dispatch`.

## Ваши действия

1. Прочитать вердикт последнего прогона и прислать вывод целиком:

```bash
gh run list --workflow CI --branch main --limit 1 --json databaseId,conclusion
gh run view "$(gh run list --workflow CI --limit 1 --json databaseId | jq -r '.[0].databaseId')" --log-failed
```

Ожидаемо: одна строка вердикта — фактический вывод на предмете, прогон оркестратора 2026-08-31.

**Конец хода:** нужно слово владельца — назвать, какой вердикт он видит у себя.
