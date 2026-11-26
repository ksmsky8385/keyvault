# keyv

A terminal vault for website accounts and API keys. Account names, platform
names, IDs, passwords and API keys are encrypted on disk.

## Install

Requires Python 3.10+ and Git. Run these commands from the package directory
on Linux, macOS, Android (Termux), or Windows Subsystem for Linux:

```sh
sh ./install.sh
keyv --version
```

Run `sh ./install.sh` again to reinstall keyv from this checkout, even if the
installed version matches.
On Termux, the installer uses Termux Python and installs the system package
`python-cryptography` if needed. The virtual environment can access that package.
If pip needs to build `cffi`, the installer ensures the Termux build tools are
available.
On Linux and macOS, the installer uses pipx when available and falls back to pip
in a virtual environment. The command is placed in `~/.local/bin` by default.
Add that directory to your PATH to run `keyv` by name.

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

keyv reads a non-empty `KEYV_MASTER` first. Otherwise it looks for
`~/.local/state/keyv/master-key` (or `$XDG_STATE_HOME/keyv/master-key`).
If the file is absent, keyv prompts once per launch with hidden input.

To create this file, run `./setup-master-key.sh` from the package directory
and enter the master key twice. For an existing vault, enter the same key
you already use to unlock it. The script keeps input hidden and leaves an
existing file in place.

keyv requires a regular file owned by you with mode `600` and rejects
symbolic links. The file holds the key in plaintext and lives outside
the vault repository. Remove any old `KEYV_MASTER` export from your
shell configuration and start a new shell to use the file.

## Storage and synchronization

State lives in `$XDG_STATE_HOME/keyv` or `~/.local/state/keyv`. Each change writes
a separate immutable file with a random name. Records use scrypt-derived
keys and AES-256-GCM. Git transfers encrypted files and exposes their
number and commit timing. Account passwords and API keys are visible while entered and when revealed.

Independent changes merge as separate files. When two devices edit the same
entry before syncing, both revisions appear in the menu. Select the version
you want under **Edit key**, apply any edits and sync again to resolve it.
Deleting an account or key removes its value-bearing files locally and from
the current repository tree on sync. A small encrypted marker with no account
or key values remains and prevents an offline edit from restoring the deleted
entry. Update all devices to a build with this deletion behavior before
syncing deletions. Earlier commits and saved working copies can still contain
the original encrypted files. Git commits use `keyv: update vault` and, when
needed, `keyv: merge vault`.

Connecting a different repository copies the current encrypted vault into
the new clone. Previous working copies remain in the private state directory.
Connecting fetches the remote contents. Use **Sync vault** to publish local data.
