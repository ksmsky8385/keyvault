# keyv

A terminal vault for website accounts and API keys. Account names, platform
names, IDs, passwords and API keys are encrypted on disk.

## Install

Requires Python 3.10+ and Git on Linux or macOS. Run the install commands
from the package directory.

If your Python permits user-site installs, use pip directly:

```sh
python3 -m pip install --user .
```

The command is normally installed in `~/.local/bin`. If your OS marks Python
as externally managed, install into a virtual environment with pip and expose
the command in the same directory:

```sh
python3 -m venv ~/.local/share/keyv/venv
~/.local/share/keyv/venv/bin/python -m pip install .
mkdir -p ~/.local/bin
ln -s ~/.local/share/keyv/venv/bin/keyv ~/.local/bin/keyv
```

Alternatively, run `pipx install .` if you already use pipx.
Make sure `~/.local/bin` is on your PATH, then run `keyv`.

## Open and use the vault

- **View keys** lists platforms alphabetically within each type. Pick a
  platform and entry by number to reveal its decrypted contents in the terminal.
  Choose **Copy ID** or **Copy password** for an account, or **Copy key** for an
  API key. After copying to the system clipboard, the same menu appears again.
  Copying uses `pbcopy` on macOS, `wl-copy` on Wayland, or `xclip` or `xsel` on X11.
- **Sync vault** fetches and merges the repository’s default branch and pushes
  local revisions. With a repository connected, keyv also syncs automatically
  after you add, edit, or delete an entry. If automatic sync fails, the local
  change is kept and you can retry with **Sync vault**.
  A new empty repository starts on `main`. Every device needs the same master key.
- **Settings → Add key** stores an account or API key locally. Platform names group
  case-insensitively within each type.
  Press Tab to complete an existing platform name when adding either type.
- **Settings → Edit key** opens the selected entry's **Edit** and **Delete**
  actions. Press Enter to retain a value while editing. Where shown in the
  footer, enter **B** for **Back** or **M** for **Main menu**. Lowercase letters
  also work. **Exit** is available on the main menu and after completing an action.
- **Settings → Vault settings → Edit repository** connects a dedicated private
  GitHub repository. An empty repository works. Enter `OWNER/REPO`
  or its GitHub URL. keyv uses HTTPS and checks the repository is private
  when connecting and updating. Configure a Git commit identity before
  syncing.

Git reads authentication from your configured credential helper. When no
credential is available, Git shows its username and password prompts in
the terminal. Enter your GitHub username and use a personal access token
as the password. A fine-grained token should grant the vault repository
**Contents: Read and write**. After a successful operation Git can remember
the credential according to your helper settings. Git's `store` helper
stores credentials in plaintext. keyv keeps the vault repository URL free
of credentials.

When `KEYV_MASTER` is absent or empty, keyv prompts once per launch with hidden
input. To supply it through your shell, add the following to `~/.zshrc` or
`~/.bashrc` and open a new shell:

```sh
export KEYV_MASTER='replace-with-your-master-passphrase'
```

This stores the passphrase in plaintext in the shell configuration and exports
it to child processes. Keep that file out of Git. Use a strong, unique
passphrase. A new empty vault uses the first passphrase that successfully
saves an entry.

## Storage and synchronization

State lives in `$XDG_STATE_HOME/keyv` or `~/.local/state/keyv`. Each change writes
a separate immutable file with a random name. Records use scrypt-derived
keys and AES-256-GCM. Git transfers encrypted files and exposes their
number and commit timing. Account passwords and API keys are visible while entered and when revealed.

Independent changes merge as separate files. When two devices edit the same
entry before syncing, both revisions appear in the menu. Select the version
you want under **Edit key**, apply any edits and sync again to resolve it.
If deletion and an edit arrive together, choose **Delete** to keep the
entry removed or save an edit to keep it. Deletion hides the entry after
sync across upgraded devices. Upgrade every device to a release that supports deletion before
using **Delete**. Old encrypted revisions remain in Git history. Git commits use
`keyv: update vault` and, when needed, `keyv: merge vault`.

Connecting a different repository copies the current encrypted vault into
the new clone. Previous working copies remain in the private state directory.
Connecting fetches the remote contents. Use **Sync vault** to publish local data.
