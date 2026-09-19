"""Things uninstalls leave behind: config/data dirs of removed apps, unowned files
in system paths, .pacnew files, backups, broken links, dead launchers."""

import fnmatch
import os
import re
import shlex
import shutil
import time
from pathlib import Path

from .util import HOME, Action, Section, path_item, age, du, human, mtime, progress, run, tilde, yellow

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
IGNORE_FILE = CONFIG / "omarchy-tidy/ignore"

# Scanned for dirs whose name matches nothing installed.
APP_DIRS = [CONFIG, HOME / ".local/share", HOME / ".local/state", HOME / ".cache"]
# Names that belong to the desktop/session or to XDG itself, not to one package.
BUILTIN = {
    "autostart", "dconf", "systemd", "environment.d", "user-dirs.dirs", "user-dirs.locale",
    "mimeapps.list", "menus", "gtk-2.0", "gtk-3.0", "gtk-4.0", "fontconfig", "fonts", "icons",
    "themes", "applications", "desktop-directories", "mime", "sounds", "keyrings", "recently-used.xbel",
    "trash", "omarchy", "omarchy-tidy", "pulse", "session", "sessions", "xsettingsd", "backgrounds",
    "wallpapers", "bash", "zsh", "fish", "nvim", "vim", "git", "ssh", "gnupg", "pki", "local", "share",
    "state", "cache", "config", "bin", "lib", "include", "etc", "man", "doc", "thumbnails", "logs",
    "tmp", "flatpak", "containers", "go", "java", "npm", "docker", "wireplumber", "pipewire",
    "gvfs-metadata", "tracker3", "ibus", "xdg-desktop-portal", "desktop", "documents", "downloads",
    "music", "pictures", "videos", "templates", "public", "kwalletd", "history", "fontconfig",
    "lesshst", "viminfo", "bashhistory", "pythonhistory", "sqlitehistory", "mariadbhistory",
    "claude", "claudejson", "codex", "copilot",
}
# Dir-name globs -> packages/commands that create them under a different name.
ALIASES = {
    ".eclipse": ["dbeaver", "eclipse"], ".swt": ["dbeaver", "eclipse"],
    ".nv": ["nvidia-utils", "nvidia-open"], "radv_builtin_shaders": ["vulkan-radeon"],
    "qtshadercache-*": ["qt6-base", "qt5-base"], "QtProject": ["qt6-base", "qt5-base"],
    "Valve Corporation": ["steam"], "umu*": ["umu-launcher"], "omaspotify": ["spotify"],
    "Omacom": ["omarchy"], "JNA": ["jre-openjdk", "jdk-openjdk", "dbeaver"],
    "Application Not Responding": ["omarchy"],
}
STOPWORDS = {"com", "org", "net", "io", "app", "apps", "desktop", "linux", "gnome", "kde", "freedesktop",
             "github", "the", "data", "user", "local", "settings", "cache", "config", "share", "bin",
             "lib", "utils", "tools", "client", "server", "daemon", "gtk", "qt", "qt5", "qt6", "python"}
SUFFIXES = ("-bin", "-git", "-desktop", "-beta", "-nightly", "-appimage", "-stable", "-electron",
            "-cli", "-dev", "-origin", "-browser", "-launcher", "-gtk", "-qt")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _parts(name):
    return [norm(p) for p in re.split(r"[.\-_ ]+", name.lstrip(".")) if len(norm(p)) >= 3]


class Known:
    """Every name that could plausibly own a config dir on this system."""

    def __init__(self, ctx):
        tok = set()
        for p in ctx.packages.values():
            for n in {p.name, p.base}:
                tok.add(norm(n))
                stripped = n
                for suf in SUFFIXES:
                    stripped = stripped.removesuffix(suf)
                tok.add(norm(stripped))
                first = re.split(r"[-_.]", n)[0]
                if len(first) >= 4 and norm(first) not in STOPWORDS:
                    tok.add(norm(first))
        for path in ctx.owned_files:
            for prefix in ("/usr/bin/", "/opt/", "/usr/lib/", "/usr/share/", "/etc/", "/etc/xdg/",
                           "/usr/share/applications/", "/usr/lib/systemd/user/"):
                if path.startswith(prefix):
                    comp = path[len(prefix):].split("/", 1)[0]
                    comp = re.sub(r"\.(desktop|service|socket|timer)$", "", comp)
                    tok.add(norm(comp))
                    tok.update(p for p in _parts(comp) if p not in STOPWORDS and len(p) >= 4)
        for d in os.environ.get("PATH", "").split(":"):
            try:
                tok.update(norm(e.name) for e in os.scandir(d))
            except OSError:
                pass
        tok.discard("")
        self.tokens = tok
        self.tokens_raw = set(ctx.packages) | {os.path.basename(p) for p in ctx.owned_files
                                               if p.startswith("/usr/bin/")}
        self.long = {t for t in tok if len(t) >= 4}

    def match(self, name):
        for glob, owners in ALIASES.items():
            if fnmatch.fnmatch(name, glob) and any(o in self.tokens_raw for o in owners):
                return True
        n = norm(name)
        if not n or n in BUILTIN or n in self.tokens:
            return True
        if any(p in self.tokens for p in _parts(name) if p not in STOPWORDS and len(p) >= 4):
            return True
        # 'BraveSoftware' ~ 'brave', 'JetBrains' ~ 'jetbrains-toolbox'
        return any(len(n) >= 4 and (n.startswith(t) or t.startswith(n)) for t in self.long)


def _ignored():
    try:
        return [l.strip() for l in IGNORE_FILE.read_text().splitlines()
                if l.strip() and not l.startswith("#")]
    except OSError:
        return []


def collect(ctx, limit=None):
    s = Section("leftovers", "Leftovers")
    progress("matching config dirs against installed software…")
    known = Known(ctx)
    ignore = _ignored()

    # ---- orphaned app dirs
    cands = []
    for base in APP_DIRS:
        if base.is_dir():
            cands += [p for p in base.iterdir() if p.is_dir() and not p.is_symlink()]
    cands += [p for p in HOME.iterdir() if p.name.startswith(".") and p.is_dir() and not p.is_symlink()
              and p not in (CONFIG, HOME / ".local", HOME / ".cache")]
    from .caches import KNOWN
    covered = {p for k, _, _ in KNOWN for p in (k, *k.parents)}
    orphans = [p for p in cands if p not in covered and not known.match(p.name)
               and not any(fnmatch.fnmatch(tilde(p), g) or fnmatch.fnmatch(p.name, g) for g in ignore)]
    progress(f"sizing {len(orphans)} unmatched dirs…")
    sizes = du(orphans)
    orphans.sort(key=lambda p: -(sizes.get(str(p)) or 0))
    rows = []
    for p in orphans:
        newest = _newest(p)
        recent = newest and time.time() - newest < 30 * 86400
        rows.append((tilde(p), human(sizes.get(str(p))), age(newest),
                     "changed recently — likely still used by something" if recent else ""))
    s.block(f"App dirs matching no installed package or command ({len(rows)})", rows[:limit] if limit else rows,
            ("path", "size", "last change", ""), "lrrl",
            note="Name-matching is a heuristic — check each before deleting. Hide ones you keep by adding "
                 f"their name or a glob to {tilde(IGNORE_FILE)}."
                 + (f"  … {len(rows) - limit} more — run `omarchy-tidy leftovers`" if limit and len(rows) > limit else ""))
    s.summary.append(f"app dirs with no matching software: {len(rows)} "
                     f"({human(sum(sizes.get(str(p)) or 0 for p in orphans))})")
    s.data["orphan_dirs"] = {tilde(p): sizes.get(str(p)) for p in orphans}
    cache = HOME / ".cache"
    s.actions.append(Action(
        "leftovers.appdirs", "Remove app dirs of uninstalled software",
        "Heuristic matches — pick only ones you recognise. Config/data dirs go to the Trash; "
        "~/.cache ones are deleted. Add keepers to ~/.config/omarchy-tidy/ignore to stop seeing them.",
        "paths", root=HOME,
        items=[path_item(p, f"{tilde(p):<40} {human(sizes.get(str(p))):>8}  {age(_newest(p)):>5}"
                            + ("  (cache: delete)" if cache in p.parents else "  (trash)"),
                         sizes.get(str(p)), "delete" if cache in p.parents else "trash")
               for p in orphans]))

    _system(s, ctx)
    _pacnew(s)
    _backups(s)
    _broken_links(s)
    _dead_launchers(s)
    _python_user_sites(s)
    _dead_venvs(s)
    _failed_units(s)
    return s


def _newest(p):
    """Newest mtime of the dir and its direct children — cheap 'last used' proxy."""
    best = mtime(p)
    try:
        for e in os.scandir(p):
            try:
                best = max(best or 0, e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    return best


# Caches that package hooks regenerate; never owned, never leftovers.
GENERATED = {"/usr/share/applications/mimeinfo.cache", "/usr/share/applications/bamf-2.index"}


def _system(s, ctx):
    owned = ctx.owned_files
    rows = []
    for d in ("/usr/lib/modules", "/opt", "/usr/bin", "/usr/share/applications",
              "/usr/lib/systemd/system", "/etc/systemd/system"):
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if e.path in owned or e.path in GENERATED:
                continue
            if d == "/etc/systemd/system" and (e.is_symlink() or e.name.endswith((".wants", ".requires"))):
                continue   # enablement links; broken ones are reported separately
            if d == "/usr/lib/modules" and re.match(r"\d", e.name):
                note = yellow("modules of a kernel that is no longer installed")
            elif d == "/etc/systemd/system":
                note = "drop-in override" if e.name.endswith(".d") else "unit written by hand or by a script"
            else:
                note = ""
            rows.append((e.path, note))
    for d in ("/usr/local/lib", "/usr/local/share", "/usr/local/etc", "/usr/local/include"):
        try:
            rows += [(e.path, "") for e in sorted(os.scandir(d), key=lambda e: e.name)
                     if e.name not in ("man", "applications", "pkgconfig", "fonts")]
        except OSError:
            pass
    if rows:
        sizes = du([r[0] for r in rows])
        rows = [(p, human(sizes.get(p)), n) for p, n in rows]
        s.block(f"System files no package owns ({len(rows)})", rows, ("path", "size", "note"), "lrl",
                note="Left by `make install`, curl|sh installers, or packages whose removal hooks missed them. "
                     "Check with `pacman -Qo <path>` before removing.")
    s.summary.append(f"unowned files in system dirs: {len(rows)}")


def _pacnew(s):
    rc, out, _ = run(["find", "/etc", "-name", "*.pacnew", "-o", "-name", "*.pacsave"], timeout=60)
    files = sorted(out.split())
    s.summary.append(f".pacnew/.pacsave files in /etc: {len(files)}")
    if files:
        s.block(f".pacnew / .pacsave ({len(files)})", [(f, age(mtime(f))) for f in files], ("file", "age"), "lr",
                note=".pacnew = the package shipped a new default you haven't merged; .pacsave = your config "
                     "kept after the package was removed.")
        s.suggest.append(("merge or discard .pacnew/.pacsave files one by one", "sudo DIFFPROG='nvim -d' pacdiff"))
        s.actions.append(Action(
            "leftovers.pacdiff", "Merge .pacnew/.pacsave files (pacdiff)",
            "pacdiff walks each file: (v)iew a diff and merge in nvim, (r)emove the .pacnew, (o)verwrite "
            "with it, or (s)kip. Omarchy-managed files (omarchy_hooks, 90-omarchy-*) usually want (o).",
            "cmd", ["sudo", "DIFFPROG=nvim -d", "pacdiff"], sudo=True))


def _backups(s):
    pats = ("*.bak", "*.bak.*", "*.old", "*.orig", "*.backup", "*omarchy-upgrade*", "*~")
    places = [HOME, CONFIG, HOME / ".local/share", HOME / ".local/bin", Path("/etc/pacman.d")]
    found = []
    for d in places:
        try:
            for e in os.scandir(d):
                if any(fnmatch.fnmatch(e.name, p) for p in pats):
                    found.append(Path(e.path))
        except OSError:
            pass
    for sub in CONFIG.iterdir() if CONFIG.is_dir() else []:
        if sub.is_dir() and not sub.is_symlink():
            try:
                found += [Path(e.path) for e in os.scandir(sub) if any(fnmatch.fnmatch(e.name, p) for p in pats)]
            except OSError:
                pass
    found = [p for p in found if not any(q in p.parents for q in found)]
    if found:
        sizes = du(found)
        found.sort(key=lambda p: -(sizes.get(str(p)) or 0))
        s.block(f"Backups and upgrade leftovers ({len(found)})",
                [(tilde(p), human(sizes.get(str(p))), age(mtime(p))) for p in found],
                ("path", "size", "age"), "lrr",
                note="Omarchy upgrades and config edits leave these; once the new setup works they're dead weight.")
        home_found = [p for p in found if HOME in p.parents]
        s.actions.append(Action(
            "leftovers.backups", "Trash old backups and upgrade leftovers",
            "Copies made before an edit or an Omarchy upgrade. They go to the Trash, so they stay "
            "restorable until you empty it. (Backups under /etc are listed but left for you.)",
            "paths", root=HOME,
            items=[path_item(p, f"{tilde(p):<72} {age(mtime(p)):>5}", sizes.get(str(p)), "trash") for p in home_found]))
        if any("omarchy-upgrade" in p.name for p in found):
            s.suggest.append(("list Omarchy's pre-upgrade backups; delete once you're happy with the upgrade",
                              "find ~ ~/.config ~/.local/share -maxdepth 3 -name '*omarchy-upgrade*' -prune -print"))
    s.summary.append(f"backup/.bak leftovers: {len(found)}")


def _broken_links(s):
    rows = []
    roots = [(CONFIG, 3), (HOME / ".local/share/applications", 1), (HOME / ".local/bin", 1),
             (HOME, 1), (Path("/etc/systemd/system"), 2)]
    for root, depth in roots:
        rc, out, _ = run(["find", str(root), "-maxdepth", str(depth), "-xtype", "l"], timeout=60)
        for p in out.splitlines():
            if os.path.basename(p).startswith("Singleton"):
                continue   # Chromium/Electron instance locks: dangling by design
            try:
                rows.append((tilde(p), "→ " + os.readlink(p)))
            except OSError:
                pass
    home_links = [r[0] for r in rows if r[0].startswith("~/")]
    if home_links:
        s.actions.append(Action(
            "leftovers.links", "Remove broken symlinks",
            "Only the link is removed; its target is already gone.", "paths", root=HOME,
            items=[path_item(str(HOME) + l[1:], f"{l}  {dict(rows)[l]}") for l in home_links]))
    if rows:
        s.block(f"Broken symlinks ({len(rows)})", rows, ("link", "target"),
                note="Under /etc/systemd/system these are services still enabled for software that's gone: "
                     "`sudo systemctl disable <unit>`." if any(r[0].startswith("/etc/systemd") for r in rows)
                else "Safe to remove: `rm <link>` deletes the link, never the (missing) target.")
    s.summary.append(f"broken symlinks: {len(rows)}")


def _dead_launchers(s):
    rows = []
    for d in (HOME / ".local/share/applications", HOME / ".config/autostart"):
        for f in sorted(d.glob("*.desktop")) if d.is_dir() else []:
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            m = re.search(r"^(?:TryExec|Exec)=(.*)$", text, re.M)
            if not m:
                continue
            try:
                argv = shlex.split(m.group(1))
            except ValueError:
                argv = m.group(1).split()
            while argv and (argv[0] == "env" or re.fullmatch(r"\w+=.*", argv[0])):
                argv.pop(0)
            if not argv:
                continue
            cmd = os.path.expandvars(argv[0])
            if cmd.startswith("$"):
                continue   # depends on the session environment; can't judge from here
            ok = os.path.exists(os.path.expanduser(cmd)) if "/" in cmd else shutil.which(cmd)
            if not ok:
                rows.append((tilde(f), cmd))
    if rows:
        s.actions.append(Action(
            "leftovers.launchers", "Trash launchers for missing programs",
            "They show in the app launcher but do nothing.", "paths", root=HOME,
            items=[path_item(str(HOME) + f[1:], f"{f}  → {c}", None, "trash") for f, c in rows]))
        s.block(f"Launchers pointing at missing programs ({len(rows)})", rows, ("desktop file", "missing command"),
                note="These still show up in the app launcher but do nothing. Delete the .desktop file "
                     "(or `omarchy-remove-launcher-entry` for launchers Omarchy made).")
    s.summary.append(f"dead launchers: {len(rows)}")


def _python_user_sites(s):
    base = HOME / ".local/lib"
    rows = []
    for d in sorted(base.glob("python3.*")) if base.is_dir() else []:
        if not shutil.which(d.name):
            rows.append(d)
    if rows:
        sizes = du(rows)
        s.actions.append(Action(
            "leftovers.pysite", "Trash pip --user packages of removed Python versions",
            "No interpreter left that would load them.", "paths", root=HOME,
            items=[path_item(p, f"{tilde(p)}  {human(sizes.get(str(p)))}", sizes.get(str(p)), "trash") for p in rows]))
        s.block("pip --user packages for Python versions no longer installed",
                [(tilde(p), human(sizes.get(str(p)))) for p in rows], ("path", "size"), "lr")
    s.summary.append(f"stale Python user site-packages: {len(rows)}")


def _dead_venvs(s):
    """Python venvs under ~ whose base interpreter was removed by a Python upgrade."""
    progress("looking for Python venvs with a missing interpreter…")
    rc, out, _ = run(["find", str(HOME), "-maxdepth", "6",
                      "(", "-name", ".cache", "-o", "-name", "node_modules", "-o", "-name", ".git",
                      "-o", "-name", "site-packages", "-o", "-path", str(HOME / ".local/share/Steam"), ")",
                      "-prune", "-o", "-name", "pyvenv.cfg", "-print"], timeout=300)
    dead = []
    for cfg in out.splitlines():
        venv = Path(cfg).parent
        if (venv / "bin/python").exists():
            continue
        try:
            kv = dict(l.split("=", 1) for l in Path(cfg).read_text().splitlines() if "=" in l)
            kv = {k.strip(): v.strip() for k, v in kv.items()}
        except (OSError, ValueError):
            kv = {}
        ver = kv.get("version") or kv.get("version_info") or "?"
        dead.append((venv, f"Python {ver} in {kv.get('home', '?')}"))
    if dead:
        sizes = du([v for v, _ in dead])
        pipx = HOME / ".local/share/pipx"   # handled by `pipx uninstall` in the others section
        s.actions.append(Action(
            "leftovers.venvs", "Trash Python venvs whose interpreter is gone",
            "Only the venv directory goes; project code next to it is untouched. Recreate with `uv venv` if needed.",
            "paths", root=HOME,
            items=[path_item(v, f"{tilde(v)}  {human(sizes.get(str(v)))}  ({h})", sizes.get(str(v)), "trash")
                   for v, h in dead if pipx not in v.parents and v != pipx]))
        s.block(f"Python venvs whose interpreter is gone ({len(dead)})",
                [(tilde(v), human(sizes.get(str(v))), tilde(h)) for v, h in dead],
                ("venv", "size", "built with"), "lrl",
                note="These can't run any more. Recreate them (`uv venv` / `python -m venv`) or delete them.")
    s.summary.append(f"broken Python venvs: {len(dead)}")


def _failed_units(s):
    rows = []
    for scope in (["--system"], ["--user"]):
        rc, out, _ = run(["systemctl", *scope, "--failed", "--no-legend", "--plain"])
        for l in out.splitlines():
            f = l.split(None, 4)
            if f:
                rows.append((scope[0].lstrip("-"), f[0], f[4] if len(f) > 4 else ""))
    if rows:
        s.block(f"Failed systemd units ({len(rows)})", rows, ("scope", "unit", "description"),
                note="Often a service of something since removed. `systemctl status <unit>` to see why.")
    s.summary.append(f"failed systemd units: {len(rows)}")
