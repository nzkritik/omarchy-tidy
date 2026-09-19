# omarchy-tidy

An audit of an Omarchy (Arch + Hyprland) system: what's installed, where it came from, and what
uninstalls left behind. The report commands are read-only. Every finding comes with the command you'd
run to clean it up, and `omarchy-tidy clean` walks through those cleanups with you, one step at a time.

## Who this is for

It's meant for **older Omarchy installs**, and for any Omarchy system that has seen **a lot of package
testing**: installing things to try them, removing them, switching between AUR builds, trying AI tools
that each bring their own Python, Node or model cache. Uninstalling a package doesn't remove
everything that came with it. Over time you collect:
- orphaned dependencies
- AUR build folders for software that's long gone
- config folders for apps you no longer have
- `.pacnew` files you never merged
- Omarchy pre-upgrade backups
- broken symlinks and launchers
- tens or hundreds of gigabytes of caches.

On a fresh Omarchy install there's little to find. The report will mostly confirm that.

## Read before you run `clean`

**Understand the report before you clean anything.** The `clean` step runs real commands:

- `sudo pacman -Rns` (remove packages together with dependencies nothing else needs)
- `sudo paccache` and `sudo pacdiff`
- `sudo journalctl --vacuum-size`
- `uv`/`pip`/`npm`/`go` cache commands
- permanent deletion of caches, AUR build folders and stale git clones
- moving config folders and backups to the Trash.

It asks before each step, nothing is preselected, and pacman asks again. But it can't know that a
package you installed two years ago is the one your printer needs, or that a "leftover" config folder
belongs to a program you run from a script.

Suggested order:

1. Run `omarchy-tidy` and read the overview. Then read the detailed sections (`omarchy-tidy packages`,
   `omarchy-tidy leftovers`, …) for the areas you plan to clean.
2. Look up any command in the **Suggested** lines that you don't recognise. Know what `pacman -Rns`,
   `paccache -ruk0` or `pacdiff` will do before you let this tool run them.
3. Run `omarchy-tidy clean --dry-run` to see every step and item it would offer.
4. Clean one area at a time, starting with the low-risk ones. For example, run
   `omarchy-tidy clean caches` before `omarchy-tidy clean packages`.
5. Have a backup or a snapshot (Omarchy's btrfs/snapper snapshots, or your own) before you remove packages.

The folder-name matching in **leftovers** is a guess. Treat every entry there as a suggestion to check,
not a verdict. You use this tool at your own risk. See [LICENSE](LICENSE).

```
omarchy-tidy                 # overview: every section's summary + suggested commands
omarchy-tidy packages        # full pacman/AUR profile
omarchy-tidy others          # pipx, uv, npm -g, cargo, go, gem, mise, conda, docker, flatpak, AppImages
omarchy-tidy clones          # git repos under ~ and yay/paru build clones
omarchy-tidy caches          # caches and data stores by size
omarchy-tidy leftovers       # orphaned config dirs, unowned system files, .pacnew, backups, broken links
omarchy-tidy all             # every section in full

omarchy-tidy --json <section>   # machine-readable
omarchy-tidy --offline          # skip the AUR lookup
omarchy-tidy --save             # also write a snapshot to ~/.local/state/omarchy-tidy/
omarchy-tidy diff [old new]     # packages installed/removed/re-marked between snapshots

omarchy-tidy clean --dry-run    # show every cleanup step and item; change nothing
omarchy-tidy clean              # interactive cleanup
omarchy-tidy clean packages caches   # only these sections
```

## Install

```bash
git clone https://github.com/nzkritik/omarchy-tidy.git
ln -s "$PWD/omarchy-tidy/omarchy-tidy" ~/.local/bin/omarchy-tidy
sudo pacman -S --needed expac pacman-contrib
```

The command is named `omarchy-tidy` rather than `tidy` because Arch's `extra` repo ships HTML Tidy as
`/usr/bin/tidy`.

Needs only Python 3 and `expac`. `pacman-contrib` (paccache, pacdiff) is optional; `gum` (shipped
with Omarchy) makes the clean prompts nicer. Run it as your normal user:
it refuses to run as root.

## What each section reports

**packages**: counts by repo (core / extra / multilib / omarchy / foreign). Every explicit package
is labelled by why it's there:
- Omarchy's base lists (`~/.local/share/omarchy/install/*.packages`, provides-aware)
- Omarchy's Install menu (packages named in `omarchy-pkg-add` calls in Omarchy's own scripts)
- added by you.

It also lists orphans (these match `pacman -Qdttq`; the ones that are only *optional* dependencies
are marked). Other checks:
- explicit packages that something else requires (candidates for `pacman -D --asdeps`)
- your biggest and most recent installs
- each foreign package's AUR status: not in the AUR, orphaned, flagged out of date, update available,
  or untouched for years
- what `paccache` could prune.

**others**: everything pacman doesn't track, including pipx venvs whose Python interpreter was removed
by an upgrade, and hand-placed executables in `~/.local/bin` and `/usr/local/bin`. That covers
broken symlinks and binaries that shadow a packaged command.

**clones**: every git repo under `~`, with size, last commit, last used, remote, and a verdict:
- *local work*: uncommitted, unpushed, stashed, or no remote. Deleting it loses something.
- *stale*: clean, pushed, and untouched for 180+ days, so it can be re-cloned later.
- *clean & pushed*
- *managed*: Omarchy itself, its plugins and themes, tmux plugins, and Omarchy's pre-upgrade backup
  of itself.
- *synced*: inside `~/Dropbox`, Nextcloud, OneDrive or Sync. It's never called stale, because deleting
  it also deletes the cloud copy.

Also shows yay/paru build clones for packages you've since removed.

**caches**: sizes of known caches (uv, pip, Hugging Face, yay, npm, go, cargo, pacman, journal,
core dumps, libvirt images, …) with the command that reclaims each, plus anything else in `~/.cache`
over 256M. On a compressed filesystem (btrfs with `compress=zstd`, as Omarchy installs by default)
the report says so: every size comes from `du`, which reports uncompressed sizes, so a deletion frees
less disk space than the figure suggests. Hugging Face models and VM disks are labelled as data, not cache. `clean` offers models one
at a time (`hf cache rm`), and VM disks are left for you to handle.

**leftovers**:
- dirs in `~/.config`, `~/.local/share`, `~/.local/state`, `~/.cache` and `~/.*` whose name matches
  no installed package, packaged file or command on `$PATH`
- files in `/usr/bin`, `/opt`, `/usr/lib/modules`, `/usr/local`, systemd unit dirs that no package owns
- `.pacnew`/`.pacsave` files
- `.bak` files and Omarchy pre-upgrade backups
- broken symlinks
- launchers whose program is gone
- Python venvs and pip `--user` sites whose interpreter is gone
- failed systemd units.

The app-dir match is a heuristic. Check before deleting anything, and hide dirs you're keeping by
adding a name or glob per line to `~/.config/omarchy-tidy/ignore`.

## Clean mode

`clean` turns the findings into steps, in section order:

| Section | Steps |
|---|---|
| packages | uninstall packages you added (oldest install first) · remove orphans · mark packages as dependencies · prune the pacman cache |
| others | uninstall pipx apps whose Python is gone · `mise prune` · `docker system prune` |
| clones | delete stale git clones · delete yay/paru build clones of removed packages |
| caches | delete regenerable cache folders · uv / pip / npm / go cache commands · core dumps · journal vacuum · pick Hugging Face models · other large `~/.cache` entries |
| leftovers | app folders of uninstalled software · `pacdiff` · old backups · broken symlinks · dead launchers · broken venvs |

Steps only appear when there's something to do. A path found by two sections (a big `~/.cache` folder
of an uninstalled app, say) is offered only once. Before each step it shows what the step does and how much space
it frees. Then it either asks yes/no (`gum confirm`) or opens a multi-select (`gum choose`) where
**nothing is preselected**. Esc skips a step and Ctrl-C stops the run. Without gum it falls back to
typed prompts.

- **Packages:** `sudo pacman -Rns` runs without `--noconfirm`, so pacman shows the whole transaction
  and asks again. The kernel, microcode, bootloader, NVIDIA driver, firmware, pacman/yay, systemd,
  NetworkManager, keyrings and `omarchy*` packages are never offered.
- **Recoverable vs not:** config dirs, backups, launchers and broken venvs go to the Trash
  (`gio trash`). Caches, yay build clones and stale git clones are deleted, because trashing them frees
  nothing. Every confirmation says which it will be.
- **Path guards:** a path is only removed if it sits strictly inside the folder its step allows and is
  not a protected folder (`~`, `~/.config`, `~/.ssh`, `~/Work`, …). No folder between it and that
  allowed folder may be a symlink, and it must still be the same inode the scan saw. Symlinks are
  unlinked, never followed, and directories are removed with `shutil.rmtree`, which is
  symlink-attack resistant. Each stale git clone is re-inspected right before deletion and skipped if
  it has gained local work.
- **Synced folders:** clones inside `~/Dropbox` (and Nextcloud/OneDrive/Sync) are never offered,
  because deleting them removes the cloud copy too.
- **Root:** sudo only ever runs fixed commands with validated package names. It never gets a path from
  your home folder.
- Every run ends with the free-space change, and every action is appended to
  `~/.local/state/omarchy-tidy/clean.log`. Items moved to the Trash still use disk space until you
  empty it. `gio trash --list` shows them, and your file manager can restore them.
- It refuses to run without a terminal (except `--dry-run`), and refuses to run as root.

## Guarantees (report commands)

- Read-only. Git is run with `GIT_OPTIONAL_LOCKS=0`, so even `git status` doesn't rewrite
  `.git/index`. Without that flag every scan would reset each repo's "last used" time.
- The only network call is one AUR RPC request listing your foreign package names (`--offline` skips it).
- Remote URLs have any `user:token@` stripped before they're printed or saved.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

`tests/test_clean.py` covers what `clean` relies on to be safe:
- the path guards, tested against a planted symlinked parent, a path swapped after the scan, a
  replaced file, `..`, relative paths, the root itself and the protected folders
- symlinks being unlinked rather than followed, trash vs delete, and dry runs changing nothing
- the log refusing a planted symlink
- package-name validation before anything reaches `sudo`
- the protected-package list
- git clones re-checked for uncommitted, unpushed and stashed work before deletion
- a regression test that inspecting a repo never rewrites its `.git/index`.

The tests delete real files, so they run in a throwaway `HOME` (plus the XDG folders), which is set
before `tidy_lib` is imported. They abort if the tool sees any other `HOME`, and remove the sandbox
afterwards. Standard library only; `git` and `gio` tests are skipped when those tools are missing.

When adding a guard, check that its test fails with the guard removed. A test that passes for the
wrong reason (for example, a path the test never created reports "already gone") protects nothing.

## Layout

```
omarchy-tidy         entry point: argument parsing, rendering, snapshots, diff
tidy_lib/util.py     Ctx (pacman db, sync repos, owned files, Omarchy lists), Section, helpers
tidy_lib/packages.py tidy_lib/others.py tidy_lib/clones.py tidy_lib/caches.py tidy_lib/leftovers.py
tidy_lib/clean.py    the interactive runner: prompts, path guards, trash/delete, log
tests/test_clean.py  sandboxed safety tests (unittest)
```

Each section module has `collect(ctx, limit) -> Section`. A Section carries report blocks plus `actions`
(`cmd` / `pick` / `paths`), which `clean.py` executes. The entry point renders it as text or JSON.
