import datetime
import re
from pathlib import Path
from textwrap import dedent
from time import sleep

from invoke import Exit, task
from watchdog.observers import Observer

DEV_ENV = {"FIRESTORE_EMULATOR_HOST": "127.0.0.1:8686"}
SKIP_WATCH = [".idea", ".pytest_cache", "__pycache__", ".git"]


class TestWatcher:
    def __init__(self, ctx):
        self.ctx = ctx

    def dispatch(self, event):
        # Ignore unwanted events
        for skip in SKIP_WATCH:
            if skip in event.src_path:
                return

        if event.is_directory or event.src_path[-1] == "~":
            return

        print(f"{event.src_path} {event.event_type}")

        self.run_tests()

    def run_tests(self):
        result = run_test_cmd(self.ctx, "pytest", env=DEV_ENV)
        if result:
            print("Tests failed. Check output for details.")


REPO_LINK = "https://github.com/altissimo-hq/firedantic"


@task
def release(ctx):
    """
    Tag the version in pyproject.toml and push the tag. Only runs from a clean
    checkout of main that matches origin/main, so the tag can't point at local-only
    or unmerged commits.
    """
    toml = Path("pyproject.toml").read_text()
    match = re.search(r'version = "(.*?)"', toml)
    if not match:
        raise Exit("Failed to find version in the pyproject.toml")
    version = match.group(1)

    branch = ctx.run("git rev-parse --abbrev-ref HEAD", hide=True).stdout.strip()
    if branch != "main":
        raise Exit(f"Releases are tagged from main, not '{branch}'")
    if ctx.run("git status --porcelain", hide=True).stdout.strip():
        raise Exit("Working tree is not clean")
    ctx.run("git fetch origin main", hide=True)
    local = ctx.run("git rev-parse HEAD", hide=True).stdout.strip()
    remote = ctx.run("git rev-parse origin/main", hide=True).stdout.strip()
    if local != remote:
        raise Exit("Local main differs from origin/main; pull or push first")
    if ctx.run(f"git tag --list {version}", hide=True).stdout.strip():
        raise Exit(f"Tag {version} already exists")

    print(f"Releasing {version} from {local[:7]}")
    ctx.run(f"git tag {version}", echo=True)
    ctx.run(f"git push origin {version}", echo=True)


def run_test_cmd(ctx, cmd, env=None) -> int:
    print("=" * 79)
    print(f"> {cmd}")
    return ctx.run(cmd, warn=True, env=env).exited


@task
def watch_tests(ctx):
    handler = TestWatcher(ctx)
    path = str(Path(".").absolute())
    observer = Observer()
    observer.schedule(handler, path, recursive=True)
    observer.start()

    print("Running tests")
    handler.run_tests()

    print(f"Watching {path} for changes.")

    try:
        while True:
            sleep(1)
    finally:
        observer.stop()
        observer.join()


@task
def unit_tests(ctx):
    ctx.run("pytest", env=DEV_ENV)


@task
def test(ctx):
    failed_commands = []

    if run_test_cmd(ctx, "pre-commit run --all-files"):
        failed_commands.append("Pre commit hooks")

    if run_test_cmd(ctx, "mypy firedantic"):
        failed_commands.append("Mypy")

    if run_test_cmd(ctx, "pytest", env=DEV_ENV):
        failed_commands.append("Unit tests")

    if failed_commands:
        msg = "Errors: " + ", ".join(failed_commands)
        raise Exit(message=msg, code=len(failed_commands))


@task
def unasync(ctx):
    """
    Generate source code for synchronous version of library
    """
    import unasync

    unasync.main()
    # Renaming can change the import order, so sort the generated imports here
    # instead of leaving it to the ruff pre-commit hook, which unasync would undo
    ctx.run("poetry run ruff check --select I --fix --quiet firedantic/_sync")
    ctx.run("poetry run ruff format .")


@task
def make_changelog(ctx):
    """
    Generate a changelog placeholder after bumping version in pyproject.toml
    """
    pyproject = (Path(__file__).parent / "pyproject.toml").read_text()
    changelog_path = Path(__file__).parent / "CHANGELOG.md"
    changelog = changelog_path.read_text()

    match = re.search(r'version = "(.*?)"', pyproject)
    if not match:
        raise Exit("Can't determine the library version")
    version = match.group(1)
    match = re.search(r"## \[Unreleased].*?## \[(.*?)]", changelog, re.DOTALL)
    if not match:
        raise Exit("Can't determine previous library version")
    old_version = match.group(1)
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    changes = f"""
    ## [Unreleased]

    ## [{version}] - {today}

    ### Added

    - Describe what's been added or remove if not applicable

    ### Changed

    - Describe what's been changed or remove if not applicable

    ### Removed

    - Describe what's been removed or remove this section if not applicable
    """
    new_changelog = changelog.replace("## [Unreleased]", dedent(changes).strip())

    links = f"""
    [unreleased]: {REPO_LINK}/compare/{version}...HEAD
    [{version}]: {REPO_LINK}/compare/{old_version}...{version}
    """
    new_changelog = re.sub(r"\[unreleased]:.*?HEAD", dedent(links).strip(), new_changelog)

    changelog_path.write_text(new_changelog)
    print(f"{changelog_path} was updated, please fill in release information")


@task
def integration(ctx):
    """
    Run all integration tests
    """
    files = [
        "integration_tests/configure_firestore_db_clients.py",
        "integration_tests/full_sync_flow.py",
        "integration_tests/full_async_flow.py",
        "integration_tests/full_readme_examples.py",
    ]
    for f in files:
        run_test_cmd(ctx, f"python {f}", env=DEV_ENV)
