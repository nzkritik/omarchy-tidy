"""Git clones scattered around $HOME, and AUR build clones in yay/paru caches."""

import os
import re
import time
from pathlib import Path

from .util import HOME, Action, Section, path_item, age, du, human, progress, red, run, tilde, yellow, green

MAX_DEPTH = 6
STALE_DAYS = 180

# Directories never worth walking: caches, package stores, game libraries,
# and places where tools keep their own managed clones.
PRUNE_NAMES = {".git", "node_modules", ".venv", "venv", "__pycache__", "site-packages",
               ".cache", ".npm", ".pnpm-store", ".cargo", ".rustup", ".gradle", ".m2",
               ".steam", "Steam", "steamapps", ".wine", "wineprefix", "compatdata",
               ".ollama", ".lmstudio", ".pinokio", "pinokio"}
PRUNE_PATHS = {HOME / p for p in (
    "go/pkg", ".local/share/Trash", ".local/share/nvim", ".local/state", ".local/share/Steam",
    ".local/lib", ".local/share/pipx", ".local/share/uv", ".local/share/mise",
    ".pub-cache", ".dropbox-dist", ".mozilla", ".config/BraveSoftware", ".config/chromium",
    ".gitlibs",   # Clojure tools.deps checkouts: a cache, not your repos
)}
# Deleting inside these also deletes the cloud copy.
SYNCED = [HOME / "Dropbox", HOME / "Nextcloud", HOME / "OneDrive", HOME / "Sync"]
# Clones that some tool manages for you; listed, but never called stale.
MANAGED = {
    HOME / ".local/share/omarchy": "Omarchy itself",
    HOME / ".config/omarchy/plugins": "Omarchy plugin",
    HOME / ".config/omarchy/themes": "Omarchy theme",
    HOME / ".tmux/plugins": "tmux plugin manager",
}


def managed_by(repo):
    synced = next((d for d in SYNCED if d in repo.parents), None)
    if synced:
        return f"in {tilde(synced)} — deleting also removes the synced copy"
    if "omarchy-upgrade" in repo.name:
        return "Omarchy's pre-upgrade backup (see `omarchy-tidy leftovers`)"
    return next((why for m, why in MANAGED.items() if repo == m or m in repo.parents), None)


def find_repos():
    found = []

    def walk(d, depth):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        if any(e.name == ".git" for e in entries):
            found.append(Path(d))
        if depth >= MAX_DEPTH:
            return
        for e in entries:
            if not e.is_dir(follow_symlinks=False) or e.name in PRUNE_NAMES:
                continue
            p = Path(e.path)
            if p in PRUNE_PATHS or os.path.ismount(p):
                continue
            walk(p, depth + 1)

    walk(HOME, 0)
    return sorted(found)


def _git(repo, *args, timeout=30):
    # GIT_OPTIONAL_LOCKS=0 stops `git status` from rewriting .git/index to refresh
    # its stat cache — that would both modify the repo and fake its "last used" time.
    rc, out, _ = run(["git", "-C", str(repo), *args], timeout=timeout, env={"GIT_OPTIONAL_LOCKS": "0"})
    return out.strip() if rc == 0 else None


def _clean_url(url):
    """Drop any user:token@ so credentials never reach the report."""
    if not url:
        return ""
    url = re.sub(r"(https?://)[^/@]+@", r"\1", url)
    url = re.sub(r"^(https?://|ssh://)?(git@)?github\.com[:/]", "gh:", url)
    return url.removesuffix(".git")


def inspect(repo):
    git = repo / ".git"
    # Read timestamps before running any git command.
    touched = max(filter(None, (_mt(git / f) for f in ("index", "FETCH_HEAD", "HEAD", "ORIG_HEAD", "logs/HEAD"))),
                  default=None)
    last_commit = _git(repo, "log", "-1", "--format=%ct")
    remotes = (_git(repo, "remote") or "").split()
    url = _clean_url(_git(repo, "remote", "get-url", remotes[0])) if remotes else ""
    status = _git(repo, "status", "--porcelain", timeout=60)
    dirty = len(status.splitlines()) if status else 0
    unpushed = _git(repo, "rev-list", "--count", "--branches", "--not", "--remotes") if remotes else None
    stashes = _git(repo, "stash", "list")
    return {
        "path": str(repo),
        "remote": url,
        "last_commit": int(last_commit) if last_commit and last_commit.isdigit() else None,
        "touched": touched,
        "dirty": dirty,
        "unpushed": int(unpushed) if unpushed and unpushed.isdigit() else 0,
        "stashes": len(stashes.splitlines()) if stashes else 0,
        "has_remote": bool(remotes),
    }


def _mt(p):
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def verdict(r, managed):
    work = []
    if r["dirty"]:
        work.append(f"{r['dirty']} uncommitted")
    if r["unpushed"]:
        work.append(f"{r['unpushed']} unpushed")
    if r["stashes"]:
        work.append(f"{r['stashes']} stashed")
    if not r["has_remote"]:
        work.append("no remote")
    if managed:
        return managed + (red(" — local changes: " + ", ".join(work)) if work and r["has_remote"] else "")
    if work:
        return red("local work: " + ", ".join(work))
    last = max(filter(None, (r["last_commit"], r["touched"])), default=None)
    if last and time.time() - last > STALE_DAYS * 86400:
        return yellow("stale, clean & pushed — safe to delete, re-clone later")
    return green("clean & pushed")


def collect(ctx, limit=None):
    s = Section("clones", "Git clones")
    progress("finding git repositories under ~…")
    repos = find_repos()
    progress(f"inspecting {len(repos)} repositories…")
    infos = []
    for repo in repos:
        r = inspect(repo)
        # One du per repo: a single call would count a nested repo only once, under its parent.
        r["size"] = du([repo]).get(str(repo))
        managed = managed_by(repo)
        r["verdict"] = verdict(r, managed)
        r["managed"] = bool(managed)
        infos.append(r)

    stale = [r for r in infos if "stale" in r["verdict"]]
    work = [r for r in infos if "local work" in r["verdict"]]
    s.summary += [
        f"repositories under ~: {len(infos)}  ({human(sum(r['size'] or 0 for r in infos))})",
        f"with local work (uncommitted / unpushed / no remote): {len(work)}",
        f"stale (no activity {STALE_DAYS}+ days, clean, pushed): {len(stale)}"
        f"  ({human(sum(r['size'] or 0 for r in stale))})",
    ]
    rows = [(tilde(r["path"]), human(r["size"]), age(r["last_commit"]), age(r["touched"]),
             r["remote"] or "—", r["verdict"])
            for r in sorted(infos, key=lambda r: r["path"])]
    s.block(f"Repositories ({len(rows)})", rows,
            ("path", "size", "last commit", "last used", "remote", "verdict"), "lrrrll",
            note="'last used' = newest of .git/index, HEAD, FETCH_HEAD, ORIG_HEAD, logs/HEAD — when you last checked out, pulled, committed or staged.")
    s.data["repos"] = [{k: v for k, v in r.items() if k != "verdict"} | {"verdict": _plain(r["verdict"])}
                       for r in infos]
    if stale:
        s.suggest.append(("stale clones are recoverable from their remote", "rm -rf <path>   # after a last look"))
        s.actions.append(Action(
            "clones.stale", "Delete stale git clones",
            f"Clean, fully pushed, untouched for {STALE_DAYS}+ days — `git clone` gets them back. "
            "Each is re-checked right before deletion and skipped if it has gained local work.",
            "paths", root=HOME, recheck=_still_disposable,
            items=[path_item(r["path"], f"{tilde(r['path'])}  {human(r['size'])}  {r['remote']}", r["size"])
                   for r in stale]))

    _aur_build_dirs(s, ctx)
    return s


def _still_disposable(path):
    """Re-inspect just before deleting: the verdict may be stale by now."""
    r = inspect(Path(path))
    if r["dirty"] or r["unpushed"] or r["stashes"] or not r["has_remote"]:
        return "now has local work"
    return None


def _plain(t):
    return re.sub(r"\033\[[0-9;]*m", "", t)


def _aur_build_dirs(s, ctx):
    bases = {p.base for p in ctx.packages.values()} | set(ctx.packages)
    for helper, d, clean in (("yay", HOME / ".cache/yay", "yay -Sc"),
                             ("paru", HOME / ".cache/paru/clone", "paru -Sc")):
        if not d.is_dir():
            continue
        progress(f"sizing {helper} build cache…")
        dirs = [p for p in d.iterdir() if p.is_dir()]
        sizes = du(dirs)
        gone = sorted((p for p in dirs if p.name not in bases), key=lambda p: -(sizes.get(str(p)) or 0))
        kept = [p for p in dirs if p.name in bases]
        gone_sz = sum(sizes.get(str(p)) or 0 for p in gone)
        kept_sz = sum(sizes.get(str(p)) or 0 for p in kept)
        s.summary.append(f"{helper} build clones: {len(dirs)} — {len(gone)} for packages no longer installed "
                         f"({human(gone_sz)}), {len(kept)} for installed ones ({human(kept_sz)})")
        if gone:
            s.block(f"{helper} clones of packages you've removed ({len(gone)})",
                    [(p.name, human(sizes.get(str(p)))) for p in gone], ("clone", "size"), "lr")
            s.suggest.append((f"{helper}: drop build clones of uninstalled packages", clean))
            s.actions.append(Action(
                f"clones.{helper}", f"Delete {helper} build clones of removed packages",
                f"Build directories for AUR packages that are no longer installed; {helper} re-clones "
                "if you ever install one again.",
                "paths", root=d,
                items=[path_item(p, f"{p.name:<36} {human(sizes.get(str(p))):>8}", sizes.get(str(p))) for p in gone]))
        if kept_sz > 1 << 30:
            s.suggest.append((f"{helper}: installed packages' build dirs ({human(kept_sz)}) only speed up rebuilds",
                              f"{clean}c   # clears the whole {helper} cache"))
