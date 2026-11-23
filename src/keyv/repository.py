"""Local vault state and Git synchronization."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .github import GitHubCredential, GitHubRepo
from .vault import EVENT_NAME, MAX_EVENT, Vault, VaultError, atomic_write


class GitError(VaultError):
    pass


def git(
    directory: Path, *args: str, allowed: tuple[int, ...] = (0,),
    credential: GitHubCredential | None = None, input_data: bytes | None = None,
    show_prompt: bool = False,
):
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("GIT_") and name not in {"GIT_ASKPASS", "GIT_TERMINAL_PROMPT"}:
            env.pop(name)
    env.pop("KEYV_MASTER", None)
    env["GIT_ALLOW_PROTOCOL"] = "https"
    if credential is not None:
        settings = {
            "credential.helper": "",
            "http.extraHeader": "",
            f"http.{credential.repository.url}.extraHeader": credential.header,
            f"http.{credential.repository.url}.followRedirects": "false",
            f"http.{credential.repository.url}.sslVerify": "true",
        }
        env["GIT_CONFIG_COUNT"] = str(len(settings))
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = ""
        for index, (key, value) in enumerate(settings.items()):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
         "-c", "merge.autoStash=false", "-c", "core.autocrlf=false", "-C", str(directory), *args],
        env=env, input=input_data, stdout=subprocess.PIPE,
        stderr=None if show_prompt else subprocess.PIPE, check=False,
    )
    if result.returncode not in allowed:
        if credential is not None or show_prompt:
            raise GitError(f"Git {args[0]} failed. Check GitHub access and repository permissions.")
        detail = result.stderr.decode(errors="replace").strip()
        raise GitError(f"Git {args[0]} failed. {detail!r}")
    return result


class Repository:
    def __init__(self, root: Path, vault: Vault):
        self.root = root
        self.vault = vault
        self.pointer = root / "active"
        name = self.pointer.read_text().strip() if self.pointer.exists() else "vault"
        if not re.fullmatch(r"vault(?:-[\w-]+)?", name):
            raise VaultError("Invalid active vault setting.")
        self.directory = root / name
        if self.directory.is_symlink():
            raise VaultError("Invalid active vault directory.")
        self.directory.mkdir(exist_ok=True, mode=0o700)

    def remote(self) -> str:
        if not (self.directory / ".git").is_dir():
            return ""
        return git(self.directory, "config", "--local", "--get", "remote.origin.url").stdout.decode().strip()

    def credential(self, address: str) -> GitHubCredential:
        repository = GitHubRepo.parse(address)
        result = git(
            self.directory, "credential", "fill", input_data=repository.credential_request,
            show_prompt=True,
        )
        credential = GitHubCredential.parse(repository, result.stdout)
        credential.verify_private()
        return credential

    def approve_credential(self, credential: GitHubCredential) -> None:
        git(self.directory, "credential", "approve", input_data=credential.data, show_prompt=True)

    def connect(self, address: str) -> None:
        credential = self.credential(address)
        self.connect_private(credential)
        self.approve_credential(credential)

    def connect_private(self, credential: GitHubCredential) -> None:
        address = credential.repository.url
        if self.remote() == address:
            return
        local = self.vault.load(self.directory)
        candidate = Path(tempfile.mkdtemp(prefix="vault-", dir=self.root))
        published = False
        try:
            git(self.root, "clone", "--no-local", "--", address, str(candidate), credential=credential)
            if not self.has_head(candidate):
                git(candidate, "symbolic-ref", "HEAD", "refs/heads/main")
            remote = self.vault.load(candidate)
            events = candidate / "events"
            for event_id in local:
                filename = f"{event_id}.keyv"
                data = (self.directory / "events" / filename).read_bytes()
                if event_id in remote:
                    if (events / filename).read_bytes() != data:
                        raise VaultError("The repositories contain different records with the same ID.")
                else:
                    events.mkdir(exist_ok=True, mode=0o700)
                    atomic_write(events / filename, data)
            self.vault.load(candidate)
            atomic_write(self.pointer, f"{candidate.name}\n".encode())
            published = True
            self.directory = candidate
        finally:
            if not published and (
                not self.pointer.exists() or self.pointer.read_text().strip() != candidate.name
            ):
                shutil.rmtree(candidate)

    @staticmethod
    def has_head(directory: Path) -> bool:
        return git(directory, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1)).returncode == 0

    def remote_branch(self, credential: GitHubCredential) -> str:
        url = credential.repository.url
        result = git(self.directory, "ls-remote", "--symref", url, "HEAD", credential=credential)
        for line in result.stdout.decode().splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                return line.removeprefix("ref: refs/heads/").split("\t", 1)[0]
        if git(self.directory, "ls-remote", "--heads", url, credential=credential).stdout:
            raise VaultError("The remote default branch is unavailable.")
        return git(self.directory, "symbolic-ref", "--short", "HEAD").stdout.decode().strip()

    def validate_tree(self, ref: str) -> dict[str, str]:
        revisions = {}
        objects = {}
        listing = git(self.directory, "ls-tree", "-rzt", "--full-tree", ref, "--", "events")
        for record in listing.stdout.split(b"\0"):
            if not record:
                continue
            metadata, filename = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode().split()
            if mode == "040000" and filename == b"events":
                continue
            name = os.fsdecode(filename).removeprefix("events/")
            match = EVENT_NAME.fullmatch(name)
            if not match or kind != "blob" or mode not in ("100644", "100755"):
                raise VaultError("Invalid file in the remote vault events directory.")
            if int(git(self.directory, "cat-file", "-s", object_id).stdout) > MAX_EVENT:
                raise VaultError("Remote vault record is too large.")
            data = git(self.directory, "cat-file", "blob", object_id).stdout
            revisions[match[1]] = self.vault.read(match[1], data)
            objects[match[1]] = object_id
        self.vault.heads(revisions)
        return objects

    def commit_local(self) -> None:
        if not (self.directory / "events").exists():
            return
        git(self.directory, "add", "-f", "--", "events")
        changes = git(self.directory, "diff", "--cached", "--quiet", "--", "events", allowed=(0, 1))
        if changes.returncode:
            git(self.directory, "commit", "--only", "-m", "keyv: update vault", "--", "events")

    def sync(self) -> bool:
        address = self.remote()
        if not address:
            raise VaultError("No connection. Set a vault repository first.")
        credential = self.credential(address)
        updated = self.sync_private(credential)
        self.approve_credential(credential)
        return updated

    def sync_private(self, credential: GitHubCredential) -> bool:
        directory = self.directory
        url = credential.repository.url
        self.vault.load(directory)
        if git(directory, "rev-parse", "--verify", "--quiet", "MERGE_HEAD", allowed=(0, 1)).returncode == 0:
            raise VaultError("A Git merge is already in progress in the active vault.")
        if git(directory, "diff", "--quiet", "--", allowed=(0, 1)).returncode:
            raise VaultError("Tracked vault files have local modifications.")
        if git(directory, "diff", "--cached", "--quiet", "--", allowed=(0, 1)).returncode:
            # A previous commit may have failed after staging append-only records.
            staged = git(directory, "diff", "--cached", "--name-status", "-z").stdout.split(b"\0")
            if any(
                status != b"A" or not path.startswith(b"events/")
                or not EVENT_NAME.fullmatch(os.fsdecode(path[7:]))
                for status, path in zip(staged[::2], staged[1::2])
            ):
                raise VaultError("The vault has unexpected staged Git changes.")
        original_head = git(directory, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1)).stdout
        self.commit_local()
        for attempt in range(3):
            branch = self.remote_branch(credential)
            ref = f"refs/remotes/origin/{branch}"
            git(
                directory, "fetch", "--prune", "--no-tags", url,
                "+refs/heads/*:refs/remotes/origin/*", credential=credential,
            )
            exists = git(directory, "rev-parse", "--verify", "--quiet", ref, allowed=(0, 1)).returncode == 0
            if exists:
                remote = self.validate_tree(ref)
                if self.has_head(directory):
                    local = self.validate_tree("HEAD")
                    if any(local[event_id] != remote[event_id] for event_id in local.keys() & remote.keys()):
                        raise VaultError("An encrypted record differs between the two histories.")
                    base = git(directory, "merge-base", "HEAD", ref, allowed=(0, 1)).stdout.strip()
                    if base:
                        for tip in ("HEAD", ref):
                            changed = git(
                                directory, "diff", "--no-renames", "--diff-filter=DMRT",
                                "--name-only", base.decode(), tip, "--", "events",
                            ).stdout
                            if changed:
                                raise VaultError("An existing vault record was removed or changed in Git.")
                    try:
                        git(directory, "merge", "--allow-unrelated-histories", "-m", "keyv: merge vault", ref)
                    except GitError:
                        if git(directory, "rev-parse", "--verify", "--quiet", "MERGE_HEAD", allowed=(0, 1)).returncode == 0:
                            git(directory, "merge", "--abort")
                        raise
                else:
                    git(directory, "reset", "--keep", ref)
            head = git(directory, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1)).stdout
            if not head:
                return False
            if exists and head == git(directory, "rev-parse", ref).stdout:
                return head != original_head
            try:
                result = git(directory, "push", "--porcelain", url, f"HEAD:refs/heads/{branch}", credential=credential)
                return head != original_head or any(
                    line.startswith((b" \t", b"*\t")) for line in result.stdout.splitlines()
                )
            except GitError:
                current = git(directory, "ls-remote", "--heads", url, f"refs/heads/{branch}", credential=credential).stdout
                old = git(directory, "rev-parse", ref).stdout.strip() if exists else b""
                if attempt == 2 or current.partition(b"\t")[0] == old:
                    raise
