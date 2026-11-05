# Source this file to sync and apply the PM environment; deactivate restores it.
# Trusts the recorded tool digest. `nova pm install` re-checks the bytes.
# --test-extras a,b selects runtime extras in the test environment (default: [all]).

if set -q argv[1]
    set -l test_extras $argv[1]
else
    set -l test_extras ''
end

set -l repo (dirname (status -f))
set -l bootstrap_saved

# Save and clear bootstrap environment
for key in PYTHONPATH PYTHONHOME VIRTUAL_ENV
    if set -q $key
        set bootstrap_saved[$key] $key
    else
        set bootstrap_saved[$key] ""
    end
    set -e $key
end

# Run setup in a separate process so its exit/failure cannot terminate the sourced shell
set -l shell (ps -p %self -o comm=)
set -l test_args
if test -n "$test_extras"
    set test_args --test-extras $test_extras
end

# Run setup in a child process so its exit/failure cannot exit the shell
set -l setup_status
try
    eval $shell -c "source $repo/setup-nova.fish --runtime-only $test_extras" >&2
    set setup_status $status
catch
    set setup_status 1
end

if test $setup_status -ne 0
    echo "activate: setup failed; shell environment unchanged" >&2
    return 1
end

functions -q deactivate
and deactivate

# Find bootstrap Python
set -l py
for candidate in $repo/.venv/bin/python $repo/venv/bin/python
    if test -x $candidate
        set py $candidate
        break
    end
end

if not set -q py
    set -l roots $NOVA_RUNTIME_DIR $repo/../tools
    if set -q NOVA_HOME
        set roots $roots $NOVA_HOME/tools
    else
        set roots $roots $HOME/.nova/tools
    end

    for root in $roots
        if test -z $root
            continue
        end
        for entry in (ls $root/python-*/bin/python 2>/dev/null)
            set py $entry
            break
        end
        if test -n "$py"
            break
        end
    end
end

if not set -q py
    echo "activate: no bootstrap Python found; run setup-nova.fish" >&2
    return 1
end

# Compose and apply environment
set -l script (python -m pm.environments --format fish)
if test -n "$script"
    eval $script
else
    echo "could not read pm env (run ./setup-nova.fish first)" >&2
    return 1
end

# Guard against double-sourcing
functions -q deactivate
and deactivate

# Snapshot what we are about to change
set -g __NOVA_SAVED_PATH $PATH
set -g __NOVA_KEY_LIST (string match -r '^export ([A-Za-z_][A-Za-z0-9_]*)=' $script | string replace -r '^export ([A-Za-z_][A-Za-z0-9_]*)=.*' '$1')
for k in $__NOVA_KEY_LIST
    if set -q $k
        set -g __NOVA_SAVED_$k "set"
        set -g __NOVA_PRIOR_$k $k
    else
        set -g __NOVA_SAVED_$k ""
        set -g __NOVA_PRIOR_$k ""
    end
end
eval $script

# Worktree detection
set -g __NOVA_WORKTREE (pwd)
set -g __NOVA_WORKTREE_NAME (git -C (pwd) rev-parse --abbrev-ref HEAD 2>/dev/null; or basename (pwd))

# Save prompt
set -g __NOVA_SAVED_PROMPT $fish_prompt
set -g __NOVA_SAVED_PROMPT_COMMAND ""

# Worktree detection
function _nova_worktree_here
    set -l top (git rev-parse --show-toplevel 2>/dev/null)
    if test -z $top
        return 1
    end
    test (realpath $top) = (realpath $__NOVA_WORKTREE)
end

# Nova function
function nova
    _nova_worktree_here; or {
        echo "nova: (pwd) is outside $__NOVA_WORKTREE; refusing (the installed command is hidden while this checkout is active)" >&2
        return 1
    }
    cd $__NOVA_WORKTREE
    if set -q PYTHON
        $PYTHON nova $argv
    else if test -x .venv/bin/python
        .venv/bin/python nova $argv
    else if test -x venv/bin/python
        venv/bin/python nova $argv
    else
        python nova $argv
    end
end

# Prompt function
function __nova_prompt
    set -l base $__NOVA_SAVED_PROMPT
    if _nova_worktree_here
        set fish_prompt "($__NOVA_WORKTREE_NAME) $base"
    else
        set fish_prompt $base
    end
end

# Set up prompt
functions -e fish_prompt
function fish_prompt
    __nova_prompt
end

# Deactivate function
function deactivate
    set -gx PATH $__NOVA_SAVED_PATH
    for k in $__NOVA_KEY_LIST
        set -l was $__NOVA_SAVED_$k
        set -l old $__NOVA_PRIOR_$k
        if test "$was" != "set"
            set -e $k
        else
            set -gx $k $old
        end
        set -e __NOVA_SAVED_$k __NOVA_PRIOR_$k
    end
    if test -z "$__NOVA_SAVED_PROMPT"
        functions -e fish_prompt
    else
        functions -c __NOVA_SAVED_PROMPT fish_prompt
    end
    if test -z "$__NOVA_SAVED_PROMPT_COMMAND"
        functions -e __nova_prompt
    else
        functions -c __NOVA_SAVED_PROMPT_COMMAND __nova_prompt
    end
    set -e __NOVA_SAVED_PATH __NOVA_KEY_LIST __NOVA_ACTIVATED __NOVA_WORKTREE __NOVA_WORKTREE_NAME __NOVA_SAVED_PROMPT __NOVA_SAVED_PROMPT_COMMAND
    functions -e deactivate nova _nova_worktree_here
end

# Remember key list
set -g __NOVA_KEY_LIST $__NOVA_KEY_LIST

# Clean up internal functions
functions -e nova_sync nova_bootstrap_python nova_compose_env nova_apply_env nova_activation_current nova_ensure_env
set -e _NOVA_REPO _nova_script _nova_keys _nova_k _nova_top
