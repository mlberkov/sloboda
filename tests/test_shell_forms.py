"""Три формы механики блока, заведённые 2026-09-04.

Повод: реестр правок канона §B, «Исполнимость shell-блока», рецидивы
2026-09-03/04 (счёт 5, 4, 5). По лестнице лечения §B дописывание текста
закрыто — лечение есть расширение механики (owner-акт 2026-09-04, пункт 1=да).
Формы стоят внутри существующих чекеров: новых чекеров пакет не заводит.

  shell_mech.pipe_truncates_error       — побочный эффект под обрезкой конвейера;
  env_presupposition.push_workflow_without_scope — push файла workflow без
                                          измеренных прав токена;
  env_presupposition.gh_auth_without_status — добавление права без чтения прав.

Единица диспозиции — «на артефакт `--fast`» для фикстур и «на блок» для строк.
"""

from __future__ import annotations

import os

import pytest
import yaml

import run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "linter", "fixtures")

# Счёты находок до правки (прогон 2026-09-04, до заведения форм): фикстуры
# дополняются, а не переписываются, и прежние формы обязаны остаться на месте.
BASELINE = {"shell_mech": 8, "env_presupposition": 3}


@pytest.fixture(scope="module")
def checkers() -> dict:
    with open(os.path.join(ROOT, "linter", "manifest.yaml"), encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh) or {}
    active, _skipped, failed = run.load_checkers(manifest, "handoff")
    assert failed == []
    return {c["name"]: c for c in active}


def check(checkers: dict, name: str, text: str) -> list[str]:
    ch = checkers[name]
    return [f.message for f in ch["module"].check(text, ch["config"])]


def marked(messages: list[str], form: str) -> list[str]:
    return [m for m in messages if f"[{form}]" in m]


def fixture_findings(capsys, colour: str, name: str) -> tuple[int, list[str]]:
    path = os.path.join(FIXTURES, colour, f"{name}.md")
    code = run.main(["--fast", path, "--no-report"])
    out = capsys.readouterr().out
    listed = out.split("## Находки", 1)[1].split("## Сводка", 1)[0]
    return code, [ln for ln in listed.splitlines() if f" {name} " in ln]


def block(*lines: str) -> str:
    body = "\n".join(lines)
    return f"```bash\n{body}\n```\n"


# ─────────────────── фикстуры: на артефакт --fast ────────────────────────

@pytest.mark.parametrize("name,form,count", [
    ("shell_mech", "pipe_truncates_error", 2),
    ("env_presupposition", "push_workflow_without_scope", 1),
    ("env_presupposition", "gh_auth_without_status", 1),
])
def test_red_fixture_carries_form(capsys, name, form, count):
    code, lines = fixture_findings(capsys, "red", name)
    assert code == 1
    assert len([ln for ln in lines if f"[{form}]" in ln]) == count


@pytest.mark.parametrize("name", ["shell_mech", "env_presupposition"])
def test_green_fixture_stays_silent(capsys, name):
    code, lines = fixture_findings(capsys, "green", name)
    assert (code, lines) == (0, [])


@pytest.mark.parametrize("name,added", [("shell_mech", 2),
                                        ("env_presupposition", 2)])
def test_previous_forms_did_not_drop(capsys, name, added):
    """Прежние счёты не падают: фикстура дополнена, а не переписана."""
    _code, lines = fixture_findings(capsys, "red", name)
    assert len(lines) == BASELINE[name] + added


# ───────────────── pipe_truncates_error: на блок ─────────────────────────

@pytest.mark.parametrize("line", [
    "git push origin HEAD 2>&1 | tail -3",
    "git commit -am 'x' 2>&1 | head -2",
    "gh pr merge 12 --squash 2>&1 | head -5",
    "gh release create v1.2 2>&1 | tail -1",
    "curl -sS -X POST https://example.invalid/hook 2>&1 | tail -2",
])
def test_side_effect_under_truncation_is_red(checkers, line):
    assert len(marked(check(checkers, "shell_mech", block(line)),
                      "pipe_truncates_error")) == 1


@pytest.mark.parametrize("lines", [
    ("git push origin HEAD 2>&1 | tail -3; echo \"rc=${PIPESTATUS[0]}\"",),
    ("git push origin HEAD 2>&1 | tail -3", "echo \"rc=${PIPESTATUS[0]}\""),
    ("gh pr create --fill 2>&1 | grep -Ei 'error|rejected' || echo 'нет строк'",),
])
def test_absolutions_keep_failure_legible(checkers, lines):
    """Оправдывают код выхода команды и фильтр по слову, а не по позиции."""
    assert marked(check(checkers, "shell_mech", block(*lines)),
                  "pipe_truncates_error") == []


@pytest.mark.parametrize("line", [
    "gh run list --limit 5 | head -5",
    "git log --oneline | head -3",
    "gh api repos/theygrow/altrego | head -20",
])
def test_reads_are_not_the_subject(checkers, line):
    """Чтение под обрезкой прячет только выдачу, а не отказ действия."""
    assert marked(check(checkers, "shell_mech", block(line)),
                  "pipe_truncates_error") == []


def test_pipefail_is_not_an_absolution(checkers):
    """`set -o pipefail` меняет код выхода, но текст отказа остаётся обрезан."""
    text = block("set -o pipefail", "git push origin HEAD 2>&1 | tail -3")
    assert len(marked(check(checkers, "shell_mech", text),
                      "pipe_truncates_error")) == 1


# ───────────── права токена: requires_above и оправдание ─────────────────

def test_workflow_push_without_status_is_red(checkers):
    text = block("git add .github/workflows/ci.yml",
                 "git commit -m 'гейт: лимит прогона'", "git push")
    assert len(marked(check(checkers, "env_presupposition", text),
                      "push_workflow_without_scope")) == 1


def test_plain_push_is_not_the_subject(checkers):
    """`requires_above`: без staged-файла workflow предпосылки о праве нет."""
    text = block("git add run.py", "git commit -m 'правка'", "git push")
    assert marked(check(checkers, "env_presupposition", text),
                  "push_workflow_without_scope") == []


@pytest.mark.parametrize("command", ["gh auth refresh -s workflow",
                                     "gh auth login --scopes workflow"])
def test_auth_change_without_status_is_red(checkers, command):
    assert len(marked(check(checkers, "env_presupposition", block(command)),
                      "gh_auth_without_status")) == 1


def test_status_above_absolves_both_forms(checkers):
    text = block("gh auth status", "git add .github/workflows/ci.yml",
                 "git push", "gh auth refresh -s workflow")
    messages = check(checkers, "env_presupposition", text)
    assert marked(messages, "push_workflow_without_scope") == []
    assert marked(messages, "gh_auth_without_status") == []
