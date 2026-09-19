"""`omarchy-tidy clean`: walk the findings and act only on what the user picks.

Safety rules, all enforced here rather than trusted to the collectors:
  * nothing happens without an explicit yes or an explicit pick (nothing is preselected);
  * pacman runs without --noconfirm, so it shows its own transaction and asks again;
  * root never receives a path from $HOME — sudo only runs fixed commands and package names;
  * a path is removed only if it is strictly inside its action's root, no component of it
    is a symlink, and it is still the same inode the scan saw; symlinks are unlinked, never
    followed; directories go through shutil.rmtree, which is symlink-attack resistant;
  * home config/backups go to the Trash; only regenerable things are deleted outright.
"""

import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from .util import HOME, bold, cyan, dim, green, have, human, red, tilde, yellow

LOG = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "omarchy-tidy/clean.log"
PKG_RE = re.compile(r"[a-z0-9@_+][a-z0-9@._+-]*")
SEP = "␟"   # label/value delimiter for gum; never appears in labels
# Never removable, whatever an action claims.
PROTECTED = {HOME, HOME / ".config", HOME / ".local", HOME / ".local/share", HOME / ".local/state",
             HOME / ".cache", HOME / ".ssh", HOME / ".gnupg", HOME / ".local/bin",
             HOME / ".local/share/omarchy", HOME / ".config/omarchy", HOME / "Work", HOME / "Dev",
             HOME / "Documents", HOME / "Desktop", HOME / "Downloads", HOME / "Pictures", Path("/")}


class Quit(Exception):
    pass


# ---------------------------------------------------------------- prompts

def confirm(question, dry):
    if dry:
        print(dim(f"    (dry run) would ask: {question}"))
        return True
    if have("gum"):
        rc = subprocess.run(["gum", "confirm", "--default=false", question]).returncode
        if rc == 130:
            raise Quit
        return rc == 0
    ans = input(f"  {question} [y/N/q] ").strip().lower()
    if ans == "q":
        raise Quit
    return ans in ("y", "yes")


def pick(header, items, dry):
    """Multi-select; nothing preselected. Returns the chosen Items."""
    if dry:
        return list(items)
    if have("gum"):
        opts = [f"{it.label}{SEP}{i}" for i, it in enumerate(items)]
        p = subprocess.run(["gum", "choose", "--no-limit", f"--label-delimiter={SEP}",
                            f"--header={header}  (space: select, enter: confirm, esc: skip)",
                            f"--height={min(len(items), 20) + 2}", *opts],
                           stdout=subprocess.PIPE, text=True)
        if p.returncode == 130:
            raise Quit
        if p.returncode != 0:
            return []
        return [items[int(x)] for x in p.stdout.split() if x.isdigit() and int(x) < len(items)]
    print(f"  {header}")
    for i, it in enumerate(items, 1):
        print(f"    {i:>3}. {it.label}")
    ans = input("  numbers to act on (e.g. 1 3 5-8, 'all', empty = skip, q = quit): ").strip().lower()
    if ans == "q":
        raise Quit
    if ans == "all":
        return list(items)
    chosen = set()
    for tok in ans.replace(",", " ").split():
        a, _, b = tok.partition("-")
        if a.isdigit() and (not b or b.isdigit()):
            chosen.update(range(int(a), int(b or a) + 1))
    return [items[i - 1] for i in sorted(chosen) if 0 < i <= len(items)]


# ---------------------------------------------------------------- execution

def log(line):
    """Append to our own log without following a planted symlink."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(f"{time.strftime('%F %T')}  {line}\n")
    except OSError as e:
        print(yellow(f"    (could not write {tilde(LOG)}: {e})"))


def run_cmd(argv, dry):
    shown = shlex.join(argv)
    if dry:
        print(dim(f"    (dry run) would run: {shown}"))
        return True
    print(dim(f"    $ {shown}"))
    rc = subprocess.run(argv).returncode
    log(f"rc={rc}  {shown}")
    if rc != 0:
        print(red(f"    exited with {rc}"))
    return rc == 0


def check_path(item, root):
    """None if item.value may be removed, else the reason it may not."""
    p = Path(item.value)
    root = Path(root)
    if not p.is_absolute() or ".." in p.parts:
        return "not a clean absolute path"
    if p in PROTECTED or p == root or root not in p.parents:
        return f"outside {tilde(root)} or protected"
    # No symlink anywhere between the root and the item's parent: a planted link
    # there would redirect the removal somewhere else.
    for d in [q for q in p.parents if q == root or root in q.parents]:
        try:
            if stat.S_ISLNK(os.lstat(d).st_mode):
                return f"{tilde(d)} is a symlink"
        except OSError as e:
            return f"cannot inspect {tilde(d)}: {e.strerror}"
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return "already gone"
    if item.ident and (st.st_dev, st.st_ino) != item.ident:
        return "changed since the scan (different file now)"
    return None


def remove_path(item, root, recheck, dry):
    reason = check_path(item, root)
    if not reason and recheck:
        reason = recheck(item.value)
    label = tilde(item.value)
    if reason:
        print(yellow(f"    skipped {label}: {reason}"))
        log(f"skipped {item.value}: {reason}")
        return False
    p = Path(item.value)
    verb = "trash" if item.mode == "trash" and not p.is_symlink() else "delete"
    if dry:
        print(dim(f"    (dry run) would {verb} {label}"))
        return True
    try:
        if verb == "trash":
            r = subprocess.run(["gio", "trash", "--", str(p)], capture_output=True, text=True)
            if r.returncode != 0:
                raise OSError(r.stderr.strip() or f"gio trash exited {r.returncode}")
        elif p.is_symlink() or not p.is_dir():
            os.unlink(p)
        else:
            shutil.rmtree(p)
    except OSError as e:
        print(red(f"    failed {label}: {e}"))
        log(f"failed {verb} {item.value}: {e}")
        return False
    print(green(f"    {'trashed' if verb == 'trash' else 'deleted'} {label}") + dim(f"  {human(item.size)}" if item.size else ""))
    log(f"{verb} {item.value}")
    return True


def do_action(a, dry):
    print()
    print(bold(cyan(f"▸ {a.title}")) + (dim(f"   ~{human(a.size)}") if a.size else ""))
    print(f"  {a.why}")

    if a.kind == "cmd":
        if confirm(f"Run: {shlex.join(a.argv)} ?", dry):
            return run_cmd(a.argv, dry)
        return None

    chosen = pick(a.title, a.items, dry)
    if not chosen:
        print(dim("  nothing selected — skipped"))
        return None

    if a.kind == "pick":
        ok = re.compile(a.valid) if a.valid else PKG_RE
        names = [it.value for it in chosen if ok.fullmatch(it.value)]
        bad = [it.value for it in chosen if not ok.fullmatch(it.value)]
        for b in bad:
            print(yellow(f"    refusing odd name {b!r}"))
        if not names:
            return None
        if a.each:
            return all([run_cmd(a.argv + [n], dry) for n in names])
        return run_cmd(a.argv + names, dry)

    if a.kind == "paths":
        where = {"trash": "move to the Trash", "delete": "permanently delete"}
        modes = sorted({it.mode for it in chosen})
        total = sum(it.size or 0 for it in chosen)
        if not confirm(f"{' / '.join(where[m] for m in modes)} {len(chosen)} item(s)"
                       + (f", {human(total)}" if total else "") + "?", dry):
            return None
        return all([remove_path(it, a.root, a.recheck, dry) for it in chosen])


def free_bytes():
    st = os.statvfs(HOME)
    return st.f_bavail * st.f_frsize


def run(results, only=None, dry=False):
    if not dry and not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("clean is interactive: run it in a terminal (or use --dry-run)")
    actions = [a for r in results for a in r.actions if a.items or a.kind == "cmd"]
    if only:
        actions = [a for a in actions if any(a.key.startswith(o) for o in only)]
    # The same path can be found by two sections (a big ~/.cache dir of an uninstalled app);
    # offer it once, in the first step that finds it.
    seen = set()
    for a in actions:
        if a.kind == "paths":
            a.items = [it for it in a.items if it.value not in seen]
            seen.update(it.value for it in a.items)
            a.size = sum(it.size or 0 for it in a.items) or None
    actions = [a for a in actions if a.items or a.kind == "cmd"]
    if not actions:
        print("Nothing to clean.")
        return

    print(bold("Cleanup plan") + dim("  — every step asks first; Esc skips a step, Ctrl-C quits"))
    for i, a in enumerate(actions, 1):
        n = f"{len(a.items)} items" if a.items else ("sudo" if a.sudo else "command")
        print(f"  {i:>2}. {a.title}" + dim(f"  ({n}{', ~' + human(a.size) if a.size else ''})"))
    if dry:
        print(yellow("\n  DRY RUN — nothing will be changed; every item is shown as if selected."))

    before = free_bytes()
    done, failed = [], []
    try:
        for a in actions:
            ok = do_action(a, dry)
            if ok is True:
                done.append(a.title)
            elif ok is False:
                failed.append(a.title)
    except (Quit, KeyboardInterrupt):
        print(yellow("\n  stopped."))
    freed = free_bytes() - before

    print()
    print(bold("Summary"))
    print(f"  steps completed: {len(done)}" + (red(f"   with errors: {len(failed)}") if failed else ""))
    for t in failed:
        print(red(f"    ✗ {t}"))
    if not dry:
        print(f"  free space on {tilde(HOME)}: {human(before)} → {human(free_bytes())}  "
              + (green(f"(+{human(freed)})") if freed > 0 else dim("(no change)")))
        print(dim(f"  log: {tilde(LOG)}   (trashed items: `gio trash --list`, restore from your file manager)"))
