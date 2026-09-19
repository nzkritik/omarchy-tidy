"""Where disk space goes that no package accounts for: caches, logs, core dumps."""

import json
import re
from pathlib import Path

from .util import HOME, Action, Item, Section, du, have, human, path_item, progress, run, tilde

C = HOME / ".cache"
# path -> (what it is, how to reclaim it). Checked in this order; missing paths are skipped.
KNOWN = [
    (C / "uv", "uv package cache", "uv cache prune   # or: uv cache clean"),
    (C / "pip", "pip wheel cache", "pip cache purge"),
    (C / "huggingface", "Hugging Face models/datasets", "hf cache ls   # then: hf cache rm <id>   (or pick them in `omarchy-tidy clean caches`)"),
    (C / "yay", "yay AUR build clones", None),      # cleanup advice lives in `omarchy-tidy clones`
    (C / "paru", "paru AUR build clones", None),
    (C / "go-build", "Go build cache", "go clean -cache"),
    (HOME / "go/pkg/mod", "Go module cache", "go clean -modcache"),
    (HOME / ".npm", "npm cache", "npm cache clean --force"),
    (C / "pnpm", "pnpm store", "pnpm store prune"),
    (HOME / ".cargo/registry", "cargo registry", "cargo cache -a   # needs cargo-cache; or rm -rf ~/.cargo/registry/cache"),
    (HOME / ".gradle/caches", "Gradle cache", "rm -rf ~/.gradle/caches"),
    (HOME / ".m2/repository", "Maven repository", "rm -rf ~/.m2/repository"),
    (HOME / ".pub-cache", "Dart/Flutter pub cache", "dart pub cache clean"),
    (C / "ms-playwright", "Playwright browsers", "rm -rf ~/.cache/ms-playwright"),
    (C / "thumbnails", "file-manager thumbnails", "rm -rf ~/.cache/thumbnails"),
    (C / "mesa_shader_cache", "Mesa shader cache", "rm -rf ~/.cache/mesa_shader_cache"),
    (HOME / ".ollama/models", "Ollama models", "ollama list; ollama rm <model>"),
    (HOME / ".lmstudio/models", "LM Studio models", "remove in LM Studio's My Models"),
    (HOME / ".local/share/Trash", "Trash", "rm -rf ~/.local/share/Trash/*"),
    (Path("/var/cache/pacman/pkg"), "pacman package cache", "omarchy-update-pkg-prune"),
    (Path("/var/lib/systemd/coredump"), "core dumps", "sudo rm /var/lib/systemd/coredump/*"),
    (Path("/var/log/journal"), "systemd journal", "sudo journalctl --vacuum-size=500M"),
    (Path("/var/lib/docker"), "Docker data", "docker system prune"),
    (Path("/var/lib/libvirt/images"), "libvirt VM disks (data, not cache)",
     "virsh -c qemu:///system list --all   # then for VMs you're done with: virsh undefine --remove-all-storage <vm>"),
]
# How `clean` reclaims each known cache: (argv, needs sudo) runs a tool; "dir" deletes the
# directory itself; missing = report only (models and VM disks are data — choose those by hand).
RECLAIM = {
    C / "uv": (["uv", "cache", "prune"], False),
    C / "pip": (["pip", "cache", "purge"], False),
    C / "go-build": (["go", "clean", "-cache"], False),
    HOME / "go/pkg/mod": (["go", "clean", "-modcache"], False),
    HOME / ".npm": (["npm", "cache", "clean", "--force"], False),
    C / "pnpm": (["pnpm", "store", "prune"], False),
    HOME / ".pub-cache": (["dart", "pub", "cache", "clean"], False),
    HOME / ".local/share/Trash": (["gio", "trash", "--empty"], False),
    Path("/var/lib/systemd/coredump"): (["sudo", "find", "/var/lib/systemd/coredump", "-xdev", "-type", "f", "-delete"], True),
    Path("/var/log/journal"): (["sudo", "journalctl", "--vacuum-size=500M"], True),
    C / "ms-playwright": "dir", C / "thumbnails": "dir", C / "mesa_shader_cache": "dir",
    HOME / ".gradle/caches": "dir", HOME / ".m2/repository": "dir", HOME / ".cargo/registry": "dir",
}
BIG = 256 << 20   # only mention unknown cache dirs above this


def collect(ctx, limit=None):
    s = Section("caches", "Caches and disk hogs")
    progress("sizing caches (the first run over a large ~/.cache takes a while)…")
    known = [(p, what, fix) for p, what, fix in KNOWN if p.exists()]
    sizes = du([p for p, _, _ in known])
    rows = []
    dir_items = []
    total = 0
    for p, what, fix in sorted(known, key=lambda k: -(sizes.get(str(k[0])) or 0)):
        n = sizes.get(str(p))
        if not n or n < 1 << 20:
            continue
        total += n
        rows.append((tilde(p), human(n), what))
        if n >= BIG and fix:
            s.suggest.append((f"{what} ({human(n)})", fix))
        how = RECLAIM.get(p)
        if n >= 64 << 20 and isinstance(how, tuple) and have(how[0][1] if how[1] else how[0][0]):
            s.actions.append(Action(f"caches.{p.name}", f"{what} ({human(n)})",
                                    f"{tilde(p)} — the tool downloads or rebuilds what it needs again.",
                                    "cmd", how[0], sudo=how[1], size=n))
        elif n >= 64 << 20 and how == "dir":
            dir_items.append(path_item(p, f"{tilde(p):<40} {human(n):>8}  {what}", n))
    s.block("Known caches", rows, ("path", "size", "what"), "lrl",
            note="Root-owned paths are sized as far as your user can read them.")

    _huggingface(s)

    # Everything else in ~/.cache, largest first.
    known_paths = {str(p) for p, _, _ in known}
    other = [p for p in C.iterdir() if str(p) not in known_paths] if C.is_dir() else []
    osz = du(other)
    big = sorted(((p, osz.get(str(p)) or 0) for p in other), key=lambda t: -t[1])
    big = [(p, n) for p, n in big if n >= BIG]
    if big:
        s.block("Other large ~/.cache entries", [(tilde(p), human(n)) for p, n in big[:limit or 30]],
                ("path", "size"), "lr",
                note="Anything in ~/.cache is safe to delete by definition; apps rebuild it. "
                     "Close the app first.")
        s.actions.append(Action(
            "caches.other", "Delete other large ~/.cache entries",
            "Apps rebuild their cache on next start. Close the app first; a browser cache means "
            "slower first page loads, not lost data.",
            "paths", root=C, items=[path_item(p, f"{tilde(p):<36} {human(n):>8}", n) for p, n in big]))
    if dir_items:
        s.actions.insert(0, Action("caches.dirs", "Delete regenerable cache directories",
                                   "Downloaded or generated on demand; nothing personal lives here.",
                                   "paths", root=HOME, items=dir_items))
    cache_total = sum(osz.values()) + sum(sizes.get(str(p)) or 0 for p, _, _ in known if p.parent == C)
    s.summary.append(f"~/.cache total: {human(cache_total)}")
    s.summary.append(f"known caches and data stores (incl. system paths): {human(total)}")

    rc, out, _ = run(["journalctl", "--disk-usage"])
    if rc == 0 and out.strip():
        s.summary.append("journal: " + out.strip().removeprefix("Archived and active journals take up ")
                         .removesuffix(" in the file system."))

    rc, out, _ = run(["df", "-h", "--output=source,target,size,used,avail,pcent", "/", str(HOME), "/var"])
    if rc == 0:
        seen, rows = set(), []
        for l in out.splitlines()[1:]:
            f = l.split()
            if f[0] not in seen:          # one row per filesystem, not per mount
                seen.add(f[0])
                rows.append(tuple(f[1:]))
        s.block("Filesystems", rows, ("mount", "size", "used", "free", "use%"), "lrrrr")
    s.data["known"] = {tilde(p): sizes.get(str(p)) for p, _, _ in known}
    return s


def _huggingface(s):
    """Hugging Face models are data you chose to download: offer each one separately."""
    if not have("hf") or not (C / "huggingface").exists():
        return
    rc, out, err = run(["hf", "cache", "ls", "--format", "json", "--sort", "size:desc"], timeout=120)
    try:
        repos = json.loads(out)
    except json.JSONDecodeError:
        s.notes.append(f"`hf cache ls` gave no usable output ({(err or out).strip()[:80]}); model picker skipped")
        return
    items = []
    for r in repos if isinstance(repos, list) else []:
        rid = r.get("id") or f"{r.get('repo_type', 'model')}/{r.get('repo_id', '')}"
        size = _parse_size(r.get("size_on_disk") or r.get("size"))
        seen = r.get("last_accessed_str") or r.get("last_accessed") or ""
        items.append(Item(rid, f"{rid:<60} {human(size):>8}  used {seen}", size))
    if items:
        s.actions.append(Action(
            "caches.huggingface", "Delete Hugging Face models you no longer use",
            "Downloaded models, not a cache: anything that needs one downloads it again. "
            "hf shows the files and asks before deleting.",
            "pick", ["hf", "cache", "rm"], items=items,
            valid=r"(model|dataset|space)/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?"))


def _parse_size(v):
    """hf prints sizes as text ('22.4G'); newer versions may give bytes."""
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]?)B?\s*", str(v or ""), re.I)
    if not m:
        return None
    return int(float(m.group(1)) * 1024 ** "_KMGT".index(m.group(2).upper() or "_"))
