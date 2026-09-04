# GREEN · env_presupposition

Тот же предмет: каждая предпосылка измеряется строкой выше той, которая на неё
опирается.

```bash
python3 -m venv .venv
./.venv/bin/pip install pyyaml
gcloud run services list --region europe-west1
gcloud run services describe theygrow-web --region europe-west1 --format='value(status.url)'
adb devices
adb -s "$(adb devices | awk 'NR==2{print $1}')" shell getprop ro.build.version.release
```

Тот же предмет на правах токена: набор прав измеряется до того, как на него
опираются, — и push файла workflow, и добавление права идут после чтения.

```bash
gh auth status
git add .github/workflows/ci.yml
git commit -m "гейт: лимит прогона"
git push
gh auth refresh -s workflow
```

**Конец хода:** нужно слово владельца — назвать, какой из перечисленных сервисов является предметом пакета.
