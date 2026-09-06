# -*- coding: utf-8 -*-
"""The generator writes into two files people read directly.

`md5_sha1.txt` is a checksum record going back to 2019 and `releases.html` is
the download archive back to 2020, so the danger here is not a crash but a
quiet change of shape: a column that stops lining up, a link order that flips,
an entry that lands in the wrong place. Those are what this pins.

The hash rows are deliberately never re-rendered -- `wppm.hash` pads to fixed
widths, so the build's own lines go in verbatim -- and the tests check the
geometry that assumption rests on.
"""
import datetime
import importlib.util
import shutil
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1]
SCRIPT = SITE / ".github/scripts/render_site.py"


@pytest.fixture(scope="module")
def render():
    spec = importlib.util.spec_from_file_location("render_site", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hash_line(name: str, size: int = 1234) -> str:
    """A row in exactly the shape wppm.hash writes."""
    md5, sha1 = "0" * 32, "1" * 40
    sha256, blake = "2" * 64, "3" * 64
    return (
        f"{md5} | {sha1} | {sha256} | {name.ljust(33)} | "
        f"{f'{size:,} Bytes'.replace(',', ' ').rjust(20)} | {blake}"
    )


def write_hashes(directory: Path, winpyver: str, names: list[str]) -> None:
    header = (
        f"{'MD5':<32} | {'SHA-1':<40} | {'SHA-256':<64} | "
        f"{'Binary':<33} | {'Size':<20} | {'blake2b-256':<64}"
    )
    rule = "|".join("-" * len(part) for part in header.split("|"))
    body = "\n".join([header, rule] + [hash_line(n) for n in names])
    (directory / f"hashes_{winpyver}.md").write_text(body + "\n", encoding="utf-8")


@pytest.fixture
def one_cycle(tmp_path):
    """Two Pythons, four flavors, the formats each really ships."""
    d = tmp_path / "hashes"
    d.mkdir()
    write_hashes(d, "3.14.7.0dot", [
        "WinPython64-3.14.7.0dot.exe", "WinPython64-3.14.7.0dot.zip",
        "WinPythondot-64bit-3.14.7.0.md",
        "pylock.64-3_14_7_0dot.toml", "requir.64-3_14_7_0dot.txt"])
    write_hashes(d, "3.14.7.0slimf", [
        "WinPython64-3.14.7.0slimf.7z", "WinPython64-3.14.7.0slimf.exe",
        "WinPythonslimf-64bit-3.14.7.0.md",
        "pylock.64-3_14_7_0slimf.toml", "requir.64-3_14_7_0slimf.txt"])
    write_hashes(d, "3.15.0.4dot", [
        "WinPython64-3.15.0.4dot.exe", "WinPython64-3.15.0.4dot.zip",
        "WinPythondot-64bit-3.15.0.4.md",
        "pylock.64-3_15_0_4dot.toml", "requir.64-3_15_0_4dot.txt"])
    return d


class TestHashTables:
    def test_binaries_and_package_sets_are_separate_tables(self, render, one_cycle):
        rows = render.read_hash_rows(one_cycle)
        tables = render.hash_tables(rows).split("\n\n")
        assert len(tables) == 2
        first = [l.split("|")[3].strip() for l in tables[0].splitlines()[2:]]
        second = [l.split("|")[3].strip() for l in tables[1].splitlines()[2:]]
        assert all(n.endswith((".exe", ".zip", ".7z")) for n in first)
        assert all(n.endswith((".toml", ".txt")) for n in second)

    def test_the_package_indexes_are_not_listed(self, render, one_cycle):
        """The build hashes them; md5_sha1.txt has never carried them."""
        rows = render.read_hash_rows(one_cycle)
        assert ".md" not in render.hash_tables(rows)

    def test_rows_go_in_verbatim(self, render, one_cycle):
        """Re-rendering is what would let the columns drift."""
        rows = render.read_hash_rows(one_cycle)
        table = render.hash_tables(rows)
        for row in rows:
            if row.name.endswith((".exe", ".zip", ".7z", ".toml", ".txt")):
                assert row.line in table

    def test_the_column_geometry_is_the_one_the_file_already_has(self, render, one_cycle):
        """(33, 42, 66, 35, 22, 65) -- measured from the 2026-03 section."""
        rows = render.read_hash_rows(one_cycle)
        data = [l for l in render.hash_tables(rows).splitlines()
                if l.count("|") >= 5 and not l.startswith(("MD5", "---"))]
        assert {tuple(len(f) for f in l.split("|")) for l in data} == {(33, 42, 66, 35, 22, 65)}

    def test_an_empty_directory_is_an_error(self, render, tmp_path):
        with pytest.raises(SystemExit):
            render.read_hash_rows(tmp_path)

    def test_metadata_without_binaries_is_an_error(self, render, tmp_path):
        """A half-finished release must not quietly produce an empty section."""
        write_hashes(tmp_path, "3.14.7.0dot", ["pylock.64-3_14_7_0dot.toml"])
        with pytest.raises(SystemExit):
            render.hash_tables(render.read_hash_rows(tmp_path))


class TestBuildsFrom:
    def test_flavors_versions_and_formats(self, render, one_cycle):
        builds = render.builds_from(render.read_hash_rows(one_cycle))
        assert list(builds) == ["3.14", "3.15"]
        assert builds["3.14"]["3.14.7.0"] == {"dot": ["exe", "zip"], "slimf": ["exe", "7z"]}
        assert builds["3.15"]["3.15.0.4"] == {"dot": ["exe", "zip"]}

    def test_slimf_is_not_read_as_slim(self, render, one_cycle):
        """The flavors overlap; the longest has to win or a build vanishes."""
        builds = render.builds_from(render.read_hash_rows(one_cycle))
        assert "slim" not in builds["3.14"]["3.14.7.0"]

    def test_the_installer_is_listed_first(self, render, one_cycle):
        """exe, then zip or 7z -- the order every entry in the archive uses."""
        builds = render.builds_from(render.read_hash_rows(one_cycle))
        for versions in builds.values():
            for flavours in versions.values():
                for formats in flavours.values():
                    assert formats[0] == "exe"

    def test_flavors_come_from_the_package_indexes(self, render, tmp_path):
        """Binary names run version and flavor together; the indexes do not."""
        write_hashes(tmp_path, "3.14.7.0dot", ["WinPython64-3.14.7.0dot.exe"])
        with pytest.raises(SystemExit):
            render.builds_from(render.read_hash_rows(tmp_path))


class TestDates:
    @pytest.mark.parametrize("day,expected", [
        (1, "1st"), (2, "2nd"), (3, "3rd"), (11, "11th"), (12, "12th"),
        (13, "13th"), (22, "22nd"), (31, "31st"),
    ])
    def test_ordinals(self, render, day, expected):
        assert render.ordinal(day) == expected

    def test_the_format_the_files_already_use(self, render):
        """"August 22nd, 2026" -- md5_sha1.txt and the archive both read so."""
        assert render.format_date(datetime.date(2026, 8, 22)) == "August 22nd, 2026"


class TestInsertion:
    def test_a_section_goes_above_the_newest(self, render):
        text = "preamble\n\n### WinPython 2026-03 (August 22nd, 2026)\n\nrows\n"
        out = render.insert_before_first_section(text, "### NEW\n\ntable")
        assert out.index("### NEW") < out.index("### WinPython 2026-03")
        assert "preamble" in out and "rows" in out

    def test_a_file_with_no_section_is_an_error(self, render):
        with pytest.raises(SystemExit):
            render.insert_before_first_section("nothing here", "### NEW")

    def test_an_entry_goes_after_the_marker(self, render):
        text = f"top\n{render.RELEASES_MARKER}\n\n<details>old</details>\n"
        out = render.insert_after(text, render.RELEASES_MARKER, "<details>new</details>")
        assert out.index("new") < out.index("old")
        assert render.RELEASES_MARKER in out

    def test_a_missing_marker_is_an_error(self, render):
        with pytest.raises(SystemExit):
            render.insert_after("no marker", render.RELEASES_MARKER, "x")

    def test_only_the_newest_entry_stays_open(self, render):
        text = '<details class="release" open>a</details>\n<details class="release">b</details>'
        assert render.close_open_entry(text).count('class="release" open') == 0


class TestAgainstTheRealSite:
    """Run it over the checked-in files, which is what CI will do."""

    @pytest.fixture
    def content(self):
        path = SITE / ".github/scripts/site_content.toml"
        if not path.is_file():
            pytest.skip("site_content.toml is gone")
        return path

    def test_a_release_lands_in_both_files(self, render, one_cycle, content, tmp_path):
        site = tmp_path / "site"
        shutil.copytree(SITE, site, ignore=shutil.ignore_patterns(".git"))
        before = (site / "releases.html").read_text(encoding="utf-8")

        render.main(["render_site.py", "--tag", "2026-04", "--hashes", str(one_cycle),
                     "--site", str(site), "--content", str(content), "--date", "2026-09-06"])

        md5 = (site / "md5_sha1.txt").read_text(encoding="utf-8")
        assert md5.lstrip().startswith("### WinPython 2026-03 (September 6th, 2026)")
        assert "### WinPython 2026-03 (August 22nd, 2026)" in md5, "history must survive"

        after = (site / "releases.html").read_text(encoding="utf-8")
        assert after.count("<details") == before.count("<details") + 1
        assert after.count('class="release" open') == 1, "exactly one entry stands open"
        assert "WinPython64-3.14.7.0dot.exe" in after

    def test_the_archive_keeps_every_older_entry(self, render, one_cycle, content, tmp_path):
        """The archive going back to 2020 is a stated WinPython advantage."""
        site = tmp_path / "site"
        shutil.copytree(SITE, site, ignore=shutil.ignore_patterns(".git"))
        before = (site / "releases.html").read_text(encoding="utf-8").count("<summary>")
        render.main(["render_site.py", "--tag", "2026-04", "--hashes", str(one_cycle),
                     "--site", str(site), "--content", str(content), "--date", "2026-09-06"])
        assert (site / "releases.html").read_text(encoding="utf-8").count("<summary>") == before + 1
