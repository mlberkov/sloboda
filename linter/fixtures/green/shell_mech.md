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

**Конец хода:** нужно слово владельца — подтвердить проект и регион из выдачи выше либо назвать другие.
