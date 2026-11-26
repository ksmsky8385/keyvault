#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
bin_dir="${PIPX_BIN_DIR:-$HOME/.local/bin}"
command_path="$bin_dir/keyv"
venv_dir="$HOME/.local/share/keyv/venv"
pipx_target=
legacy_install=
termux_pkg=
python_command=python3

if [ -n "${PREFIX:-}" ] && [ -x "$PREFIX/bin/pkg" ]; then
    termux_pkg="$PREFIX/bin/pkg"
    python_command="$PREFIX/bin/python3"
fi

has_cryptography_package() {
    "$1" -c '
from importlib.metadata import version

raise SystemExit(int(version("cryptography").split(".", 1)[0]) < 41)
' >/dev/null 2>&1
}

finish() {
    "$command_path" --version
    active_command=$(command -v keyv || :)
    case "$active_command" in
        "$command_path") ;;
        '') printf '%s\n' "Add $bin_dir to PATH to run keyv by name." ;;
        *) printf '%s\n' "Your PATH resolves keyv to $active_command. Put $bin_dir first to use the updated version." ;;
    esac
}

if [ -L "$command_path" ]; then
    target=$(readlink "$command_path")
    case "$target" in
        "$venv_dir/bin/keyv") ;;
        /*/venvs/keyv/bin/keyv)
            pipx_target=${target%/bin/keyv}
            if [ ! -f "$pipx_target/pipx_metadata.json" ]; then
                printf '%s\n' "Cannot confirm ownership of $command_path." >&2
                exit 1
            fi
            ;;
        *)
            printf '%s\n' "An existing command occupies $command_path." >&2
            exit 1
            ;;
    esac
elif [ -e "$command_path" ]; then
    if [ "$bin_dir" = "$HOME/.local/bin" ] && [ -f "$command_path" ] &&
        grep -Fxq 'from keyv.cli import main' "$command_path"; then
        legacy_install=1
    else
        printf '%s\n' "An existing command occupies $command_path." >&2
        exit 1
    fi
fi

if [ -z "$termux_pkg" ] && [ -z "$legacy_install" ] &&
    { [ ! -L "$command_path" ] || [ -n "$pipx_target" ]; } &&
    command -v pipx >/dev/null 2>&1; then
    if PIPX_BIN_DIR="$bin_dir" pipx install --force "$project_dir"; then
        finish
        exit 0
    fi
    printf '%s\n' "pipx installation failed. Trying pip." >&2
fi

if ! command -v "$python_command" >/dev/null 2>&1 ||
    ! "$python_command" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    printf '%s\n' "Python 3.10 or newer is required." >&2
    exit 1
fi

# Installing from the checkout path makes pip reinstall keyv even at the
# same version. Let pip reuse dependencies that already satisfy requirements.
set -- --upgrade
if [ -n "$termux_pkg" ]; then
    if ! has_cryptography_package "$python_command"; then
        "$termux_pkg" install python-cryptography
        if ! has_cryptography_package "$python_command"; then
            printf '%s\n' 'Termux python-cryptography version 41 or newer is required.' >&2
            exit 1
        fi
    fi
    # pip may need to build cffi even when cryptography itself is prebuilt.
    if [ ! -x "$PREFIX/bin/clang" ] || [ ! -x "$PREFIX/bin/pkg-config" ] ||
        [ ! -f "$PREFIX/include/ffi.h" ]; then
        if ! "$python_command" -c 'import _cffi_backend' >/dev/null 2>&1; then
            "$termux_pkg" install clang pkg-config libffi
        fi
    fi
    set -- "$@" --only-binary=cryptography
fi

if [ -n "$legacy_install" ]; then
    "$python_command" -m pip install --user "$@" "$project_dir"
    finish
    exit 0
fi

if [ -n "$pipx_target" ]; then
    venv_dir=$pipx_target
fi
if [ -n "$termux_pkg" ]; then
    "$python_command" -m venv --without-pip --system-site-packages "$venv_dir"
elif [ ! -x "$venv_dir/bin/python" ]; then
    "$python_command" -m venv --without-pip "$venv_dir"
fi

if "$venv_dir/bin/python" -m pip --version >/dev/null 2>&1; then
    "$venv_dir/bin/python" -m pip install "$@" "$project_dir"
elif "$python_command" -m pip --version >/dev/null 2>&1; then
    "$python_command" -m pip --python "$venv_dir/bin/python" install "$@" "$project_dir"
else
    "$venv_dir/bin/python" -m ensurepip --upgrade
    "$venv_dir/bin/python" -m pip install "$@" "$project_dir"
fi

mkdir -p "$bin_dir"
if [ ! -L "$command_path" ] && [ ! -e "$command_path" ]; then
    ln -s "$venv_dir/bin/keyv" "$command_path"
fi
finish
