"""The new-app generator (scripts/new_module.py, docs/PLATFORM.md).

It runs on a copy of the backend and of the files it edits, then the app it
made must pass its own smoke test and the platform's rules - so a change to
the manifests that the template misses fails here, not for the next author.
A taken or reserved id must write nothing.
From backend/: python tests/test_new_module.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EDITED = ("frontend/src/modules/index.tsx", "frontend/src/modules/pages.ts", "frontend/src/lib/navConfig.ts", "docs/PLATFORM.md")


def generate(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "scripts" / "new_module.py"), *args, "--root", str(root)],
                          capture_output=True, text=True)


def run_test(root: Path, name: str) -> None:
    result = subprocess.run([sys.executable, f"tests/{name}.py"], cwd=root / "backend", capture_output=True, text=True, timeout=170)
    assert result.returncode == 0, f"{name} failed after generating an app:\n{result.stdout[-2000:]}{result.stderr[-3000:]}"


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copytree(REPO / "backend" / "app", root / "backend" / "app", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "backend" / "tests", root / "backend" / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(REPO / "backend" / "main.py", root / "backend" / "main.py")
        for name in EDITED:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / name, root / name)
        (root / "frontend" / "src" / "pages").mkdir(parents=True)

        made = generate(root, "club_events", "Events", "Club events and who is going")
        assert made.returncode == 0, made.stderr
        assert "| `club_events` | `/api/club_events` | Events |" in (root / "docs" / "PLATFORM.md").read_text()
        assert "clubEvents];" in (root / "frontend" / "src" / "modules" / "index.tsx").read_text()
        run_test(root, "test_club_events")
        run_test(root, "test_modules")

        before = {p: p.read_text() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        for args in (("club_events", "Events again", "Twice"), ("admin", "Admin", "Reserved"), ("Bad-Id", "Bad", "Invalid")):
            refused = generate(root, *args)
            assert refused.returncode != 0, args
        after = {p: p.read_text() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        assert before == after, "a refused run changed files"


if __name__ == "__main__":
    tests()
    print("new module: ok")
