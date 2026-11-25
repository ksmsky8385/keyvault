#!/bin/sh
set -eu

exec python3 - <<'PYTHON'
import getpass
import os
import stat
import sys
import tempfile
import warnings
from pathlib import Path

configured = Path(os.environ.get("XDG_STATE_HOME", ""))
base = configured if configured.is_absolute() else Path.home() / ".local" / "state"
root = base / "keyv"
destination = root / "master-key"

try:
    if os.path.lexists(destination):
        raise FileExistsError(f"Master key file already exists: {destination}")

    warnings.simplefilter("error", getpass.GetPassWarning)
    key = getpass.getpass("Master key: ")
    if not key or any(char in key for char in "\r\n\x00"):
        raise ValueError("Enter a non-empty master key on one line.")
    if getpass.getpass("Repeat master key: ") != key:
        raise ValueError("The master keys do not match.")

    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"Vault state must be a private directory owned by you: {root}")

    with tempfile.NamedTemporaryFile(dir=root, mode="w", encoding="utf-8") as key_file:
        os.fchmod(key_file.fileno(), 0o600)
        key_file.write(key + "\n")
        key_file.flush()
        os.fsync(key_file.fileno())
        os.link(key_file.name, destination)
except getpass.GetPassWarning:
    sys.exit("A terminal is required for hidden input.")
except (EOFError, KeyboardInterrupt):
    sys.exit("Master key setup cancelled.")
except (OSError, ValueError) as error:
    sys.exit(str(error))

print(f"Master key file created: {destination}")
if os.environ.get("KEYV_MASTER"):
    print("Remove KEYV_MASTER from your shell configuration and open a new shell to use the file.")
PYTHON
