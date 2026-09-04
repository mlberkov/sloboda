# GREEN · shell_mech

Handoff for shell — тот же предмет, собранный по вопросам (1) и (4) «Исполнимости shell-блока».

```bash
set -euo pipefail
PROJECT="$(gcloud config get-value project)"
REGION="$(gcloud config get-value run/region)"
printf 'PROJECT=%s REGION=%s\n' "$PROJECT" "$REGION"
printf 'ветка: %s\n' "$(git rev-parse --abbrev-ref HEAD)"
printf 'дерево чисто: %s\n' "$(git status --porcelain | wc -l)"
```

Тот же коммит вольта, что упал 2026-08-31, собранный по формам того же дня: диск
адресован путём WSL-вида, шаги сцеплены `&&` — провал любого останавливает остаток.

```bash
cd /mnt/d/Obsidian/TheyGrow && git add -A && git commit -m "canon: правки дня" && git push && git log --oneline -1
```

Тот же блок выдачи пакета, собранный так, что отказ доходит до владельца: код
выхода самой команды печатается явно, а вторая команда фильтрует свой отказ по
слову, а не по позиции строки.

```bash
git push origin HEAD 2>&1 | tail -3; echo "push rc=${PIPESTATUS[0]}"
gh pr create --fill 2>&1 | grep -Ei 'error|rejected' || echo "строк error/rejected нет"
```

**Конец хода:** нужно слово владельца — подтвердить проект и регион из выдачи выше либо назвать другие.
