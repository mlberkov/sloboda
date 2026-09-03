"""Контракт разрешения путей вольта: только окружение, без умолчаний.

Повод (ADR-053 §1.4, ярус 3; решение владельца 17=в от 2026-09-03): абсолютные
пути машины владельца ушли из репозитория в две переменные окружения
`ALTREGO_VAULT_CLONE` и `ALTREGO_VAULT_MASTER`. Умолчание на путь владельца
здесь опаснее его отсутствия: прогон на чужой машине молча считал бы хэши по
несуществующему либо постороннему каталогу, и «вольт не адресован» стало бы
неотличимо от «вольт прочитан». Поэтому меряется не только то, что переменные
читаются, но и то, что откатываться некуда.

Единица диспозиции — «на прогон»: каждое утверждение исполняется вызовом
`run.main(...)` либо чтением исходника, а не пересказом кода.
"""

from __future__ import annotations

import glob
import os

import run
from tests.conftest import infra_lines, parse_run_output

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GREEN = sorted(glob.glob(os.path.join(ROOT, "linter", "fixtures", "green", "*.md")))


def unset(monkeypatch) -> None:
    monkeypatch.delenv(run.ENV_CLONE, raising=False)
    monkeypatch.delenv(run.ENV_MASTER, raising=False)


# ───────────────────────── источник и отсутствие отката ──────────────────────

def test_env_names_are_the_declared_ones():
    assert (run.ENV_CLONE, run.ENV_MASTER) == ("ALTREGO_VAULT_CLONE",
                                               "ALTREGO_VAULT_MASTER")


def test_no_hardcoded_vault_fallback():
    """Отката нет ни константой, ни литералом в исходнике.

    Статическое свойство файла, поэтому и меряется статически — тем же приёмом,
    что контракт имени job'а в tests/test_ci_contract.py.
    """
    assert not hasattr(run, "DEFAULT_CLONE")
    with open(os.path.join(ROOT, "run.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "vaults/" not in source
    assert "Obsidian" not in source


def test_empty_env_is_the_same_as_unset(monkeypatch):
    monkeypatch.setenv(run.ENV_CLONE, "   ")
    assert run.vault_clone() is None


def test_env_is_the_only_source(vault_pair, monkeypatch, full_run):
    """Конфиг путей не несёт: посторонний `vault.clone_path` ничего не значит."""
    config = vault_pair.config()
    config["vault"]["clone_path"] = os.path.join(vault_pair.base, "нет-такого")
    config["vault"]["master_path"] = os.path.join(vault_pair.base, "нет-такого")
    code, seen, _ = full_run(config)
    assert (code, seen["reds"], seen["warnings"]) == (0, [], [])


# ──────────────────────────── диспозиция без вольта ──────────────────────────

def test_fast_is_green_with_both_unset(monkeypatch, capsys):
    """`--fast` вольта не касается: гейт CI идёт ровно так."""
    unset(monkeypatch)
    assert run.main(["--fast", *GREEN, "--no-report"]) == 0
    assert "- красных: 0" in capsys.readouterr().out


def test_full_without_env_is_red_not_green(monkeypatch, capsys):
    """`--full` без переменных краснеет vault_env_unset, а не зеленеет."""
    unset(monkeypatch)
    assert run.main(["--full", "--no-report"]) == 1
    seen = parse_run_output(capsys.readouterr().out)
    (red,) = infra_lines(seen["reds"], "vault_env_unset")
    assert run.ENV_CLONE in red
    (warn,) = infra_lines(seen["warnings"], "vault_env_unset")
    assert run.ENV_MASTER in warn


def test_full_without_env_recomputes_nothing(monkeypatch, capsys):
    """Хэши не считаются ни по какому пути: каждое правило — UNRESOLVED.

    «Не измерено», а не «ноль расхождений»: пустая выдача при отсутствии опоры
    отрицательным результатом не является (§11, R-VACUUM-007).
    """
    unset(monkeypatch)
    run.main(["--full", "--no-report"])
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("- реестр ↔ вольт:"))
    assert "[unresolved]" in line
    assert "registry-drift" not in line and "scenario-drift" not in line


def test_message_names_the_variable_not_an_empty_path(vault_pair, monkeypatch,
                                                      full_run):
    """Клон не адресован — в тексте канала 2 стоит имя переменной, а не дыра.

    Владелец лечит прогон по этому тексту: `git -C  pull --ff-only` не
    исполнится и прочитается как поломка сообщения, а не как поручение.
    """
    monkeypatch.delenv(run.ENV_CLONE, raising=False)
    _code, seen, _out = full_run(vault_pair.config())
    (warn,) = infra_lines(seen["warnings"], "vault_ahead_unknown")
    assert f"${run.ENV_CLONE}" in warn
