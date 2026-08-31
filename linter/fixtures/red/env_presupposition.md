# RED · env_presupposition

История инцидента (локальная, среда WSL2/Ubuntu, 2026-08-31). Владелец назвал
среду PEP 668 — «системный pip не использовать». Блок ниже был собран до того,
как это состояние было измерено, и упал на предпосылке, а не на предмете:

```bash
pip install --user pyyaml
gcloud run services update theygrow-web --region europe-west1 --min-instances 1
adb -s R58M12ABCDE shell input keyevent 82
```

Провал `pip install --user` в externally-managed-environment неотличим от
«пакета нет»: владелец видит трейсбек установщика, а не ответ про предмет.
