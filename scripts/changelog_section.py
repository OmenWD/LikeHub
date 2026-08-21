"""Секция CHANGELOG.md по номеру версии.

Заметки к релизу пишутся руками по-английски: автогенерация из коммитов давала
русские заголовки, которые ничего не говорят международной аудитории HACS.

    python scripts/changelog_section.py 1.3.2      # печатает тело секции
    python scripts/changelog_section.py --list     # печатает версии из файла
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]")


def sections() -> dict[str, str]:
    """Тело каждой версии, в порядке появления в файле."""
    result: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []

    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(line)
        if match:
            if current:
                result[current] = "\n".join(body).strip()
            current = match.group("version")
            body = []
            continue
        # Ссылочные определения в конце файла к телу секции не относятся.
        if current and not line.startswith("["):
            body.append(line)

    if current:
        result[current] = "\n".join(body).strip()
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    found = sections()
    if sys.argv[1] == "--list":
        print("\n".join(found))
        return 0

    version = sys.argv[1].lstrip("v")
    if version not in found:
        print(
            f"В CHANGELOG.md нет секции для версии {version}. "
            f"Есть: {', '.join(found) or '—'}",
            file=sys.stderr,
        )
        return 1

    print(found[version])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
