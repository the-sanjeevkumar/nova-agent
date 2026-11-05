"""Source-install launchers shared by setup, installers, and Windows repair.

Launchers execute store Python in isolated mode. They set the install's
default home and load nova_bootstrap before the entry point. Bootstrap
reads the selected dependency generation at each start.

Windows uses distlib executables or a command-file fallback. POSIX uses
an executable shell wrapper. The standalone writer requires PM's store
interpreter before it publishes either command.
"""

from __future__ import annotations

from contextlib import suppress
import json
import os
import re
import shlex
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pm.environments import owning_home_root, store_root

# ``sys`` attribute a launcher sets before ``import nova_bootstrap``: pin NOVA_HOME to the
# install's default root once the launch-time repair ran (``nova_bootstrap._pin_launcher_home``).
PIN_DEFAULT_HOME_FLAG = "_nova_pin_default_home"


def _inline_string_literal(value: str) -> str:
    """Keep inline Python source intact through Windows PowerShell's native argv quoting."""
    if os.name != "nt":
        return repr(value)
    escaped = value.encode("unicode_escape").decode("ascii")
    return "'" + escaped.replace("'", "\\x27").replace('"', "\\x22") + '"'


def runtime_command(repo_root: Path, args=(), *, module: str = "nova_cli.main",
                    code: str | None = None, python: str | Path | None = None,
                    home: str | Path | None = None) -> list[str]:
    """An installation-bound command, safe to persist across dependency GC.

    Store Python owns the ABI; bootstrap selects and leases dependencies at
    child start. Nix and developer interpreters retain their external owner.
    No selected generation or ambient PYTHONPATH is captured in the command.
    """
    root = Path(repo_root).resolve()
    python = python or resolve_store_python(root) or Path(sys.executable)
    entry = f"exec({_inline_string_literal(code)})" if code is not None else (
        f"runpy.run_module({_inline_string_literal(module)}, run_name='__main__', alter_sys=True)")
    # A literal home is pinned up front; the default one needs ``nova_constants`` from the
    # checkout, so it is pinned only after ``nova_bootstrap``'s launch-time repair (see
    # ``_launcher_script``), here again for a bootstrap that predates that hook.
    if home is not None:
        pin, settle = (f"os.environ['NOVA_HOME'] = os.environ.get('NOVA_HOME') or "
                       f"{_inline_string_literal(str(home))}; "), ""
    else:
        pin = f"sys.{PIN_DEFAULT_HOME_FLAG} = True; "
        settle = ("os.environ.get('NOVA_HOME') or os.environ.__setitem__('NOVA_HOME', "
                  "str(__import__('nova_constants').get_default_nova_root())); ")
    bootstrap = (
        "import os, sys, runpy; "
        "os.environ.pop('PYTHONHOME', None); os.environ.pop('PYTHONPATH', None); "
        "os.environ.pop('VIRTUAL_ENV', None); "
        f"sys.path.insert(0, {_inline_string_literal(str(root))}); "
        + pin
        + "import nova_bootstrap; "
        + settle
        + entry
    )
    return [str(python), "-I", "-c", bootstrap, *args]


def print_runtime_command(repo_root: Path, argv: list[str]) -> None:
    """Machine boundary for consumers holding the exact published launcher."""
    import argparse

    parser = argparse.ArgumentParser(description="Resolve this installation's launch command.")
    parser.add_argument("--module", default="nova_cli.main")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    args = options.args[1:] if options.args[:1] == ["--"] else options.args
    print(json.dumps(runtime_command(repo_root, args, module=options.module)))


def installation_command(repo_root: Path, args=(), *, module: str = "nova_cli.main",
                         python: str | Path | None = None, home: str | Path | None = None) -> list[str]:
    """Persist a source launcher, never the versioned tool it currently uses.

    External/Nix installs retain their externally owned interpreter contract.
    Source installation/update publication refreshes the local launcher when
    the managed Python pin changes.
    """
    root = Path(repo_root)
    if resolve_store_python(root) is None:
        return runtime_command(root, args, module=module, python=python, home=home)
    prefix = [] if module == "nova_cli.main" else ["--run-module", module]
    return [str(root / ".nova" / "bin" / "nova"), *prefix, *args]


#: Launcher command names — keep in lockstep with scripts/install.ps1
#: Publish-UserCommand and nova_cli/_install_repair.py.
WINDOWS_BIN_LAUNCHERS = ("nova", "nova-acp")

#: command name -> (entry module, callable) — mirrors pyproject.toml
#: [project.scripts].
ENTRY_POINTS = {
    "nova": ("nova_cli.main", "main"),
    "nova-acp": ("acp_adapter.entry", "main"),
}


def _is_windows() -> bool:
    return os.name == "nt"


def resolve_store_python(repo_root: Path, *, publication: bool = False) -> Path | None:
    """Read PM's committed Python tool, without adopting unrecorded bytes.

    Callers that PERSIST the result in a launcher (``stage_launcher`` and the
    publication gates below) pass ``publication=True``: the tree's own store
    wins over an inherited ``NOVA_RUNTIME_DIR``. The override names a store
    for the running process (an e2e fixture, a desktop toolchain) that a later
    scratch cleanup may delete, leaving the launcher to exit 127 forever
    (#131745). It still supplies the store when the tree records none.
    """
    if publication:
        own = _store_python(store_root(repo_root, honor_runtime_override=False))
        if own is not None:
            return own
    return _store_python(store_root(repo_root))


def _store_python(runtime: Path) -> Path | None:
    rel = "python.exe" if _is_windows() else "bin/python3"

    facts = runtime / "facts.json"
    if facts.is_file():
        try:
            packages = json.loads(facts.read_text(encoding="utf-8-sig")).get(
                "packages", {}
            )
            entry = (packages.get("python") or {}).get("entry")
        except (OSError, ValueError):
            entry = None
        if entry:
            candidate = runtime / entry / rel
            if candidate.is_file():
                return candidate

    return None


def _load_script_maker():
    """distlib's ScriptMaker — standalone first, then pip's vendored copy."""
    try:
        from distlib.scripts import ScriptMaker

        return ScriptMaker
    except ImportError:
        pass
    try:
        from pip._vendor.distlib.scripts import ScriptMaker

        return ScriptMaker
    except ImportError:
        return None


def _store_script_maker(python_exe: Path):
    """ScriptMaker from the pip that ships with the interpreter being bound.

    A launch activates only the dependency environment, which carries no
    distlib and drops the store's site-packages, so the update that repins
    Python could only publish a .cmd and retire nova.exe -- which Desktop's
    backend sweep, shortcuts and terminal profiles name. python-build-standalone
    ships pip (the installer mints the first nova.exe through it), and its
    vendored distlib is self-contained. It loads under a private name so the
    store's pip never answers an ``import pip`` in this process."""
    package = Path(python_exe).parent / "Lib" / "site-packages" / "pip" / "_vendor" / "distlib"
    if not (package / "scripts.py").is_file():
        return None
    import hashlib
    import importlib
    import importlib.util

    # One private package per store copy: a later repin's pip never reuses the first one's.
    name = f"_nova_store_distlib_{hashlib.sha256(str(package).encode()).hexdigest()[:12]}"
    try:
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                name, package / "__init__.py", submodule_search_locations=[str(package)])
            if spec is None or spec.loader is None:
                return None
            sys.modules[name] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules[name])
        return importlib.import_module(f"{name}.scripts").ScriptMaker
    except (ImportError, OSError, SyntaxError, AttributeError):  # a torn or foreign pip: mint a .cmd
        sys.modules.pop(name, None)
        return None


def exe_is_venv_bound(exe: Path, venv_dir: Path | None) -> bool:
    """True when an existing launcher exe embeds the venv interpreter —
    i.e. it is a copied venv console-script trampoline from the pre-pm
    installer, which boots through ``venv\\Scripts\\python.exe`` and must be
    replaced. distlib launchers append the interpreter shebang after the zip
    payload; both encodings are scanned to be safe."""
    if venv_dir is None:
        return False
    needles = set()
    for interpreter in (
        venv_dir / "Scripts" / "nova.exe",
        venv_dir / "Scripts" / "nova-acp.exe",
        venv_dir / "Scripts" / "python.exe",
        venv_dir / "bin" / "python3",
        venv_dir / "bin" / "nova",
        venv_dir / "bin" / "nova-acp",
    ):
        for enc in ("utf-8", "utf-16-le"):
            try:
                needles.add(str(interpreter).encode(enc))
            except Exception:
                continue
    try:
        data = Path(exe).read_bytes()
    except OSError:
        return False
    return any(needle in data for needle in needles)


def _write_atomic(target: Path, write) -> Path | None:
    """Stage under a pid-suffixed name then os.replace, so a concurrent
    process start never sees a torn launcher."""
    staging = target.with_name(f"{target.name}.stage.{os.getpid()}")
    try:
        write(staging)
        os.replace(staging, target)
        return target
    except OSError:
        try:
            staging.unlink()
        except OSError:
            pass
        return None


def mint_launcher(
    name: str,
    repo_root: Path,
    out_dir: Path,
    python_exe: Path,
    site_packages: Path | None,
) -> Path | None:
    """Write a native launcher with the shared bootstrap script, or return None."""
    module, func = ENTRY_POINTS[name]
    out_dir = Path(out_dir)
    script = _launcher_script(name, Path(repo_root), site_packages)

    if not _is_windows():
        return _mint_shell_launcher(name, out_dir, python_exe, script)

    script_maker_cls = _load_script_maker() or _store_script_maker(python_exe)
    if script_maker_cls is not None:
        class _PathedScriptMaker(script_maker_cls):  # type: ignore[misc,valid-type]
            def _get_script_text(self, entry):
                return script

        import tempfile
        from zipfile import BadZipFile, ZipFile

        def make(directory: Path) -> list[str]:
            maker = _PathedScriptMaker(None, str(directory), add_launchers=True)
            maker.executable = str(python_exe)
            maker.variants = {""}
            maker.clobber = True
            return maker.make(f"{name} = {module}:{func}", {"interpreter_args": ["-I"]})

        # distlib's exe ZIP records the current time, so byte equality cannot
        # detect an unchanged launcher. Compare its loader + shebang and script;
        # leave an active executable alone when only the ZIP timestamp changed.
        with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=out_dir) as staging:
            try:
                candidate = next((Path(p) for p in make(Path(staging))
                                  if Path(p).suffix.lower() == ".exe"), None)
            except Exception:
                candidate = None
            if candidate is not None:
                target = out_dir / candidate.name
                try:
                    with ZipFile(target) as old, ZipFile(candidate) as new:
                        if (old.namelist() == new.namelist() == ["__main__.py"]
                                and target.read_bytes()[:old.infolist()[0].header_offset]
                                == candidate.read_bytes()[:new.infolist()[0].header_offset]
                                and old.read("__main__.py") == new.read("__main__.py")):
                            return target
                except (OSError, BadZipFile, KeyError):
                    pass
                # For changed launchers, distlib's .deleteme replacement can
                # move an executable Windows still has mapped in memory.
                try:
                    written = make(out_dir)
                except Exception:
                    written = []
                for path in written:
                    if Path(path).suffix.lower() == ".exe":
                        return Path(path)
        # distlib ran but produced no exe (unexpected) — fall through to cmd.

    # A prepared app environment need not include distlib, even when the
    # installer used it to publish a native exe. Keep that executable if its
    # embedded interpreter and script still match; replacing it with a cmd
    # would require unlinking the currently running exe on Windows.
    existing = out_dir / f"{name}.exe"
    from zipfile import BadZipFile, ZipFile
    try:
        with ZipFile(existing) as archive:
            if archive.namelist() == ["__main__.py"]:
                prefix = existing.read_bytes()[:archive.infolist()[0].header_offset]
                shebangs = (f"#!{python_exe} -I\n".encode(),
                            f'#!"{python_exe}" -I\n'.encode())
                # Vendored distlib trails the shebang with an extra CRLF
                # before the zip; compare the shebang line itself.
                tail = prefix.rstrip(b"\r\n")
                if (any(tail.endswith(shebang.rstrip(b"\r\n")) for shebang in shebangs)
                        and archive.read("__main__.py") == script.encode("utf-8")):
                    return existing
    except (OSError, BadZipFile, KeyError):
        pass

    # The script is data to Python, not interpolated shell source.
    import base64
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    code = f"import base64; exec(base64.b64decode('{encoded}'))"
    body = (
        "@echo off\r\n"
        f'"{python_exe}" -I -c "{code}" %*\r\n'
    )
    return _write_atomic(out_dir / f"{name}.cmd", lambda p: p.write_text(body, encoding="utf-8"))


# A killed move can tear the repair's own code (``nova_bootstrap``, ``nova_cli``). With the
# marker still there, a launcher whose checkout import failed runs the copy of the repair that the
# updater published beside the marker (``update_cmd_commit.publish_recovery_closure``) once every
# file hashes to the blob id its manifest names; a missing, torn or foreign closure (or none, from
# an updater predating it) is rebuilt from git's objects at the marker's ``pre`` with the recorded
# git or an absolute PATH entry (never one from the current directory) and verified the same way.
# Stdlib plus that copy only, under the same claim and checkout lock (a live writer still refuses,
# which exits 1 with the repair's own reason), then a relaunch from the restored tree.
_CLOSURE_REPAIR = """\
def _nova_closure_repair():
    import contextlib, hashlib, io, shutil, subprocess
    from pathlib import Path
    root = Path(__ROOT__)
    git = root / '.git'
    try:
        if git.is_file():
            text = git.read_text(encoding='utf-8-sig').strip()
            git = root / text[7:].strip() if text.startswith('gitdir:') else git
        fields = dict(line.partition('=')[::2] for line in (git / __MARKER__).read_text(encoding='utf-8-sig').splitlines())
    except OSError:
        return
    pre = fields.get('pre', '').strip()
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', pre):
        return
    closure = git / __CLOSURE_DIR__ / pre
    files = (*__CLOSURE__, __INIT__)

    def blob_id(data):
        return hashlib.new('sha1' if len(pre) == 40 else 'sha256', b'blob %d\\0' % len(data) + data).hexdigest()

    def verified():
        try:
            listed = dict(line.split(' ', 1)[::-1] for line in (closure / __MANIFEST__).read_text(encoding='utf-8-sig').splitlines())
            return (set(listed) == set(files) and listed[__INIT__] == blob_id(b'')
                    and all(blob_id((closure / rel).read_bytes()) == oid for rel, oid in listed.items()))
        except (OSError, ValueError):
            return False

    if not verified():
        exe = fields.get('git', '').strip()
        if not (os.path.isabs(exe) and os.path.isfile(exe)):
            names = ('git.exe',) if os.name == 'nt' else ('git',)
            exe = next((os.path.join(d, n) for d in os.environ.get('PATH', '').split(os.pathsep) if os.path.isabs(d)
                        for n in names if os.path.isfile(os.path.join(d, n))), '')
        if not exe:
            return

        def git_out(*args):
            done = subprocess.run([exe, '--no-replace-objects', '-C', str(root), *args], capture_output=True, timeout=60,
                                  stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if done.returncode != 0:
                raise OSError(done.stderr)
            return done.stdout

        staging = closure.with_name('.%s.%d.launch' % (pre, os.getpid()))
        try:
            ids = {}
            for entry in git_out('ls-tree', '-z', '--full-tree', pre, '--', *__CLOSURE__).split(b'\\0'):
                meta, _, rel = entry.decode('utf-8', 'replace').partition('\\t')
                if meta.split()[1:2] == ['blob']:
                    ids[rel] = meta.split()[2]
            blobs = {__INIT__: b''}
            for rel in __CLOSURE__:
                blobs[rel] = git_out('cat-file', 'blob', ids[rel])
                if blob_id(blobs[rel]) != ids[rel]:  # an object that inflates to bytes pre never had
                    raise OSError('%s does not hash to %s' % (rel, ids[rel]))
            blobs[__MANIFEST__] = ''.join('%s %s\\n' % (blob_id(data), rel) for rel, data in blobs.items()).encode('utf-8')
            for rel, data in blobs.items():
                (staging / rel).parent.mkdir(parents=True, exist_ok=True)
                (staging / rel).write_bytes(data)
            shutil.rmtree(closure, ignore_errors=True)
            os.replace(staging, closure)
        except (OSError, KeyError, subprocess.SubprocessError):
            pass
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        if not verified():
            return
    for name in [n for n in sys.modules if n.split('.')[0] in ('nova_cli', 'nova_bootstrap', 'nova_constants', 'pm')]:
        del sys.modules[name]
    tree = os.path.normcase(os.path.realpath(root))
    sys.path[:] = [str(closure)] + [p for p in sys.path if p and os.path.normcase(os.path.realpath(p)) != tree
                                    and not os.path.normcase(os.path.realpath(p)).startswith(tree + os.sep)]
    sys.dont_write_bytecode = True
    said = io.StringIO()
    restored = False
    try:
        with contextlib.redirect_stderr(said):
            from nova_cli import _early_recovery
            restored = _early_recovery.restore_interrupted_pull(root)
    except Exception as exc:
        said.write('nova: the checkout cannot start after an interrupted `nova update`, and it was not '
                   'repaired: %s\\n' % (exc,))
    if restored:
        sys.stderr.write('nova: the checkout could not start after an interrupted `nova update`; '
                         'repaired it with the recovery code saved before the update.\\n' + said.getvalue())
    sys.stderr.write(said.getvalue() or 'nova: the checkout cannot start after an interrupted `nova update`; '
                     'launch again once the update that owns it finishes.\\n')
    raise SystemExit(1)
"""


def _launcher_script(name: str, repo_root: Path, dependencies: Path | None) -> str:
    module, func = ENTRY_POINTS[name]
    # Profile boot repairs shared launchers: their default must stay at the
    # install's dependency root, not whichever profile triggered publication.
    #
    # The launch-time repair of a checkout a killed update left torn (``nova_bootstrap`` ->
    # ``_early_recovery.restore_interrupted_pull``) runs before ANY other checkout module is
    # imported: a merge killed while writing ``nova_constants.py`` must not stop every launch
    # before the repair. The default-home pin (which needs ``nova_constants``) happens inside
    # ``nova_bootstrap`` right after the repair (``_PIN_DEFAULT_HOME``), and here again for a
    # bootstrap that predates that hook.
    root = str(repo_root.resolve())
    from nova_cli._early_recovery import (
        INTERRUPTED_PULL_MARKER,
        RECOVERY_CLOSURE,
        RECOVERY_CLOSURE_DIR,
        RECOVERY_CLOSURE_INIT,
        RECOVERY_CLOSURE_MANIFEST,
    )

    closure_repair = (_CLOSURE_REPAIR.replace("__MARKER__", repr(INTERRUPTED_PULL_MARKER))
                      .replace("__CLOSURE_DIR__", repr(RECOVERY_CLOSURE_DIR))
                      .replace("__MANIFEST__", repr(RECOVERY_CLOSURE_MANIFEST))
                      .replace("__INIT__", repr(RECOVERY_CLOSURE_INIT))
                      .replace("__CLOSURE__", repr(RECOVERY_CLOSURE))
                      .replace("__ROOT__", repr(root)))  # last: a path is never re-substituted
    return (
        # The repair's own stdlib, bound before the checkout root goes on sys.path: a stdlib-named
        # file in the tree must not run in its place ahead of the closure's verification. The first
        # line stays the shape gateway.status._BOOTSTRAPS recognises as this launcher (#124318).
        "import os, re, sys\n"
        "import contextlib, hashlib, io, pathlib, shutil, subprocess\n"
        "os.environ.pop('PYTHONHOME', None)\n"
        "os.environ.pop('PYTHONPATH', None)\n"
        f"sys.path.insert(0, {root!r})\n"
        + closure_repair +
        "if sys.argv[1:2] == ['--print-runtime-command']:\n"
        "    sys.dont_write_bytecode = True\n"
        "    from pathlib import Path\n"
        "    try:\n"
        "        from nova_cli import _early_recovery\n"
        "    except Exception as exc:\n"
        "        if _nova_closure_repair() or not isinstance(exc, ImportError):\n"
        "            raise\n"
        "        _early_recovery = None  # a tree without the repair: the imports below report real damage\n"
        f"    if _early_recovery is not None and _early_recovery.restore_interrupted_pull(Path({root!r})):\n"
        "        _early_recovery.relaunch_after_restore()\n"
        "    from nova_constants import get_default_nova_root\n"
        "    os.environ['NOVA_HOME'] = os.environ.get('NOVA_HOME') or str(get_default_nova_root())\n"
        "    from nova_cli._launchers import print_runtime_command\n"
        f"    print_runtime_command(Path({root!r}), sys.argv[2:])\n"
        "    sys.exit(0)\n"
        f"sys.{PIN_DEFAULT_HOME_FLAG} = True\n"
        "try:\n"
        "    import nova_bootstrap\n"
        "except Exception:\n"
        "    _nova_closure_repair()\n"
        "    raise\n"
        "if not os.environ.get('NOVA_HOME'):\n"
        "    from nova_constants import get_default_nova_root\n"
        "    os.environ['NOVA_HOME'] = str(get_default_nova_root())\n"
        "if sys.argv[1:2] == ['--run-module']:\n"
        "    import runpy\n"
        "    if len(sys.argv) < 3: sys.exit('nova: --run-module needs a module')\n"
        "    module = sys.argv.pop(2)\n"
        "    del sys.argv[1]\n"
        "    runpy.run_module(module, run_name='__main__', alter_sys=True)\n"
        "    sys.exit(0)\n"
        f"from {module} import {func}\n"
        "sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
        f"sys.exit({func}())\n"
    )


def _write_shell(target: Path, command: list[str]) -> Path | None:
    """Write an idempotent POSIX wrapper that runs ``command`` verbatim (shlex-quoted)."""
    body = f'#!/bin/sh\nexec {shlex.join(command)} "$@"\n'
    try:
        if not target.is_symlink() and target.read_bytes() == body.encode("utf-8"):
            if os.access(target, os.X_OK):
                return target
    except OSError:
        pass

    def write(staging: Path) -> None:
        staging.write_text(body, encoding="utf-8", newline="\n")
        staging.chmod(0o755)

    return _write_atomic(target, write)


def _mint_shell_launcher(name: str, out_dir: Path, python_exe: Path, script: str) -> Path | None:
    """Write a POSIX wrapper exec'ing the entry point through the store interpreter.

    The script is passed as a single shlex-quoted argv entry, so real newlines
    reach ``python -I -c`` intact (repr-style escaping produces literal ``\\n``
    sequences that fail to compile).
    """
    return _write_shell(out_dir / name, [str(python_exe), "-I", "-c", script])


def _mint_windows_launcher(name: str, out_dir: Path, python_exe: Path, script: str) -> Path | None:
    """Write a Windows launcher (.exe via distlib or .cmd fallback)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    script_maker_cls = _load_script_maker() or _store_script_maker(Path(sys.executable))
    if script_maker_cls is not None:
        class _PathedScriptMaker(type(ScriptMaker)):  # type: ignore[misc,valid-type]
            def _get_script_text(self, entry):
                return script

        import tempfile
        from zipfile import BadZipFile, ZipFile

        def make(directory: Path) -> list[str]:
            maker = _PathedScriptMaker(None, str(directory), add_launchers=True)
            maker.executable = str(python_exe)
            maker.variants = {""}
            maker.clobber = True
            return maker.make(f"{name} = {ENTRY_POINTS[name][0]}:{ENTRY_POINTS[name][1]}", {"interpreter_args": ["-I"]})

        # distlib's exe ZIP records the current time, so byte equality cannot
        # detect an unchanged launcher. Compare its loader + shebang and script;
        # leave an active executable alone when only the ZIP timestamp changed.
        with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=out_dir) as staging:
            try:
                candidate = next((Path(p) for p in make(Path(staging))
                                  if Path(p).suffix.lower() == ".exe"), None)
            except Exception:
                candidate = None
            if candidate is not None:
                target = out_dir / candidate.name
                try:
                    with ZipFile(target) as old, ZipFile(candidate) as new:
                        if (old.namelist() == new.namelist() == ["__main__.py"]
                                and target.read_bytes()[:old.infolist()[0].header_offset]
                                == candidate.read_bytes()[:new.infolist()[0].header_offset]
                                and old.read("__main__.py") == new.read("__main__.py")):
                            return target
                except (OSError, BadZipFile, KeyError):
                    pass
                # For changed launchers, distlib's .deleteme replacement can
                # move an executable Windows still has mapped in memory.
                try:
                    written = make(out_dir)
                except Exception:
                    written = []
                for path in written:
                    if Path(path).suffix.lower() == ".exe":
                        return Path(path)
        # distlib ran but produced no exe (unexpected) — fall through to cmd.

    # A prepared app environment need not include distlib, even when the
    # installer used it to publish a native exe. Keep that executable if its
    # embedded interpreter and script still match; replacing it with a cmd
    # would require unlinking the currently running exe on Windows.
    existing = out_dir / f"{name}.exe"
    from zipfile import BadZipFile, ZipFile
    try:
        with ZipFile(existing) as archive:
            if archive.namelist() == ["__main__.py"]:
                prefix = existing.read_bytes()[:archive.infolist()[0].header_offset]
                shebangs = (f"#!{python_exe} -I\n".encode(),
                            f'#!"{python_exe}" -I\n'.encode())
                # Vendored distlib trails the shebang with an extra CRLF
                # before the zip; compare the shebang line itself.
                tail = prefix.rstrip(b"\r\n")
                if (any(tail.endswith(shebang.rstrip(b"\r\n")) for shebang in shebangs)
                        and archive.read("__main__.py") == script.encode("utf-8")):
                    return existing
    except (OSError, BadZipFile, KeyError):
        pass

    # The script is data to Python, not interpolated shell source.
    import base64
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    code = f"import base64; exec(base64.b64decode('{encoded}'))"
    body = (
        "@echo off\r\n"
        f'"{python_exe}" -I -c "{code}" %*\r\n'
    )
    return _write_atomic(out_dir / f"{name}.cmd", lambda p: p.write_text(body, encoding="utf-8"))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test launcher script generation.")
    parser.add_argument("--name", required=True, choices=ENTRY_POINTS.keys())
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    from nova_cli._launchers import resolve_store_python, mint_launcher
    python_exe = resolve_store_python(repo_root) or Path(sys.executable)
    site_packages = Path(sys.executable).parent / "Lib" / "site-packages"
    if not site_packages.is_dir():
        site_packages = None
    result = mint_launcher(args.name, repo_root, out_dir, Path(python_exe), site_packages)
    if result:
        print(f"Created launcher: {result}")
    else:
        print("Failed to create launcher", file=sys.stderr)
        sys.exit(1)

# =============================================================================
# Missing functions expected by other modules
# =============================================================================

def ensure_install_launchers(repo_root: Path, bin_dir: Path) -> list[Path]:
    """Ensure all launchers are installed in bin_dir, return list of written paths."""
    out_dir = Path(bin_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(repo_root)
    python_exe = resolve_store_python(repo_root) or Path(sys.executable)
    written = []
    for name in ENTRY_POINTS:
        result = mint_launcher(name, repo_root, out_dir, python_exe, None)
        if result:
            written.append(result)
    return written


def expose_cli(repo_root: Path, *, create: bool = True) -> dict:
    """Expose CLI on PATH by creating launchers in standard locations."""
    from nova_cli._launchers import ensure_install_launchers, resolve_store_python
    import logging
    import os

    root = Path(repo_root).resolve()
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    try:
        written = ensure_install_launchers(Path("."), bin_dir)
        # Check if PATH already contains bin_dir
        path_env = os.environ.get("PATH", "")
        if str(bin_dir) not in path_env.split(os.pathsep):
            return {"ok": True, "warning": f"Add {bin_dir} to PATH"}
        return {"ok": True, "written": [str(p) for p in written]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def publish_launchers(project_root: Path, *, create: bool = True) -> None:
    """Refresh durable commands; bootstrap repairs only existing PATH exposure."""
    import logging
    from nova_cli._launchers import ENTRY_POINTS, ensure_install_launchers, expose_cli, resolve_store_python
    from nova_cli.steward import read_install_stamp

    root = Path(project_root)
    log = logging.getLogger(__name__)

    # Import here to avoid circular imports
    from nova_cli.steward import read_install_stamp
    from nova_cli.venv_sync import _is_sealed

    if _is_sealed(root):
        log.info("launchers: sealed tree at %s keeps its own", project_root)
        return

    if resolve_store_python(project_root) is None:
        logging.getLogger(__name__).warning("launchers: no managed interpreter under %s; %s not published",
                                           project_root, project_root / ".nova" / "bin")
        return

    written = ensure_install_launchers(project_root, project_root / ".nova" / "bin")
    if len(written) != len(ENTRY_POINTS):
        from pm.package import InstallError
        raise InstallError("launchers", "source launcher publication failed", "retry the source update")

    result = expose_cli(project_root, create=create)
    if not result.get("ok", False):
        logging.getLogger(__name__).warning("CLI exposure failed: %s", result.get("error"))


def stage_launcher(name: str, repo_root: Path, target_dir: Path) -> Path | None:
    """Stage a single launcher for publication."""
    from nova_cli._launchers import mint_launcher, resolve_store_python
    repo_root = Path(repo_root)
    target_dir = Path(target_dir)
    python_exe = resolve_store_python(repo_root) or Path(sys.executable)
    return mint_launcher(name, repo_root, target_dir, python_exe, None)


def owns_user_path(home: Path) -> bool:
    """Check if a path owns the user's nova installation."""
    from nova_cli._launchers import resolve_store_python
    home = Path(home)
    # Check if the home directory contains a nova installation
    return (home / ".nova").exists() or (home / "bin" / "nova").exists()


def _launcher_python(launcher: Path) -> str:
    """Get the python interpreter from a launcher."""
    # Read the shebang or embedded python path from launcher
    try:
        first = launcher.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        if first.startswith("#!"):
            shebang = " ".join(part for part in first[2:].strip().split()
                               if not part.startswith("-"))
            if shebang:
                return shebang
    except Exception:
        pass
    return sys.executable
