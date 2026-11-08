"""scripts/fork_release.py: which version comes next, and what the changelog becomes."""

from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fork_release.py"
spec = importlib.util.spec_from_file_location("fork_release", SCRIPT)
fork_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fork_release)

DAY = datetime.date(2026, 11, 8)
LOG = "# Changes\n\nIntro.\n\n## Не выпущено\n\n- new thing\n\n## 2026.11.01\n\n- old thing\n"


def test_the_first_release_of_a_day_is_the_date_and_later_ones_count_up():
    assert fork_release.next_version(DAY, set()) == "2026.11.08"
    assert fork_release.next_version(DAY, {"fork-2026.11.08"}) == "2026.11.08.2"
    assert fork_release.next_version(DAY, {"fork-2026.11.08", "fork-2026.11.08.2"}) == "2026.11.08.3"


def test_the_unreleased_section_becomes_the_release_and_a_fresh_one_opens():
    text, notes = fork_release.released_text(LOG, "2026.11.08")

    assert notes == "- new thing"
    assert text == ("# Changes\n\nIntro.\n\n## Не выпущено\n\n## 2026.11.08\n\n- new thing\n\n"
                    "## 2026.11.01\n\n- old thing\n")
    assert fork_release.released_text(text.replace("## Не выпущено\n\n", "## Не выпущено\n\n- more\n\n"),
                                      "2026.11.08.2")[1] == "- more"


def test_a_release_without_notes_is_refused():
    with pytest.raises(SystemExit, match="empty"):
        fork_release.released_text("## Не выпущено\n\n## 2026.11.01\n\n- old\n", "2026.11.08")
