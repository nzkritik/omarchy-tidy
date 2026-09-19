"""pacman / AUR package profile."""

import json
import re
import time
import urllib.parse
import urllib.request

from .util import Action, Item, Section, age, date, human, progress, run, yellow, red

RECENT_DAYS = 30
# Never offered for removal by `clean`, however they got installed: removing these can leave
# the machine unbootable, without a network, or unable to update itself.
CRITICAL = re.compile(r"(linux(-lts|-zen|-hardened)?(-headers)?|.*-ucode|efibootmgr|limine.*|grub|mkinitcpio"
                      r"|nvidia.*|.*-firmware.*|base|base-devel|sudo|pacman.*|yay|paru|systemd.*|glibc"
                      r"|networkmanager|iwd|wpa_supplicant|omarchy.*|archlinux-keyring|.*-keyring)")


def collect(ctx, limit=None):
    s = Section("packages", "Pacman packages")
    pkgs = ctx.packages
    explicit = [p for p in pkgs.values() if p.explicit]

    # ---- by source
    repos = [r for r in ctx.repos] + ["foreign"]
    rows = []
    for r in repos:
        inr = [p for p in pkgs.values() if p.repo == r]
        if not inr:
            continue
        ex = [p for p in inr if p.explicit]
        rows.append((r if r != "foreign" else "foreign (AUR/local)", str(len(inr)), str(len(ex)),
                     human(sum(p.size for p in inr))))
    rows.append(("total", str(len(pkgs)), str(len(explicit)), human(sum(p.size for p in pkgs.values()))))
    s.block("By source", rows, ("source", "packages", "explicit", "size"), "lrrr")

    # ---- why explicit packages are here
    by_origin = {"omarchy": [], "omarchy-menu": [], "you": []}
    for p in explicit:
        by_origin[ctx.origin(p)].append(p)
    s.summary += [
        f"installed: {len(pkgs)}  (explicit {len(explicit)}, dependencies {len(pkgs) - len(explicit)})",
        f"explicit from Omarchy's base lists: {len(by_origin['omarchy'])}",
        f"explicit from Omarchy's Install menu: {len(by_origin['omarchy-menu'])}",
        f"explicit added by you: {len(by_origin['you'])}",
    ]
    s.data["origin_counts"] = {k: len(v) for k, v in by_origin.items()}

    mine = sorted(by_origin["you"] + by_origin["omarchy-menu"], key=lambda p: (p.repo, p.name))
    rows = [(p.name, p.repo, ctx.origin(p) if ctx.origin(p) != "you" else "",
             human(p.size), date(p.installed),
             _needed_by(p)) for p in mine]
    s.block(f"Explicit packages not in Omarchy's base ({len(mine)})", _cut(rows, limit),
            ("package", "repo", "via", "size", "installed", "needed by"), "lllrll",
            note=_more(rows, limit, "omarchy-tidy packages"))
    s.data["user_explicit"] = [p.name for p in mine]
    s.actions.append(Action(
        "packages.remove", "Uninstall packages you added",
        "Explicit packages outside Omarchy's base. pacman removes each with the dependencies nothing "
        "else needs (-Rns), shows the full list and asks again. It refuses anything another package requires.",
        "pick", ["sudo", "pacman", "-Rns"], sudo=True,
        items=[Item(p.name, f"{p.name:<28} {human(p.size):>8}  {p.repo:<8} {date(p.installed)}  {_needed_by(p)}", p.size)
               for p in sorted(mine, key=lambda p: p.installed) if not CRITICAL.fullmatch(p.name)]))

    # ---- orphans
    orphans = sorted((p for p in pkgs.values() if not p.explicit and not p.required_by),
                     key=lambda p: p.name)
    opt_only = [p for p in orphans if p.optional_for]
    true_orph = [p for p in orphans if not p.optional_for]
    if orphans:
        rows = [(p.name, p.repo, human(p.size),
                 ("optional for " + ", ".join(p.optional_for[:3])) if p.optional_for else "")
                for p in orphans]
        s.block(f"Orphans: dependencies nothing requires ({len(orphans)})", rows,
                ("package", "repo", "size", "note"), "llrl",
                note="The Omarchy helper removes only the ones with no note; the 'optional for' ones "
                     "add features to a package you have, so remove those by name if you don't use it."
                     if opt_only else None)
        s.suggest.append(("review and remove orphans (Omarchy's own helper)", "omarchy-update-orphan-pkgs"))
        s.actions.append(Action(
            "packages.orphans", "Remove orphaned dependencies",
            "Installed as dependencies of something since removed. Ones marked 'optional for' add a "
            "feature to a package you still have.",
            "pick", ["sudo", "pacman", "-Rns"], sudo=True,
            items=[Item(r[0], f"{r[0]:<26} {r[2]:>8}  {r[3]}", pkgs[r[0]].size) for r in rows]))
    s.summary.append(f"orphans: {len(true_orph)} (+{len(opt_only)} only optional for others)")
    s.data["orphans"] = [p.name for p in orphans]

    # ---- explicit but required by something: could be demoted to deps
    demote = sorted((p for p in explicit if p.required_by and ctx.origin(p) == "you"),
                    key=lambda p: p.name)
    if demote:
        rows = [(p.name, ", ".join(p.required_by[:4]) + (" …" if len(p.required_by) > 4 else ""))
                for p in demote]
        s.block(f"Marked explicit, but other packages require them ({len(demote)})",
                _cut(rows, limit), ("package", "required by"),
                note="Marking these as dependencies lets them go away with their parent. "
                     + (_more(rows, limit, "omarchy-tidy packages") or ""))
        s.suggest.append(("demote a package so it becomes an orphan when its parent goes",
                          "sudo pacman -D --asdeps <package>"))
        s.actions.append(Action(
            "packages.demote", "Mark packages as dependencies",
            "Nothing is removed now: these just stop being 'explicit', so they become orphans "
            "(and get offered for removal) once the package that needs them is gone.",
            "pick", ["sudo", "pacman", "-D", "--asdeps"], sudo=True,
            items=[Item(p.name, f"{p.name:<24} needed by {', '.join(p.required_by[:4])}") for p in demote
                   if not CRITICAL.fullmatch(p.name)]))

    # ---- biggest things you added
    big = sorted(mine, key=lambda p: -p.size)[:limit or 20]
    s.block("Largest packages you added", [(p.name, human(p.size), p.repo) for p in big],
            ("package", "size", "repo"), "lrl")

    # ---- recent explicit installs
    cutoff = time.time() - RECENT_DAYS * 86400
    recent = sorted((p for p in mine if p.installed >= cutoff), key=lambda p: -p.installed)
    if recent:
        s.block(f"Explicitly installed in the last {RECENT_DAYS} days ({len(recent)})",
                [(p.name, date(p.installed), p.repo) for p in recent],
                ("package", "installed", "repo"))

    # ---- Omarchy base packages you've removed (informational)
    missing = sorted(n for n in ctx.omarchy_base if not ctx.resolve(n))
    if missing:
        s.summary.append(f"Omarchy base packages not installed: {len(missing)} ({', '.join(missing[:8])}"
                         + (" …)" if len(missing) > 8 else ")"))

    # ---- foreign packages vs the AUR
    foreign = sorted((p for p in pkgs.values() if p.repo == "foreign"), key=lambda p: p.name)
    devel = [p for p in foreign if p.name.endswith(("-git", "-hg", "-svn", "-bzr"))]
    s.summary.append(f"foreign (AUR/local): {len(foreign)}  (VCS/-git builds: {len(devel)})")
    if foreign:
        aur = {} if ctx.offline else _aur_info([p.name for p in foreign], s)
        rows = []
        for p in foreign:
            status = _aur_status(p, (aur or {}).get(p.name), ctx.offline or aur is None)
            rows.append((p.name, p.version, "exp" if p.explicit else "dep", human(p.size), status))
        s.block(f"Foreign packages ({len(foreign)})", rows,
                ("package", "version", "reason", "size", "AUR status"), "lllrl")
        s.data["foreign"] = {r[0]: r[4] for r in rows}

    # ---- pacman cache
    _pacman_cache(s)
    return s


def _needed_by(p):
    if p.required_by:
        return "required: " + ", ".join(p.required_by[:2]) + (" …" if len(p.required_by) > 2 else "")
    if p.optional_for:
        return "optional: " + ", ".join(p.optional_for[:2]) + (" …" if len(p.optional_for) > 2 else "")
    return ""


def _cut(rows, limit):
    return rows if not limit else rows[:limit]


def _more(rows, limit, cmd):
    if limit and len(rows) > limit:
        return f"… {len(rows) - limit} more — run `{cmd}` for the full list"
    return None


def _aur_info(names, s):
    """One AUR RPC call per 150 names. Returns {} with a note on failure."""
    progress("querying the AUR…")
    info = {}
    for i in range(0, len(names), 150):
        q = urllib.parse.urlencode([("arg[]", n) for n in names[i:i + 150]])
        try:
            with urllib.request.urlopen(f"https://aur.archlinux.org/rpc/v5/info?{q}", timeout=15) as r:
                for res in json.load(r).get("results", []):
                    info[res["Name"]] = res
        except Exception as e:
            s.notes.append(f"AUR lookup failed ({e}); AUR status column is blank. Use --offline to skip it.")
            return None
    return info


def _aur_status(p, res, unknown):
    if unknown:
        return ""
    if res is None:
        return yellow("not in AUR — local build or removed upstream")
    bits = []
    rc, out, _ = run(["vercmp", p.version, res["Version"]])
    if rc == 0 and out.strip().lstrip("-").isdigit() and int(out) < 0:
        bits.append(f"update {res['Version']}")
    if res.get("OutOfDate"):
        bits.append(yellow(f"flagged out-of-date {age(res['OutOfDate'])} ago"))
    if not res.get("Maintainer"):
        bits.append(yellow("orphaned (no maintainer)"))
    if res.get("LastModified") and time.time() - res["LastModified"] > 2 * 365 * 86400:
        bits.append(f"untouched {age(res['LastModified'])}")
    return ", ".join(bits) or "ok"


def _pacman_cache(s):
    from .util import du
    cache = "/var/cache/pacman/pkg"
    size = du([cache]).get(cache)
    s.summary.append(f"pacman package cache: {human(size)} ({cache})")
    for flags, why, fix in (
        (["-d", "-k2"], "old versions beyond the 2 newest", "omarchy-update-pkg-prune   # = sudo paccache -rk2"),
        (["-d", "-u", "-k0"], "cached packages that are no longer installed", "sudo paccache -ruk0"),
    ):
        rc, out, err = run(["paccache", *flags])
        if rc == 127:
            s.notes.append("paccache not found (pacman-contrib); cache pruning estimate skipped")
            return
        line = next((l for l in out.splitlines() if "finished dry run" in l or "no candidate" in l), "")
        if "finished dry run" in line:
            detail = line.split("finished dry run:", 1)[1].strip()
            s.summary.append(f"  {why}: {detail}")
            s.suggest.append((f"cache: {why}", fix))
            argv = ["sudo", "paccache", "-r" + ("uk0" if "-u" in flags else "k2")]
            s.actions.append(Action(f"packages.cache.{'uninstalled' if '-u' in flags else 'old'}",
                                    f"Prune pacman cache: {why}", detail, "cmd", argv, sudo=True))
