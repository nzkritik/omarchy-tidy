"""Everything installed outside pacman: flatpak, pipx, uv, npm, cargo, go, gem,
mise, conda, docker, AppImages, and hand-placed binaries."""

import json
import os
from pathlib import Path

from .util import HOME, Action, Item, Section, age, du, have, human, mtime, progress, run, tilde, yellow


def collect(ctx, limit=None):
    s = Section("others", "Other package managers")
    rows = []          # (manager, count, size, where)
    detail = {}        # manager -> [names]

    def add(manager, names, size_paths=(), where=""):
        names = sorted(set(names))
        sizes = du(size_paths) if size_paths else {}
        rows.append((manager, str(len(names)), human(sum(sizes.values())) if sizes else "",
                     where or ", ".join(tilde(p) for p in size_paths if Path(p).exists())))
        if names:
            detail[manager] = names

    progress("checking other package managers…")

    # flatpak
    if have("flatpak"):
        rc, out, _ = run(["flatpak", "list", "--app", "--columns=application,installation"])
        apps = [l.split("\t")[0] for l in out.splitlines() if l.strip()]
        add("flatpak apps", apps, ["/var/lib/flatpak", HOME / ".local/share/flatpak"])
        rc, out, _ = run(["flatpak", "uninstall", "--unused", "--assumeno"])
        if "Nothing unused" not in out and rc in (0, 1) and out.strip():
            s.suggest.append(("flatpak runtimes no app uses", "flatpak uninstall --unused"))
    else:
        s.notes.append("flatpak not installed")

    # pipx
    # Read the venvs directly: `pipx list` aborts on a venv whose Python is gone.
    pipx_home = Path(os.environ.get("PIPX_HOME", HOME / ".local/share/pipx"))
    venvs = sorted(p for p in (pipx_home / "venvs").glob("*") if p.is_dir())
    if venvs or have("pipx"):
        add("pipx", [v.name for v in venvs], [pipx_home])
        broken = [v for v in venvs if not (v / "bin/python").exists()]
        if broken:
            s.block("pipx apps whose Python interpreter is gone", [
                (v.name, yellow("→ " + os.readlink(v / "bin/python") if (v / "bin/python").is_symlink() else "missing"))
                for v in broken], ("app", "interpreter"),
                note="A Python upgrade removed the version these were built with; they no longer run.")
            s.suggest.append(("rebuild pipx apps against the current Python (or `pipx uninstall <app>`)",
                              "pipx reinstall-all"))
            sizes = du(broken)
            s.actions.append(Action(
                "others.pipx.uninstall", "Uninstall pipx apps whose Python is gone",
                "They can't run as they are. Pick the ones you no longer want; to keep one instead, "
                "skip this and run `pipx reinstall-all`.",
                "pick", ["pipx", "uninstall"], each=True,
                items=[Item(v.name, f"{v.name}  {human(sizes.get(str(v)))}", sizes.get(str(v))) for v in broken]))

    # uv tools + python builds
    if have("uv"):
        rc, out, _ = run(["uv", "tool", "list"])
        tools = [l.split()[0] for l in out.splitlines() if l and not l.startswith(("-", " ", "No tools"))]
        rc, tdir, _ = run(["uv", "tool", "dir"])
        add("uv tools", tools, [tdir.strip()] if tdir.strip() else [])
        rc, out, _ = run(["uv", "python", "list", "--only-installed"])
        pys = [l.split()[0] for l in out.splitlines() if l.strip() and "/uv/python/" in l]
        rc, pdir, _ = run(["uv", "python", "dir"])
        add("uv pythons", pys, [pdir.strip()] if pdir.strip() else [])
    else:
        s.notes.append("uv not installed")

    # npm global
    if have("npm"):
        rc, out, _ = run(["npm", "ls", "-g", "--depth=0", "--json"], timeout=60)
        try:
            deps = list(json.loads(out or "{}").get("dependencies", {}))
        except json.JSONDecodeError:
            deps = []
        rc, root, _ = run(["npm", "root", "-g"])
        root = root.strip()
        # npm/corepack ship with node itself; only count what you added.
        deps = [d for d in deps if d not in ("npm", "corepack")]
        add("npm -g", deps, [root] if root and not root.startswith("/usr/") else [],
            where=root)
    else:
        s.notes.append("npm not installed")

    # cargo
    cargo_home = Path(os.environ.get("CARGO_HOME", HOME / ".cargo"))
    if have("cargo") or cargo_home.exists():
        rc, out, _ = run(["cargo", "install", "--list"])
        crates = [l.split()[0] for l in out.splitlines() if l and not l.startswith(" ")]
        add("cargo install", crates, [cargo_home])
    rustup = Path(os.environ.get("RUSTUP_HOME", HOME / ".rustup"))
    if rustup.exists():
        tcs = [p.name for p in (rustup / "toolchains").glob("*")]
        add("rustup toolchains", tcs, [rustup])

    # go
    gopath = Path(os.environ.get("GOPATH", HOME / "go"))
    if gopath.exists():
        add("go install", [p.name for p in (gopath / "bin").glob("*")], [gopath])

    # ruby gems (user)
    gems = list((HOME / ".local/share/gem").glob("ruby/*/gems/*")) + list((HOME / ".gem").glob("ruby/*/gems/*"))
    if gems:
        add("gem --user", [p.name for p in gems],
            [p for p in (HOME / ".local/share/gem", HOME / ".gem") if p.exists()])

    # mise
    if have("mise"):
        rc, out, _ = run(["mise", "ls", "--json"])
        try:
            tools = [f"{t}@{v['version']}" for t, vs in json.loads(out or "{}").items() for v in vs]
        except (json.JSONDecodeError, TypeError, KeyError):
            tools = []
        add("mise", tools, [HOME / ".local/share/mise"])
        names = [t.split("@")[0] for t in tools]
        if len(set(names)) < len(names):
            s.suggest.append(("mise keeps every version it ever installed; drop the ones no config uses",
                              "mise prune --dry-run   # then without --dry-run"))
            s.actions.append(Action("others.mise.prune", "Prune unused mise tool versions",
                                    "Removes versions no mise config references; mise lists them and asks.",
                                    "cmd", ["mise", "prune"]))

    # conda
    for base in (HOME / "miniconda3", HOME / "anaconda3", HOME / "miniforge3", HOME / ".conda"):
        if base.exists():
            envs = [p.name for p in (base / "envs").glob("*") if p.is_dir()]
            add(f"conda ({tilde(base)})", envs, [base])

    # docker
    if have("docker"):
        rc, out, err = run(["docker", "system", "df", "--format", "{{.Type}}\t{{.TotalCount}}\t{{.Size}}\t{{.Reclaimable}}"], timeout=30)
        if rc == 0:
            for l in out.splitlines():
                t, n, size, recl = l.split("\t")
                rows.append((f"docker {t.lower()}", n, size, f"reclaimable {recl}"))
            s.suggest.append(("docker: stopped containers, dangling images, build cache",
                              "docker system prune   # add -a to drop all unused images"))
            s.actions.append(Action("others.docker.prune", "Docker: prune stopped containers, dangling images, build cache",
                                    "docker lists what it will remove and asks.", "cmd", ["docker", "system", "prune"]))
        else:
            rows.append(("docker", "?", "", "/var/lib/docker (root-only)"))
            s.notes.append("docker daemon not reachable (not running, or you're not in the docker group); "
                           "counts skipped")

    # AppImages
    progress("looking for AppImages…")
    appimgs = []
    for d in (HOME / "Applications", HOME / "Downloads", HOME / ".local/bin", HOME / "bin", HOME / "Apps", HOME):
        if d.is_dir():
            appimgs += [p for p in d.glob("*") if p.name.lower().endswith(".appimage")]
    if appimgs:
        sizes = du(appimgs)
        s.block(f"AppImages ({len(appimgs)})",
                [(tilde(p), human(sizes.get(str(p))), age(mtime(p))) for p in sorted(appimgs)],
                ("file", "size", "age"), "lrr")

    s.block("Package managers", rows, ("manager", "items", "size", "location"), "lrrl")
    s.blocks.insert(0, s.blocks.pop())
    for m, names in detail.items():
        shown = names if not limit else names[:limit]
        s.block(f"{m} ({len(names)})", [], (),
                note=", ".join(shown) + (" …" if len(shown) < len(names) else ""))
    s.data["managers"] = detail

    _hand_placed(s, ctx)
    return s


def _hand_placed(s, ctx):
    """Executables in ~/.local/bin, ~/bin and /usr/local/bin — nothing tracks these."""
    rows = []
    for d in (HOME / ".local/bin", HOME / "bin", Path("/usr/local/bin"), Path("/usr/local/sbin")):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_symlink() and not p.exists():
                rows.append((tilde(p), yellow("broken symlink → " + os.readlink(p)), ""))
            elif p.is_symlink():
                rows.append((tilde(p), "→ " + tilde(os.readlink(p)), age(mtime(p))))
            elif p.is_file():
                shadows = f"/usr/bin/{p.name}" in ctx.owned_files
                rows.append((tilde(p), yellow("shadows /usr/bin/" + p.name) if shadows else "", age(mtime(p))))
    if rows:
        s.block(f"Hand-placed executables ({len(rows)})", rows, ("path", "note", "age"), "llr",
                note="Nothing owns these, so no uninstall will ever remove them.")
