"""Маркеры изъятия: `linter/ignores.py`.

Проверяется адресность (следующая строка, названный чекер) и то, что изъятие без
причины ничего не снимает и само даёт находку: молчаливое изъятие неотличимо от
отсутствия предмета, а именно эту неотличимость полоса и ловит.
"""

from __future__ import annotations

from linter import ignores
from linter.common import ERROR, RED, Finding


def f(line: int, checker: str = "smoke_line", severity: str = RED) -> Finding:
    return Finding(line, checker, severity, "сообщение")


def test_parse_marker_fields():
    text = "<!-- lint:ignore smoke_line — строка смоука цитируется -->\nстрока\n"
    (ig,) = ignores.parse(text)
    assert (ig.line, ig.target, ig.checker) == (1, 2, "smoke_line")
    assert ig.reason == "строка смоука цитируется" and ig.valid


def test_dash_forms():
    for dash in ("—", "–", "-", "--"):
        text = f"<!-- lint:ignore smoke_line {dash} причина -->\nстрока\n"
        (ig,) = ignores.parse(text)
        assert ig.valid and ig.reason == "причина", dash


def test_scope_next_line_only():
    text = "<!-- lint:ignore smoke_line — причина -->\nстрока\nещё строка\n"
    kept, applied = ignores.apply(text, [f(2), f(3)])
    assert applied == 1
    assert [x.line for x in kept] == [3]


def test_scope_checker_only():
    text = "<!-- lint:ignore smoke_line — причина -->\nстрока\n"
    kept, applied = ignores.apply(text, [f(2, "smoke_line"), f(2, "turn_end")])
    assert applied == 1
    assert [x.checker for x in kept] == ["turn_end"]


def test_empty_reason_keeps_finding():
    text = "<!-- lint:ignore smoke_line -->\nстрока\n"
    kept, applied = ignores.apply(text, [f(2)])
    assert applied == 0
    checkers = sorted(x.checker for x in kept)
    assert checkers == ["ignore_without_reason", "smoke_line"]
    extra = next(x for x in kept if x.checker == ignores.NAME)
    assert extra.severity == ERROR and extra.line == 1


def test_applied_counts_matched_only():
    """Маркер, не совпавший ни с одной находкой, применённым не считается."""
    text = "<!-- lint:ignore smoke_line — причина -->\nстрока\n"
    kept, applied = ignores.apply(text, [])
    assert (kept, applied) == ([], 0)


def test_several_markers_on_one_line():
    text = ("<!-- lint:ignore smoke_line — раз --><!-- lint:ignore turn_end — два -->\n"
            "строка\n")
    kept, applied = ignores.apply(text, [f(2, "smoke_line"), f(2, "turn_end")])
    assert applied == 2 and kept == []


def test_marker_inside_line_of_text():
    text = "текст <!-- lint:ignore grep_vacuum — причина --> хвост\nстрока\n"
    kept, applied = ignores.apply(text, [f(2, "grep_vacuum")])
    assert applied == 1 and kept == []
