"""Render the parts of the site that a release changes.

    python .github/scripts/render_site.py --tag 2026-04 --hashes <dir> --site .

A release build uploads one `hashes_<winpyver>.md` per flavor, in exactly the
table format `md5_sha1.txt` is made of. Those files, plus the handful of
sentences a human writes per cycle, are everything the site needs:

  * `md5_sha1.txt` gains a section -- the rows go in verbatim, because
    `wppm.hash` pads to fixed widths, so nothing has to be re-rendered and
    nothing can drift.
  * `releases.html` gains one `<details>` block at the top of the archive, and
    the block that was open closes.

Both files are edited between markers, so this owns those regions and touches
nothing else. What it cannot know -- which Python leads, the highlights line,
the release-notes URL, the prose describing each flavor -- comes from
`site_content.toml`, and is the whole of the per-cycle hand edit.

Betas do not go on the site: the archive has never listed one, and only a
single cycle ever put one in `md5_sha1.txt`. The caller decides; this renders
whatever tag it is given.
"""
import argparse
import datetime
import re
import sys
import tomllib
from pathlib import Path

# the header wppm.hash writes, reproduced exactly: its column widths are
# constants there, which is what lets rows from different flavors be
# concatenated without re-rendering any of them
HASH_HEADER = (
    f"{'MD5':<32} | {'SHA-1':<40} | {'SHA-256':<64} | "
    f"{'Binary':<33} | {'Size':<20} | {'blake2b-256':<64}"
)
HASH_RULE = "|".join("-" * len(part) for part in HASH_HEADER.split("|"))

BINARY_SUFFIXES = (".exe", ".zip", ".7z")
PACKAGE_SET_SUFFIXES = (".toml", ".txt")

# the order the download links have always been listed in: the installer
# first, then whichever archive that flavor ships
FORMAT_ORDER = ("exe", "zip", "7z")

# WinPythonslim-64bit-3.15.0.5b1.md -- the flavor is explicit here, which is
# why the flavors are read off the changelogs rather than guessed out of the
# binary names, where version and flavor run together
CHANGELOG_ROW = re.compile(r"^WinPython(?P<flavor>[A-Za-z0-9]*)-(?P<arch>\d+)bit-(?P<version>.+)\.md$")

# Both files accumulate: a release adds an entry, it does not replace the last
# one. So these are insertion points, not regions this script owns. md5_sha1.txt
# is served as plain text and gets no marker at all -- the first "### " line is
# unambiguous enough, and a marker there would be visible to every reader.
RELEASES_MARKER = "<!-- newest release is inserted after this line -->"
SECTION_PREFIX = "### "


class Row:
    """One line of a hash table, kept verbatim."""

    __slots__ = ("name", "line")

    def __init__(self, name: str, line: str):
        self.name = name
        self.line = line


def read_hash_rows(hashes_dir: Path) -> list[Row]:
    """Every data row of every hashes_*.md, unparsed apart from the name."""
    rows: list[Row] = []
    files = sorted(hashes_dir.glob("hashes_*.md"))
    if not files:
        raise SystemExit(f"no hashes_*.md in {hashes_dir}")
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("MD5", "---")) or line.count("|") < 5:
                continue
            fields = line.split("|")
            rows.append(Row(fields[3].strip(), line.rstrip()))
    return rows


def hash_tables(rows: list[Row]) -> str:
    """The two tables of an md5_sha1.txt section.

    Binaries first, then the lock files and requirements -- the order the file
    has always used. The package indexes are hashed by the build too, but have
    never been listed here, so they are dropped.
    """
    binaries = sorted((r for r in rows if r.name.endswith(BINARY_SUFFIXES)), key=lambda r: r.name)
    package_sets = sorted(
        (r for r in rows if r.name.endswith(PACKAGE_SET_SUFFIXES)), key=lambda r: r.name
    )
    if not binaries:
        raise SystemExit("the hashes name no binaries; is this the right release?")

    out = [HASH_HEADER, HASH_RULE]
    out += [r.line for r in binaries]
    out += ["", HASH_HEADER, HASH_RULE]
    out += [r.line for r in package_sets]
    return "\n".join(out)


def md5_section(title: str, when: datetime.date, rows: list[Row]) -> str:
    """### WinPython 2026-04 (September 6th 2026), then the two tables."""
    return f"### {title} ({format_date(when)})\n\n{hash_tables(rows)}\n"


def ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


def format_date(when: datetime.date) -> str:
    return f"{when:%B} {ordinal(when.day)}, {when.year}"


def builds_from(rows: list[Row]) -> dict:
    """{python minor: {ver2: {flavor: [formats]}}} read off the file names."""
    flavors, versions = set(), {}
    for row in rows:
        match = CHANGELOG_ROW.match(row.name)
        if match:
            flavors.add(match.group("flavor"))
    if not flavors:
        raise SystemExit("the hashes name no package index, so no flavor is known")

    # longest first, so "slimf" is not read as "slim" with a stray f
    pattern = re.compile(
        r"^WinPython(?P<arch>\d+)-(?P<ver2>\d[\d.]*?)"
        rf"(?P<flavor>{'|'.join(sorted(flavors, key=len, reverse=True))})"
        r"(?P<level>[a-z0-9]*)\.(?P<format>exe|zip|7z)$"
    )
    for row in rows:
        match = pattern.match(row.name)
        if not match:
            continue
        ver2 = match.group("ver2")
        entry = versions.setdefault(ver2, {})
        entry.setdefault(match.group("flavor"), []).append(match.group("format"))

    by_minor: dict = {}
    for ver2, flavours in sorted(versions.items(), key=lambda kv: version_key(kv[0])):
        minor = ".".join(ver2.split(".")[:2])
        by_minor.setdefault(minor, {})[ver2] = {
            f: sorted(v, key=FORMAT_ORDER.index) for f, v in flavours.items()
        }
    return by_minor


def version_key(ver: str) -> tuple:
    return tuple(int(p) for p in ver.split(".") if p.isdigit())


def download_url(tag: str, name: str) -> str:
    return f"https://github.com/winpython/winpython/releases/download/{tag}/{name}"


def changelog_url(name: str) -> str:
    return f"https://github.com/winpython/winpython/blob/master/changelogs/{name}"


def releases_entry(tag: str, title: str, when: datetime.date, content: dict, rows: list[Row]) -> str:
    """One <details> block, the shape every entry in the archive already has."""
    flavor_text = content["flavors"]
    release = content["release"]
    pythons = release.get("pythons", {})
    level = release.get("level", "")

    lines = [
        '<details class="release" open>',
        f'    <summary>{title} <span class="when">— {format_date(when)}</span></summary>',
        '    <div class="body">',
        f'        <p><a href="{release["notes_url"]}">Release notes and discussion</a>',
        '           · <a href="index.html#download">download page</a></p>',
        f'        <p class="hl">{release["highlights"]}</p>',
    ]

    tag_url = f"https://github.com/winpython/winpython/releases/tag/{tag.replace('/', '%2F')}"
    for minor, versions in builds_from(rows).items():
        for ver2, flavours in versions.items():
            python = pythons.get(minor, ver2.rsplit(".", 1)[0])
            forge = f"https://sourceforge.net/projects/winpython/files/WinPython_{minor}/{ver2}/"
            lines += [
                f'        <p class="grp">WinPython <strong>{minor}</strong> — Python {python} · <a',
                f'           href="{forge}">SourceForge</a> and <a',
                f'           href="{tag_url}">Github</a></p>',
                "        <ul>",
            ]
            for flavor in sorted(flavours, key=lambda f: (len(f), f)):
                stem = f"WinPython64-{ver2}{flavor}{level}"
                index = f"WinPython{flavor}-64bit-{ver2}{level}.md"
                pylock = f"pylock.64-{ver2.replace('.', '_')}{flavor}{level}.toml"
                requir = f"requir.64-{ver2.replace('.', '_')}{flavor}{level}.txt"
                links = [
                    f'<a href="{download_url(tag, stem + "." + fmt)}">{fmt}</a>'
                    for fmt in flavours[flavor]
                ]
                links += [
                    f'<a href="{changelog_url(index)}">packages</a>',
                    f'<a href="{changelog_url(pylock)}">pylock</a>',
                    f'<a href="{changelog_url(requir)}">requirements</a>',
                ]
                described = flavor_text.get(flavor, {}).get("archive", flavor)
                lines.append(
                    f"            <li>WinPython64-<strong>{ver2}</strong>{flavor}{level}"
                    f" — {described} :"
                )
                lines += [f"                {link}," for link in links[:-1]]
                lines.append(f"                {links[-1]}</li>")
            lines.append("        </ul>")

    lines += ["    </div>", "</details>"]
    return "\n".join(lines)


def insert_after(text: str, marker: str, block: str) -> str:
    """Put a block just after a marker line, which stays where it is."""
    at = text.find(marker)
    if at < 0:
        raise SystemExit(f"marker not found: {marker}")
    after = at + len(marker)
    return f"{text[:after]}\n\n{block}\n{text[after:].lstrip(chr(10))}"


def insert_before_first_section(text: str, block: str) -> str:
    """Put a section above the newest one, which is the file's first."""
    at = text.find(SECTION_PREFIX)
    if at < 0:
        raise SystemExit(f"no {SECTION_PREFIX.strip()!r} section to insert above")
    return f"{text[:at]}{block}\n\n{text[at:]}"


def close_open_entry(text: str) -> str:
    """Only the newest entry stands open, so the last newest one closes."""
    return text.replace('<details class="release" open>', '<details class="release">', 1)


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag, e.g. 2026-04")
    parser.add_argument("--hashes", required=True, type=Path, help="directory of hashes_*.md")
    parser.add_argument("--site", default=Path("."), type=Path, help="the site checkout")
    parser.add_argument("--content", type=Path, help="site_content.toml (default: beside this script)")
    parser.add_argument("--date", help="release date as YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv[1:])

    content_path = args.content or Path(__file__).with_name("site_content.toml")
    with content_path.open("rb") as fh:
        content = tomllib.load(fh)
    when = (
        datetime.date.fromisoformat(args.date) if args.date
        else datetime.date.fromisoformat(content["release"]["date"])
        if content["release"].get("date") else datetime.date.today()
    )
    title = content["release"].get("title") or f"WinPython {args.tag}"

    rows = read_hash_rows(args.hashes)

    md5_path = args.site / "md5_sha1.txt"
    md5_path.write_text(
        insert_before_first_section(
            md5_path.read_text(encoding="utf-8"), md5_section(title, when, rows)
        ),
        encoding="utf-8", newline="\n",
    )
    print(f"md5_sha1.txt   + section {title}")

    releases_path = args.site / "releases.html"
    text = close_open_entry(releases_path.read_text(encoding="utf-8"))
    releases_path.write_text(
        insert_after(text, RELEASES_MARKER,
                     releases_entry(args.tag, title, when, content, rows)),
        encoding="utf-8", newline="\n",
    )
    print(f"releases.html  + entry {title}")


if __name__ == "__main__":
    main(sys.argv)
