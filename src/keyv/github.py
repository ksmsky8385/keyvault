"""GitHub repository identity and private visibility checks."""

import base64
import json
import re
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .vault import VaultError

ADDRESS = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)?"
    r"([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?"
)


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    name: str

    @classmethod
    def parse(cls, value: str):
        match = ADDRESS.fullmatch(value)
        if not match or match[2] in (".", ".."):
            raise VaultError("Enter a GitHub repository as OWNER/REPO or its GitHub URL.")
        return cls(*match.groups())

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"

    @property
    def credential_request(self) -> bytes:
        return f"protocol=https\nhost=github.com\npath={self.owner}/{self.name}.git\n\n".encode()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class GitHubCredential:
    repository: GitHubRepo
    data: bytes = field(repr=False)
    username: bytes = field(repr=False)
    token: bytes = field(repr=False)

    @classmethod
    def parse(cls, repository: GitHubRepo, data: bytes):
        values = dict(line.split(b"=", 1) for line in data.split(b"\n") if b"=" in line)
        username = values.get(b"username", b"")
        token = values.get(b"password", b"")
        if not username or not re.fullmatch(rb"[!-~]+", token):
            raise VaultError("GitHub requires a Git username and personal access token.")
        return cls(repository, data, username, token)

    @property
    def header(self) -> str:
        encoded = base64.b64encode(self.username + b":" + self.token).decode("ascii")
        return f"Authorization: Basic {encoded}"

    def verify_private(self) -> None:
        url = f"https://api.github.com/repos/{self.repository.owner}/{self.repository.name}"
        request = Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self.token.decode("ascii"),
            "X-GitHub-Api-Version": "2026-03-10",
        })
        try:
            with build_opener(NoRedirect()).open(request, timeout=15) as response:
                metadata = json.load(response)
        except HTTPError as error:
            raise VaultError(
                f"GitHub repository verification returned HTTP {error.code}. "
                "Check the repository address and GitHub token access."
            ) from None
        except (URLError, OSError, ValueError):
            raise VaultError("Could not verify repository visibility with GitHub.") from None
        if not isinstance(metadata, dict) or metadata.get("visibility") != "private":
            raise VaultError("Select a private GitHub repository.")
