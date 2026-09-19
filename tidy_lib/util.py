"""Shared helpers: subprocess, sizes, terminal output, and the lazily-built
system context every section draws from."""

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

HOME = Path.home()
PACMAN_LOCAL = Path("/var/lib/pacman/local")
OMARCHY = HOME / ".local/share/omarchy"

# ---------------------------------------------------------------- output

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code):
    return lambda s: f"\033[{code}m{s}\033[0m" if COLOR else str(s)


bold, dim, red, green, yellow, cyan = (_c(n) for n in ("1", "2", "31", "32", "33", "36"))


def progress(msg):
    """Status line on stderr so stdout stays clean for piping / --json."""
    if sys.stderr.isatty():
        sys.stderr.write(f"\r\033[K{dim(msg)}")
        sys.stderr.flush()


def progress_done():
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()


def human(n):
    if n is None:
        return "?"
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def age(ts):
    """Unix timestamp -> '3d', '5mo', '2y'."""
    if not ts:
        return "?"
    days = (time.time() - ts) / 86400
    if days < 1:
        return "today"
    if days < 60:
        return f"{days:.0f}d"
    if days < 730:
        return f"{days / 30.4:.0f}mo"
    return f"{days / 365:.1f}y"


def date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "?"


def tilde(p):
    s = str(p)
    h = str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def table(rows, headers, aligns=None, indent="  "):
    """rows: list of tuples of str. aligns: string of 'l'/'r' per column."""
    if not rows:
        return []
    aligns = aligns or "l" * len(headers)
    strip = lambda s: re.sub(r"\033\[[0-9;]*m", "", s)
    widths = [max(len(strip(str(r[i]))) for r in [headers, *rows]) for i in range(len(headers))]

    def fmt(r):
        cells = []
        for i, v in enumerate(r):
            pad = widths[i] - len(strip(str(v)))
            cells.append(str(v) + " " * pad if aligns[i] == "l" else " " * pad + str(v))
        return (indent + "  ".join(cells)).rstrip()

    return [dim(fmt(headers)), *(fmt(r) for r in rows)]


# ---------------------------------------------------------------- processes


def have(cmd):
    return shutil.which(cmd) is not None


def run(cmd, timeout=120, env=None):
    """Return (returncode, stdout, stderr); rc 127 if missing, 124 on timeout."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "LC_ALL": "C", **(env or {})})
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out after {timeout}s"


def du(paths, timeout=600):
    """Disk usage in bytes for each path, from a single du call.
    Unreadable subpaths still produce a (partial) total, so stderr is ignored."""
    paths = [str(p) for p in paths if Path(p).exists()]
    if not paths:
        return {}
    rc, out, _ = run(["du", "-s", "-B1", "--", *paths], timeout=timeout)
    sizes = {}
    for line in out.splitlines():
        size, _, path = line.partition("\t")
        if size.isdigit():
            sizes[path] = int(size)
    return sizes


def mtime(p):
    try:
        return Path(p).stat().st_mtime
    except OSError:
        return None


# ---------------------------------------------------------------- section result


@dataclass
class Section:
    """What every collector returns. Rendering and --json both read this."""
    key: str
    title: str
    summary: list = field(default_factory=list)   # short "k: v" lines
    blocks: list = field(default_factory=list)    # (heading, rows, headers, aligns, note)
    suggest: list = field(default_factory=list)   # (why, command)
    notes: list = field(default_factory=list)     # skipped checks and why
    data: dict = field(default_factory=dict)      # machine-readable extras for --json
    actions: list = field(default_factory=list)   # Action objects for `omarchy-tidy clean`

    def block(self, heading, rows, headers, aligns=None, note=None):
        self.blocks.append((heading, rows, headers, aligns, note))


# ---------------------------------------------------------------- clean actions


@dataclass
class Item:
    """One pickable thing: a package name or a path."""
    value: str                 # package name, or absolute path
    label: str                 # what the picker shows
    size: int = None
    mode: str = "delete"       # paths only: "delete" (permanent) or "trash" (recoverable)
    ident: tuple = None        # paths only: (st_dev, st_ino) seen at scan time


@dataclass
class Action:
    """A cleanup step `omarchy-tidy clean` can offer.

    kind:
      cmd    run `argv` once after a yes/no
      pick   user picks items; run `argv + picked` (or once per item if each=True)
      paths  user picks items; each is trashed/deleted in-process under `root`
    """
    key: str
    title: str
    why: str
    kind: str
    argv: list = None
    items: list = None
    sudo: bool = False
    each: bool = False
    root: object = None        # paths: every item must sit strictly inside this dir
    recheck: object = None     # paths: fn(path) -> None if still safe, else a reason string
    size: int = None           # estimated reclaim, for the listing
    valid: str = None          # pick: regex every picked value must fully match (default: package name)

    def __post_init__(self):
        if self.size is None and self.items:
            known = [i.size for i in self.items if i.size]
            self.size = sum(known) if known else None


def path_item(p, label=None, size=None, mode="delete"):
    """Item for a path, remembering its identity so a swapped path is refused later."""
    try:
        st = os.lstat(p)
        ident = (st.st_dev, st.st_ino)
    except OSError:
        ident = None
    return Item(str(p), label or tilde(p), size, mode, ident)


# ---------------------------------------------------------------- package model


@dataclass
class Pkg:
    name: str
    base: str
    version: str
    size: int
    installed: int
    explicit: bool
    required_by: list
    optional_for: list
    repo: str = "foreign"


class Ctx:
    """Facts several sections need, computed once on first use."""

    def __init__(self, offline=False):
        self.offline = offline
        self.provides = {}   # virtual/provided name -> installed packages providing it

    def resolve(self, name, pkgs=None):
        """Installed package for a name or something it provides, else None."""
        pkgs = pkgs if pkgs is not None else self.packages
        return name if name in pkgs else next(iter(self.provides.get(name, [])), None)

    @cached_property
    def packages(self):
        progress("reading pacman database…")
        rc, out, err = run(["expac", "-Q", "--timefmt=%s", "-l", "|",
                            "%n\t%e\t%v\t%m\t%l\t%w\t%N\t%o\t%S"])
        if rc != 0:
            raise SystemExit(f"expac failed ({err.strip()}); install it with: sudo pacman -S expac")
        pkgs, optdeps = {}, {}
        split = lambda s: [x for x in s.split("|") if x]
        for line in out.splitlines():
            n, base, ver, size, inst, reason, req, opt, prov = line.split("\t")
            pkgs[n] = Pkg(n, base, ver, int(size), int(inst), reason == "explicit", split(req), [])
            optdeps[n] = split(opt)
            for pv in split(prov):
                self.provides.setdefault(pv, []).append(n)
        # pacman has no "optionally required by" field in expac; invert optdepends.
        for n, opts in optdeps.items():
            for o in opts:
                for target in [o] if o in pkgs else self.provides.get(o, []):
                    if n not in pkgs[target].optional_for:
                        pkgs[target].optional_for.append(n)
        for n, repo in self.sync_repo.items():
            if n in pkgs:
                pkgs[n].repo = repo
        return pkgs

    @cached_property
    def sync_repo(self):
        """name -> first repo providing it, in pacman.conf order (the one pacman uses)."""
        rc, out, _ = run(["pacman", "-Sl"])
        m = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                m.setdefault(parts[1], parts[0])
        return m

    @cached_property
    def repos(self):
        """Repo names in pacman.conf order."""
        rc, out, _ = run(["pacman-conf", "--repo-list"])
        return out.split() if rc == 0 else sorted(set(self.sync_repo.values()))

    @cached_property
    def owned_files(self):
        """Every path owned by an installed package, as '/usr/bin/foo' (dirs without slash)."""
        progress("reading package file lists…")
        owned = set()
        for d in PACMAN_LOCAL.iterdir():
            f = d / "files"
            if not f.is_file():
                continue
            in_files = False
            for line in f.read_text(errors="replace").splitlines():
                if line.startswith("%"):
                    in_files = line == "%FILES%"
                elif in_files and line:
                    owned.add("/" + line.rstrip("/"))
        return owned

    @cached_property
    def omarchy_base(self):
        """Packages Omarchy itself installs (install/omarchy-base.packages)."""
        return _read_pkg_list(OMARCHY / "install/omarchy-base.packages")

    @cached_property
    def omarchy_other(self):
        """Hardware-conditional packages Omarchy may install (omarchy-other.packages)."""
        return _read_pkg_list(OMARCHY / "install/omarchy-other.packages")

    @cached_property
    def omarchy_menu(self):
        """Packages that Omarchy's optional installers (Install menu) pull in."""
        names = set()
        bindir = OMARCHY / "bin"
        if not bindir.is_dir():
            return names
        pat = re.compile(r"omarchy-pkg-(?:aur-)?(?:add|install)\s+([^|;&#\n\"]+)")
        for f in bindir.glob("omarchy-*"):
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            for m in pat.finditer(text):
                for tok in m.group(1).split():
                    if re.fullmatch(r"[a-z0-9][a-z0-9@._+-]*", tok):
                        names.add(tok)
        return names

    def origin(self, pkg):
        """Why an explicitly installed package is here."""
        if pkg.name in self.omarchy_listed:
            return "omarchy"
        if pkg.name in self.omarchy_menu_installed:
            return "omarchy-menu"
        if pkg.repo == "omarchy":
            return "omarchy"
        return "you"

    @cached_property
    def omarchy_listed(self):
        """Installed packages satisfying an entry in Omarchy's package lists (via provides too)."""
        return {self.resolve(n) for n in self.omarchy_base | self.omarchy_other} - {None}

    @cached_property
    def omarchy_menu_installed(self):
        return {self.resolve(n) for n in self.omarchy_menu} - {None}


def _read_pkg_list(path):
    try:
        return {l.strip() for l in path.read_text().splitlines()
                if l.strip() and not l.lstrip().startswith("#")}
    except OSError:
        return set()
