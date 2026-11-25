#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
bin_dir="${PIPX_BIN_DIR:-$HOME/.local/bin}"
command_path="$bin_dir/keyv"
venv_dir="$HOME/.local/share/keyv/venv"
pipx_target=

finish() {
    "$command_path" --version
    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *) printf '%s\n' "Add $bin_dir to PATH to run keyv by name." ;;
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
        python3 -m pip install --user --upgrade "$project_dir"
        finish
        exit 0
    fi
    printf '%s\n' "An existing command occupies $command_path." >&2
    exit 1
fi

if { [ ! -L "$command_path" ] || [ -n "$pipx_target" ]; } && command -v pipx >/dev/null 2>&1; then
    if PIPX_BIN_DIR="$bin_dir" pipx install --force "$project_dir"; then
        finish
        exit 0
    fi
    printf '%s\n' "pipx installation failed. Trying pip." >&2
fi

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "Python 3.10 or newer is required." >&2
    exit 1
fi

if [ -n "$pipx_target" ]; then
    venv_dir=$pipx_target
elif [ ! -x "$venv_dir/bin/python" ]; then
    python3 -m venv --without-pip "$venv_dir"
fi

if "$venv_dir/bin/python" -m pip --version >/dev/null 2>&1; then
    "$venv_dir/bin/python" -m pip install --upgrade "$project_dir"
elif python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip --python "$venv_dir/bin/python" install --upgrade "$project_dir"
else
    "$venv_dir/bin/python" -m ensurepip --upgrade
    "$venv_dir/bin/python" -m pip install --upgrade "$project_dir"
fi

mkdir -p "$bin_dir"
if [ ! -L "$command_path" ] && [ ! -e "$command_path" ]; then
    ln -s "$venv_dir/bin/keyv" "$command_path"
fi
finish
