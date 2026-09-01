"""Разбор артефакта: `linter/common.py`.

Огороженные блоки — общий предмет половины чекеров: если `parse_blocks` считает
границы иначе, чем читает владелец, ошибаются все читатели сразу.
"""

from __future__ import annotations

import re

from linter import common


def test_blocks_basic_bounds():
    text = "пролог\n```bash\nls -la\necho ok\n```\nэпилог\n"
    (b,) = common.parse_blocks(text)
    assert (b.lang, b.fence_line, b.start, b.end) == ("bash", 2, 3, 4)
    assert b.closed and b.is_shell
    assert b.lines == ["ls -la", "echo ok"]


def test_numbered_offsets():
    text = "пролог\n```bash\nls\necho ok\n```\n"
    (b,) = common.parse_blocks(text)
    assert list(b.numbered()) == [(3, "ls"), (4, "echo ok")]


def test_unclosed_block():
    text = "```bash\nls\n"
    (b,) = common.parse_blocks(text)
    assert not b.closed and b.lines == ["ls", ""]


def test_closing_fence_must_be_at_least_as_long():
    text = "````bash\n```\nвсё ещё внутри\n````\n"
    (b,) = common.parse_blocks(text)
    assert b.closed and "всё ещё внутри" in b.lines


def test_tilde_fence_and_language():
    text = "~~~python\nprint(1)\n~~~\n"
    (b,) = common.parse_blocks(text)
    assert b.lang == "python" and not b.is_shell and b.closed


def test_handoff_marker_makes_block_shell_without_language():
    text = "Handoff for shell\n\n```\nls\n```\n"
    (b,) = common.parse_blocks(text)
    assert b.is_shell and b.lang == ""


def test_handoff_lookback_is_data():
    """Окно взгляда назад — данные манифеста, не константа кода."""
    text = "Handoff for shell\n1\n2\n3\n4\n```\nls\n```\n"
    assert not common.parse_blocks(text)[0].is_shell
    assert common.parse_blocks(text, {"handoff_lookback": 6})[0].is_shell


def test_shell_blocks_filter():
    text = "```bash\nls\n```\n\n```python\nprint(1)\n```\n"
    assert [b.lang for b in common.shell_blocks(text)] == ["bash"]


def test_in_block_lines_covers_fences():
    text = "пролог\n```bash\nls\n```\nэпилог\n"
    assert common.in_block_lines(text) == {2, 3, 4}


def test_mask_spans_preserves_length():
    line = "run-20260831T140913Z и ещё 42"
    masked = common.mask_spans(line, [r"run-\d{8}T\d{6}Z"])
    assert len(masked) == len(line) and "run-" not in masked and "42" in masked


def test_significant_chars():
    # Значимые — буквы и цифры: a, b, 1, 2.
    assert common.significant_chars("a b, 12!") == 4
    assert common.significant_chars(" ,.!—") == 0


def test_head_sections_stops_on_heading_and_end():
    lines = ["## Ваши действия", "раз", "два", "## Дальше", "три"]
    head = re.compile(r"^##\s+Ваши действия")
    end = re.compile(r"^Конец хода")
    assert common.head_sections(lines, head, end, 10) == [(2, 3)]


def test_head_sections_stops_on_end_marker_and_max_lines():
    lines = ["## Ваши действия", "раз", "Конец хода", "два"]
    head = re.compile(r"^##\s+Ваши действия")
    end = re.compile(r"^Конец хода")
    assert common.head_sections(lines, head, end, 10) == [(2, 2)]
    assert common.head_sections(lines, head, end, 1) == [(2, 2)]


def test_split_lines_normalizes_eol():
    assert common.split_lines("a\r\nb\rc\n") == ["a", "b", "c", ""]
