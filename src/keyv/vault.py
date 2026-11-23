"""Encrypted append-only storage and revision selection."""

import json
import os
import re
import stat
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

EVENT_NAME = re.compile(r"([0-9a-f]{32})\.keyv")
ID = re.compile(r"[0-9a-f]{32}")
MAGIC = b"keyv\x01"
MAX_EVENT = 1024 * 1024


class VaultError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    kind: str
    platform: str
    name: str
    login: str
    secret: str


@dataclass(frozen=True)
class Revision:
    id: str
    entry_id: str
    parents: tuple[str, ...]
    entry: Entry
    deleted: bool


def atomic_write(path: Path, contents: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temporary = Path(temp.name)
        try:
            temp.write(contents)
            temp.flush()
            os.fsync(temp.fileno())
            temp.close()
            temporary.replace(path)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)


class Cipher:
    def __init__(self, master: str):
        self.master = master.encode("utf-8")
        self.salt = os.urandom(16)
        self.keys: dict[bytes, AESGCM] = {}

    def key(self, salt: bytes) -> AESGCM:
        if salt not in self.keys:
            if len(self.keys) >= 64:
                self.keys.clear()
            key = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(self.master)
            self.keys[salt] = AESGCM(key)
        return self.keys[salt]

    def encrypt(self, event_id: str, data: bytes) -> bytes:
        nonce = os.urandom(12)
        header = MAGIC + self.salt + nonce
        return header + self.key(self.salt).encrypt(nonce, data, header + event_id.encode())

    def decrypt(self, event_id: str, data: bytes) -> bytes:
        if not data.startswith(MAGIC) or not 49 <= len(data) <= MAX_EVENT:
            raise VaultError("Invalid encrypted vault record.")
        salt, nonce = data[5:21], data[21:33]
        try:
            plain = self.key(salt).decrypt(nonce, data[33:], data[:33] + event_id.encode())
        except InvalidTag:
            raise VaultError("Incorrect master key or damaged vault record.") from None
        self.salt = salt
        return plain


class Vault:
    def __init__(self, master: str):
        self.cipher = Cipher(master)
        self.cache: dict[str, tuple[bytes, Revision]] = {}

    def read(self, event_id: str, data: bytes) -> Revision:
        cached = self.cache.get(event_id)
        if cached and cached[0] == data:
            return cached[1]
        try:
            payload = json.loads(self.cipher.decrypt(event_id, data))
            entry_id = payload["entry_id"]
            parents = payload["parents"]
            deleted = payload.get("deleted", False)
            entry = Entry(**payload["entry"])
            if (
                not ID.fullmatch(entry_id)
                or not isinstance(parents, list)
                or not all(ID.fullmatch(parent) for parent in parents)
                or type(deleted) is not bool
                or (not parents and (entry_id != event_id or deleted))
                or entry.kind not in ("account", "api")
                or not all(isinstance(value, str) for value in vars(entry).values())
                or not all((entry.platform, entry.name, entry.secret))
                or (entry.kind == "account" and not entry.login)
            ):
                raise ValueError
            revision = Revision(event_id, entry_id, tuple(parents), entry, deleted)
        except (TypeError, ValueError, KeyError, AttributeError):
            raise VaultError("Invalid vault revision.") from None
        self.cache[event_id] = (data, revision)
        return revision

    def heads(self, revisions: dict[str, Revision]) -> list[Revision]:
        children = defaultdict(list)
        pending = {}
        for revision in revisions.values():
            pending[revision.id] = len(revision.parents)
            for parent in revision.parents:
                if parent not in revisions or revisions[parent].entry_id != revision.entry_id:
                    raise VaultError("Vault revision history is incomplete.")
                children[parent].append(revision.id)
        ready = [event_id for event_id, count in pending.items() if not count]
        visited = 0
        while ready:
            visited += 1
            for child in children.get(ready.pop(), ()):
                pending[child] -= 1
                if pending[child] == 0:
                    ready.append(child)
        if visited != len(revisions):
            raise VaultError("Vault revision history contains a cycle.")
        return [revision for revision in revisions.values() if revision.id not in children]

    def load(self, directory: Path) -> dict[str, Revision]:
        events = directory / "events"
        if events.is_symlink() or (events.exists() and not events.is_dir()):
            raise VaultError("Invalid vault directory.")
        revisions = {}
        if events.exists():
            for path in sorted(events.iterdir()):
                match = EVENT_NAME.fullmatch(path.name)
                info = path.lstat()
                if not match or not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EVENT:
                    raise VaultError("Invalid file in the vault events directory.")
                event_id = match[1]
                revisions[event_id] = self.read(event_id, path.read_bytes())
        self.heads(revisions)
        return revisions

    def save(
        self, directory: Path, entry: Entry, prior: list[Revision], *, deleted: bool = False,
    ) -> None:
        event_id = uuid4().hex
        payload = {
            "entry_id": prior[0].entry_id if prior else event_id,
            "parents": sorted(revision.id for revision in prior),
            "entry": vars(entry),
        }
        if deleted:
            payload["deleted"] = True
        data = self.cipher.encrypt(event_id, json.dumps(payload, ensure_ascii=False).encode())
        if len(data) > MAX_EVENT:
            raise VaultError("Vault entry is too large.")
        events = directory / "events"
        events.mkdir(mode=0o700, exist_ok=True)
        atomic_write(events / f"{event_id}.keyv", data)
