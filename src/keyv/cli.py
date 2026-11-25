"""Interactive keyv entry point."""

import argparse
import fcntl
import getpass
import os
import shutil
import stat
import subprocess
import sys
import warnings
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from unicodedata import combining, east_asian_width

from . import __version__
from .repository import Repository
from .vault import Entry, Revision, Vault, VaultError

try:
    import readline
except ImportError:
    readline = None


def state_home() -> Path:
    configured = Path(os.environ.get("XDG_STATE_HOME", ""))
    base = configured if configured.is_absolute() else Path.home() / ".local" / "state"
    return base / "keyv"


BACK = -1
HOME = -2


def color(value: str, code: str, *, stream=None) -> str:
    stream = sys.stdout if stream is None else stream
    if stream.isatty() and os.environ.get("TERM") != "dumb" and "NO_COLOR" not in os.environ:
        return f"\x1b[{code}m{value}\x1b[0m"
    return value


def heading(title: str) -> None:
    print()
    title_width = sum(
        2 if east_asian_width(char) in ("W", "F") else 1
        for char in title if not combining(char)
    )
    columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    width = max(26, title_width + 4)
    if not sys.stdout.isatty() or width > columns:
        print(color(title, "1;36"))
        return
    border = ("╭", "╮", "╰", "╯", "─", "│")
    try:
        "".join(border).encode(sys.stdout.encoding or "ascii")
    except UnicodeEncodeError:
        border = ("+", "+", "+", "+", "-", "|")
    top_left, top_right, bottom_left, bottom_right, line, side = border
    print(color(top_left + line * (width - 2) + top_right, "36"))
    padding = " " * (width - title_width - 3)
    print(f"{color(side, '36')} {color(title, '1;36')}{padding}{color(side, '36')}")
    print(color(bottom_left + line * (width - 2) + bottom_right, "36"))


def status(message: str, code: str) -> None:
    print(f"\n  {color(message, code)}")


def error_message(error: Exception) -> None:
    label = color("keyv", "1;31", stream=sys.stderr)
    print(f"\n  {label}: {display(str(error))}", file=sys.stderr)


def choose(title: str, options: list[str], *, back: bool = False, home: bool = False) -> int:
    heading(title)
    if options:
        print()
    for number, option in enumerate(options, 1):
        print(f"  {color(f'{number:>2}.', '1;36')}  {option}")
    shortcuts = {}
    if back:
        shortcuts["b"] = ("Back", BACK)
    if home:
        shortcuts["m"] = ("Main menu", HOME)
    if shortcuts:
        footer = "   |   ".join(
            f"{color(key.upper(), '1;36')}  {label}" for key, (label, _) in shortcuts.items()
        )
        print(f"\n  {footer}")
    print()
    while True:
        answer = input(color("  > ", "1;36")).strip().lower()
        if answer in shortcuts:
            return shortcuts[answer][1]
        if answer.isascii() and answer.isdecimal() and len(answer) <= 9:
            index = int(answer) - 1
            if 0 <= index < len(options):
                return index
        print(f"  {color('Choose one of the options shown.', '33')}")


def ask(prompt: str, *, optional: bool = False, strip: bool = True) -> str:
    while True:
        value = input(f"  {color(prompt, '1')}")
        if strip:
            value = value.strip()
        if value or optional:
            return value
        print(f"  {color('A value is required.', '33')}")


def ask_platform(options: list[str]) -> str:
    prompt = "Platform name: "
    if readline is None or not options or not sys.stdin.isatty():
        return ask(prompt)
    completer = readline.get_completer()
    delimiters = readline.get_completer_delims()
    matches: list[str] = []

    def complete(text: str, state: int) -> str | None:
        if state == 0:
            prefix = platform_key(text)
            matches[:] = [name for name in options if platform_key(name).startswith(prefix)]
        return matches[state] if state < len(matches) else None

    try:
        readline.parse_and_bind("bind ^I rl_complete" if "libedit" in (readline.__doc__ or "") else "tab: complete")
        readline.set_completer_delims("")
        readline.set_completer(complete)
        return ask(prompt)
    finally:
        readline.set_completer(completer)
        readline.set_completer_delims(delimiters)


def hidden(prompt: str, *, optional: bool = False) -> str:
    while True:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                value = getpass.getpass(f"  {prompt}")
            except getpass.GetPassWarning:
                raise VaultError("A terminal is required for hidden input.") from None
        if value or optional:
            return value
        print(f"  {color('A value is required.', '33')}")


def load_master_key(root: Path) -> str:
    configured = os.environ.get("KEYV_MASTER")
    if configured:
        return configured

    path = root / "master-key"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return hidden("Enter master key: ")
    except OSError as error:
        raise VaultError(f"Cannot open {path}: {error.strerror}") from None

    with os.fdopen(fd, encoding="utf-8") as key_file:
        info = os.fstat(key_file.fileno())
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise VaultError(f"Master key file must be a regular file owned by you with mode 600: {path}")
        master = key_file.read().removesuffix("\n")
    if not master or "\n" in master or "\x00" in master:
        raise VaultError(f"Master key file must contain one non-empty line: {path}")
    return master


def display(value: str) -> str:
    return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in value)


def platform_key(value: str) -> str:
    return value.casefold()


class ReturnHome(Exception):
    pass


class ExitKeyv(Exception):
    pass


def submenu(
    title: str, options: list[str], *, home: bool = False, exit_menu: bool = False,
) -> int | None:
    actions = [*options, "Exit"] if exit_menu else options
    index = choose(title, actions, back=True, home=home)
    if index == BACK:
        return None
    if index == HOME:
        raise ReturnHome
    if exit_menu and index == len(options):
        raise ExitKeyv
    return index


def finished() -> None:
    submenu("Continue?", [], home=True, exit_menu=True)


def copy_to_clipboard(value: str) -> None:
    commands = []
    if sys.platform == "darwin":
        commands.append(("pbcopy",))
    if os.environ.get("WAYLAND_DISPLAY"):
        commands.append(("wl-copy",))
    if os.environ.get("DISPLAY"):
        commands.extend((("xclip", "-selection", "clipboard"), ("xsel", "--clipboard", "--input")))

    environment = os.environ.copy()
    environment.pop("KEYV_MASTER", None)
    for program, *args in commands:
        executable = shutil.which(program)
        if executable is None:
            continue
        try:
            result = subprocess.run(
                [executable, *args], input=value.encode("utf-8"), env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        except OSError:
            continue
        if result.returncode == 0:
            return
    raise VaultError("Clipboard unavailable. Check your desktop session and clipboard tool.")


class Menu:
    def __init__(self, repository: Repository):
        self.repository = repository

    def heads(self) -> list[Revision]:
        vault = self.repository.vault
        return vault.heads(vault.load(self.repository.directory))

    @staticmethod
    def kind(*, home: bool = False) -> str | None:
        index = submenu("Key type", ["Account", "API key"], home=home)
        return None if index is None else ("account", "api")[index]

    def browse(self, *, editing: bool = False) -> None:
        while (kind := self.kind(home=editing)) is not None:
            self.platforms(kind, editing=editing)

    def platforms(self, kind: str, *, editing: bool) -> None:
        while True:
            variants = defaultdict(list)
            for revision in self.heads():
                if revision.entry.kind == kind:
                    variants[revision.entry_id].append(revision)
            groups = defaultdict(list)
            for revisions in variants.values():
                if any(not revision.deleted for revision in revisions):
                    for revision in revisions:
                        if editing or not revision.deleted:
                            groups[platform_key(revision.entry.platform)].append(revision)
            platforms = sorted(groups)
            if not platforms:
                status("No entries for this type.", "33")
            labels = [min(item.entry.platform for item in groups[key]) for key in platforms]
            index = submenu("Platform", [display(label) for label in labels], home=True)
            if index is None:
                return
            self.entries(groups[platforms[index]], variants, editing=editing)

    def entries(self, revisions: list[Revision], variants: dict[str, list[Revision]], *, editing: bool) -> None:
        entries = sorted(revisions, key=lambda item: (item.entry.name.casefold(), item.id))
        while entries:
            counts = Counter(item.entry.name.casefold() for item in entries)
            names = []
            for revision in entries:
                label = display(revision.entry.name)
                if len(variants[revision.entry_id]) > 1:
                    state = "deleted" if revision.deleted else "edited"
                    label += f" ({state} revision {revision.id[:8]})"
                elif counts[revision.entry.name.casefold()] > 1:
                    label += f" ({revision.id[:8]})"
                names.append(label)
            index = submenu("Key name", names, home=True)
            if index is None:
                return
            revision = entries[index]
            if editing:
                if self.entry_actions(revision, variants[revision.entry_id]):
                    return
            else:
                self.view(revision.entry)

    @staticmethod
    def view(entry: Entry) -> None:
        heading(f"{display(entry.platform)} / {display(entry.name)}")
        fields = [("ID", entry.login), ("Password", entry.secret)] if entry.kind == "account" else [
            ("API key", entry.secret),
        ]
        field_width = max(len(label) for label, _ in fields)
        print()
        for label, value in fields:
            print(f"  {color(label.ljust(field_width), '1')}  {display(value)}")
        copy_options = ["Copy ID", "Copy password"] if entry.kind == "account" else ["Copy key"]
        while (index := submenu("Continue?", copy_options, home=True, exit_menu=True)) is not None:
            try:
                copy_to_clipboard(fields[index][1])
            except VaultError as error:
                error_message(error)
            else:
                status("Copied to clipboard.", "32")

    def add(self) -> None:
        while (kind := self.kind()) is not None:
            heading("Add account" if kind == "account" else "Add API key")
            print()
            platforms = {}
            same_type = {}
            for revision in self.heads():
                if not revision.deleted:
                    name = revision.entry.platform
                    key = platform_key(name)
                    platforms.setdefault(key, name)
                    if revision.entry.kind == kind:
                        same_type.setdefault(key, name)
            platforms.update(same_type)
            platform = ask_platform([platforms[key] for key in sorted(platforms) if platforms[key].isprintable()])
            platform = platforms.get(platform_key(platform), platform)
            name = ask("Account name: " if kind == "account" else "Key name: ")
            login = ask("ID: ") if kind == "account" else ""
            secret = ask("Password: " if kind == "account" else "API key: ", strip=False)
            entry = Entry(kind, platform, name, login, secret)
            self.save_entry(entry, [])
            finished()

    def entry_actions(self, revision: Revision, variants: list[Revision]) -> bool:
        entry = revision.entry
        while True:
            action = submenu(
                f"{display(entry.platform)} / {display(entry.name)}", ["Edit", "Delete"], home=True,
            )
            if action is None:
                return False
            if action == 0:
                self.edit(revision, variants)
                finished()
                return True
            if submenu("Delete this entry?", ["Confirm delete"]) == 0:
                self.save_entry(entry, variants, deleted=True)
                finished()
                return True

    def edit(self, revision: Revision, variants: list[Revision]) -> None:
        entry = revision.entry
        heading("Edit account" if entry.kind == "account" else "Edit API key")
        if len(variants) > 1:
            status("Saving resolves concurrent revisions using the selected version and your edits.", "33")
        print()
        platform = ask(f"Platform [{display(entry.platform)}]: ", optional=True)
        name = ask(f"Name [{display(entry.name)}]: ", optional=True)
        login = ask("New ID [Enter to keep]: ", optional=True) if entry.kind == "account" else ""
        prompt = "New password" if entry.kind == "account" else "New API key"
        secret = ask(f"{prompt} [Enter to keep]: ", optional=True, strip=False)
        updated = replace(
            entry, platform=platform or entry.platform, name=name or entry.name,
            login=login or entry.login, secret=secret or entry.secret,
        )
        if updated != entry or len(variants) > 1:
            self.save_entry(updated, variants)

    def save_entry(self, entry: Entry, prior: list[Revision], *, deleted: bool = False) -> None:
        self.repository.vault.save(self.repository.directory, entry, prior, deleted=deleted)
        if deleted:
            self.repository.vault.prune_deleted(self.repository.directory)
        status("Deleted locally." if deleted else "Saved locally.", "32")
        try:
            if self.repository.remote():
                self.sync_vault()
        except (VaultError, OSError, ValueError) as error:
            error_message(error)
            status("Local change kept. Use Sync vault to retry.", "33")

    def settings(self) -> None:
        while True:
            action = submenu("Settings", ["Add key", "Edit key", "Vault settings"])
            if action is None:
                return
            if action == 0:
                self.add()
            elif action == 1:
                self.browse(editing=True)
            else:
                self.vault_settings()

    def vault_settings(self) -> None:
        while True:
            remote = self.repository.remote() or "No connection"
            option = submenu(f"Vault repository: {display(remote)}", ["Edit repository"], home=True)
            if option is None:
                return
            self.repository.connect(ask("Repository address: "))
            status("Repository connected.", "32")

    def run(self) -> None:
        actions = (self.browse, self.sync_vault, self.settings)
        while True:
            index = choose(
                f"keyvault ver {__version__}", ["View keys", "Sync vault", "Settings", "Exit"],
            )
            if index == 3:
                return
            try:
                actions[index]()
            except ReturnHome:
                continue
            except ExitKeyv:
                return
            except (VaultError, OSError, ValueError) as error:
                error_message(error)

    def sync_vault(self) -> None:
        if self.repository.sync():
            status("Vault updated.", "32")
        else:
            status("No changes.", "33")


def main() -> int:
    parser = argparse.ArgumentParser(description="Encrypted account and API key vault")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args()
    try:
        root = state_home()
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
            raise VaultError(f"Vault state must be a private directory owned by you: {root}")
        fd = os.open(root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise VaultError("Another keyv session is using this vault.") from None
            master = load_master_key(root)
            repository = Repository(root, Vault(master))
            repository.vault.load(repository.directory)
            Menu(repository).run()
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    except (VaultError, OSError, ValueError) as error:
        error_message(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
