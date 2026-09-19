# omarchy-tidy

An audit of an Omarchy (Arch + Hyprland) system: what's installed, where it came from, and what
uninstalls left behind. The report commands are read-only. Every finding comes with the command you'd
run to clean it up, and `omarchy-tidy clean` walks through those cleanups with you, one step at a time.

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

Install: `ln -s ~/Work/omarchy-tidy/omarchy-tidy ~/.local/bin/omarchy-tidy`. The command is named
`omarchy-tidy` rather than `tidy` because Arch's `extra` repo ships HTML Tidy as `/usr/bin/tidy`.

Needs only Python 3 and `expac`. `paccache` (pacman-contrib) is optional. Run it as your normal user:
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
- *managed*: Omarchy itself, its plugins and themes, and tmux plugins.

Also shows yay/paru build clones for packages you've since removed.

**caches**: sizes of known caches (uv, pip, Hugging Face, yay, npm, go, cargo, pacman, journal,
core dumps, libvirt images, …) with the command that reclaims each, plus anything else in `~/.cache`
over 256M.

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

`clean` turns the findings into steps: uninstall packages you added, remove orphans, prune caches,
delete yay clones and stale git clones, trash leftover config and backups, fix broken links and dead
launchers, and merge `.pacnew` files. Before each step it shows what the step does and how much space
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
  `~/.local/state/omarchy-tidy/clean.log`.

## Guarantees (report commands)

- Read-only. Git is run with `GIT_OPTIONAL_LOCKS=0`, so even `git status` doesn't rewrite
  `.git/index`. Without that flag every scan would reset each repo's "last used" time.
- The only network call is one AUR RPC request listing your foreign package names (`--offline` skips it).
- Remote URLs have any `user:token@` stripped before they're printed or saved.

## Layout

```
omarchy-tidy         entry point: argument parsing, rendering, snapshots, diff
tidy_lib/util.py     Ctx (pacman db, sync repos, owned files, Omarchy lists), Section, helpers
tidy_lib/packages.py tidy_lib/others.py tidy_lib/clones.py tidy_lib/caches.py tidy_lib/leftovers.py
tidy_lib/clean.py    the interactive runner: prompts, path guards, trash/delete, log
```

Each section module has `collect(ctx, limit) -> Section`. A Section carries report blocks plus `actions`
(`cmd` / `pick` / `paths`), which `clean.py` executes. The entry point renders it as text or JSON.
