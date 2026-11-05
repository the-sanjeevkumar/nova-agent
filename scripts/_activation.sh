# Sourced, never executed: how a POSIX shell gets the Nova dev environment.
# `activate` (interactive, reversible) and `scripts/run-in-nova-env` (a child
# process, one-way) both build on it; neither owns any of it.
#
#   nova_sync REPO [FLAG]         bring the install up to date (setup-nova.sh)
#   nova_compose_env REPO DIALECT print the composed environment, `sh` or `fish`
#   nova_apply_env SCRIPT         evaluate a composed `sh` script into this shell
#   nova_activation_current REPO  is the inherited environment still REPO's?
#   nova_ensure_env REPO          sync, compose and apply unless it is current
#
# pm records, beside the installed-state file named by __NOVA_ACTIVATED, one
# stamp per dependency input carrying the exact mtime that input had when the
# install was last verified against it (pm.environments.record_activation_inputs).
# Any input whose mtime DIFFERS from its stamp (newer or older, since a branch
# switch can move it either way) means the inherited environment may not match
# its inputs. `-nt`/`-ot` are bash builtins, so the check costs no process
# spawn, and every successful sync re-records, so one re-sync settles it.
# Missing stamps or inputs, a dangling sentinel or a literal "1" all read as
# stale, and so does an install that did not build the test environment:
# every environment this library composes includes it.

# nova_sync REPO [--test-environment[=EXTRAS]]: run setup in a child, so a
# failure cannot exit or half-change the caller's shell and setup never
# republishes launchers or shell config. Progress goes to stderr.
nova_sync() {
    local repo="$1" test_environment="${2:---test-environment}"
    (
        unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
        bash "$repo/setup-nova.sh" --runtime-only "$test_environment"
    ) >&2
}

# nova_bootstrap_python REPO: print the interpreter that can run pm before
# any dependency is importable. It only emits the environment; it installs nothing.
nova_bootstrap_python() {
    local repo="$1" store candidate
    for candidate in "$repo/.venv/bin/python" "$repo/.venv/Scripts/python.exe" \
                     "$repo/venv/bin/python" "$repo/venv/Scripts/python.exe"; do
        [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    for store in "${NOVA_RUNTIME_DIR:-}" "$repo/../tools" "${NOVA_HOME:-$HOME/.nova}/tools"; do
        [ -n "$store" ] || continue
        for candidate in "$store"/python-*/bin/python3 "$store"/python-*/python.exe \
                         "$store"/python-*/bin/python "$store"/python-*/bin/python.exe; do
            [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
        done
    done
    return 1
}

# nova_compose_env REPO DIALECT: print the environment of the installed state
# as a script for a shell of DIALECT (`sh` or `fish`).
nova_compose_env() {
    local repo="$1" dialect="$2" python script
    python="$(nova_bootstrap_python "$repo")" || {
        echo "no bootstrap Python found; run setup-nova.sh" >&2
        return 1
    }
    script="$(PYTHONHOME= PYTHONPATH="$repo" "$python" -m pm.environments --format "$dialect")" &&
        [ -n "$script" ] || {
        echo "could not read pm env (run ./setup-nova.sh first)" >&2
        return 1
    }
    printf '%s\n' "$script"
}

# nova_apply_env SCRIPT: evaluate a script from `nova_compose_env REPO sh`.
nova_apply_env() {
    # Resolved before the eval replaces PATH.
    local cygpath name
    cygpath="$(command -v cygpath 2>/dev/null || :)"
    eval "$1"
    # The MSYS/Cygwin runtime hands a native Windows Python these variables in
    # Windows form (`C:\a;C:\b`) and converts them back only for its own
    # children. Exported verbatim, bash splits PATH on ':' and finds no commands.
    if [ -n "$cygpath" ]; then
        PATH="$("$cygpath" -u -p "$PATH")"
        for name in HOME TMPDIR TMP TEMP; do
            if [ -n "${!name-}" ]; then
                printf -v "$name" '%s' "$("$cygpath" -u "${!name}")"
            fi
        done
    fi
}

# nova_activation_current REPO: status 0 when the inherited environment
# matches REPO's current inputs.
nova_activation_current() {
    local repo="$1" sentinel stamps stamp input stamped=0
    [ -n "${__NOVA_ACTIVATED:-}" ] && [ -e "${__NOVA_ACTIVATED}" ] || return 1
    # activate.ps1 records a Windows path; Git Bash accepts it with slashes.
    sentinel="${__NOVA_ACTIVATED//\\//}"
    stamps="${sentinel%/*}/inputs"
    [ -f "$stamps/.project-root" ] && [ -f "$stamps/.test-environment" ] || return 1
    local owner="$repo" recorded
    recorded="$(< "$stamps/.project-root")"
    if command -v cygpath >/dev/null 2>&1; then
        owner="$(cygpath -am "$repo")" || return 1
        recorded="${recorded//\\//}"
        [ "${owner,,}" = "${recorded,,}" ] || return 1
    fi
    [ "$owner" = "$recorded" ] || return 1
    [ -f "$stamps/.test-environment" ] || return 1
    recorded="$(< "$stamps/.test-environment")"
    [ "$recorded" = "${2:---test-environment}" ] || return 1
    while IFS= read -r -d '' stamp && [ -n "$stamp" ]; do
        input="${stamp%%=*}"
        stamped="${stamp#*=}"
        [ -e "$input" ] || return 1
        [ "$input" -nt "$stamped" ] && return 1
        [ "$input" -ot "$stamped" ] && return 1
        stamped=1
    done < "$stamps/inputs"
    [ "$stamped" = 1 ] || return 1
    return 0
}

# nova_ensure_env REPO [DIALECT [TEST_ENV]]: sync, compose, apply if not current.
nova_ensure_env() {
    local repo="$1" dialect="${2:-sh}" test_env="${3:---test-environment}"
    nova_activation_current "$repo" "$test_env" && return 0
    nova_sync "$repo" "$test_env" || return 1
    local script
    script="$(nova_compose_env "$repo" "$dialect")" || return 1
    nova_apply_env "$script"
    return 0
}

# run-in-nova-env REPO COMMAND...: run a command under the Nova dev environment.
# Usage: . scripts/_activation.sh && nova_ensure_env "$PWD" && run_in_nova_env "$PWD" some command
run_in_nova_env() {
    local repo="$1"
    shift
    nova_ensure_env "$repo" || return 1
    exec "$@"
}
