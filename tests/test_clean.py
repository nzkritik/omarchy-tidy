"""Safety tests for `omarchy-tidy clean` and the helpers it trusts.

Run from the repo root:  python3 -m unittest discover -s tests -v

These tests delete real files, so everything happens in a throwaway HOME. HOME and the XDG
dirs are pointed at it BEFORE tidy_lib is imported (tidy_lib reads HOME at import time), and
the run aborts if tidy_lib ended up with any other HOME.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="omarchy-tidy-test-")).resolve()
for var, sub in (("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                 ("XDG_STATE_HOME", ".local/state"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[var] = str(SANDBOX / sub) if sub else str(SANDBOX)
os.environ["NO_COLOR"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tidy_lib import clean, clones  # noqa: E402
from tidy_lib.caches import _parse_size  # noqa: E402
from tidy_lib.packages import CRITICAL  # noqa: E402
from tidy_lib.util import HOME, Action, Item, path_item  # noqa: E402

if HOME != SANDBOX or clean.LOG.parent.parent.parent != SANDBOX / ".local":
    sys.exit(f"refusing to run: tidy_lib HOME is {HOME}, expected the sandbox {SANDBOX}")


def tearDownModule():
    shutil.rmtree(SANDBOX, ignore_errors=True)


def quiet(fn, *a, **kw):
    """Call fn with stdout captured; return (result, output)."""
    buf = StringIO()
    with redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


class Case(unittest.TestCase):
    """Each test gets its own directory inside the sandbox HOME."""

    def setUp(self):
        self.d = SANDBOX / f"case-{self.id().rsplit('.', 1)[-1]}"
        self.d.mkdir()
        self.root = self.d / "root"
        self.root.mkdir()
        self.victim = self.d / "victim"
        self.victim.mkdir()
        (self.victim / "precious").write_text("keep me")

    def assertVictimIntact(self):
        self.assertEqual((self.victim / "precious").read_text(), "keep me")

    def remove(self, item, root=None, recheck=None, dry=False):
        return quiet(clean.remove_path, item, root or self.root, recheck, dry)[0]


class PathGuards(Case):
    def test_deletes_a_plain_directory_tree(self):
        (self.root / "app/sub").mkdir(parents=True)
        (self.root / "app/sub/f").write_text("x")
        self.assertTrue(self.remove(path_item(self.root / "app")))
        self.assertFalse((self.root / "app").exists())

    def test_refuses_path_under_a_symlinked_parent(self):
        os.symlink(self.victim, self.root / "evil")
        self.assertFalse(self.remove(path_item(self.root / "evil/precious")))
        self.assertVictimIntact()

    def test_unlinks_a_symlink_without_following_it(self):
        os.symlink(self.victim, self.root / "link")
        self.assertTrue(self.remove(path_item(self.root / "link")))
        self.assertFalse(os.path.lexists(self.root / "link"))
        self.assertVictimIntact()

    def test_refuses_a_path_swapped_after_the_scan(self):
        (self.root / "swap").mkdir()
        item = path_item(self.root / "swap")
        (self.root / "swap").rmdir()
        os.symlink(self.victim, self.root / "swap")
        self.assertFalse(self.remove(item))
        self.assertVictimIntact()

    def test_refuses_a_file_replaced_by_another_file(self):
        (self.root / "f").write_text("old")
        item = path_item(self.root / "f")
        (self.root / "f").unlink()
        (self.root / "f").write_text("new, different inode")
        self.assertIn("changed since the scan", clean.check_path(item, self.root))

    def test_refuses_paths_outside_the_root(self):
        self.assertFalse(self.remove(path_item(self.victim)))
        self.assertVictimIntact()

    def test_refuses_the_root_itself(self):
        self.assertIsNotNone(clean.check_path(path_item(self.root), self.root))

    def test_refuses_dotdot_paths(self):
        item = Item(str(self.root / ".." / "victim"), "x")
        self.assertIsNotNone(clean.check_path(item, self.root))

    def test_refuses_relative_paths(self):
        self.assertIsNotNone(clean.check_path(Item("victim/precious", "x"), self.root))

    def test_refuses_protected_dirs_even_when_inside_the_root(self):
        for p in (HOME / ".config", HOME / ".ssh", HOME / ".local/share", HOME / "Work"):
            with self.subTest(p=p):
                p.mkdir(parents=True, exist_ok=True)   # must exist, or "already gone" masks the check
                self.assertIn("protected", clean.check_path(path_item(p), HOME) or "")

    def test_reports_already_gone(self):
        self.assertEqual(clean.check_path(Item(str(self.root / "nope"), "x"), self.root), "already gone")

    def test_dry_run_changes_nothing(self):
        (self.root / "keep").mkdir()
        self.assertTrue(self.remove(path_item(self.root / "keep"), dry=True))
        self.assertTrue((self.root / "keep").is_dir())

    def test_recheck_veto_blocks_removal(self):
        (self.root / "x").mkdir()
        self.assertFalse(self.remove(path_item(self.root / "x"), recheck=lambda p: "vetoed"))
        self.assertTrue((self.root / "x").exists())

    @unittest.skipUnless(shutil.which("gio"), "gio not installed")
    def test_trash_mode_moves_into_the_sandbox_trash(self):
        (self.root / "oldapp").mkdir()
        (self.root / "oldapp/cfg").write_text("cfg")
        self.assertTrue(self.remove(path_item(self.root / "oldapp", mode="trash")))
        self.assertFalse((self.root / "oldapp").exists())
        self.assertTrue((SANDBOX / ".local/share/Trash/files/oldapp/cfg").exists())

    def test_rmtree_is_symlink_attack_resistant_here(self):
        self.assertTrue(shutil.rmtree.avoids_symlink_attacks)


class Log(Case):
    def test_log_is_private(self):
        quiet(clean.log, "hello")
        self.assertEqual(clean.LOG.stat().st_mode & 0o777, 0o600)

    def test_log_refuses_a_planted_symlink(self):
        clean.LOG.parent.mkdir(parents=True, exist_ok=True)
        if clean.LOG.exists() or clean.LOG.is_symlink():
            clean.LOG.unlink()
        os.symlink(self.victim / "precious", clean.LOG)
        try:
            _, out = quiet(clean.log, "should not land in the victim")
            self.assertIn("could not write", out)
            self.assertVictimIntact()
        finally:
            clean.LOG.unlink()


class PickActions(Case):
    def setUp(self):
        super().setUp()
        self.ran = []
        self._orig = clean.run_cmd
        clean.run_cmd = lambda argv, dry: self.ran.append(argv) or True

    def tearDown(self):
        clean.run_cmd = self._orig

    def test_package_names_are_validated_before_sudo(self):
        a = Action("t", "t", "t", "pick", ["sudo", "pacman", "-Rns"],
                   items=[Item("yazi", "yazi"), Item("foo; rm -rf ~", "evil"), Item("-Rdd", "flag")])
        quiet(clean.do_action, a, True)
        self.assertEqual(self.ran, [["sudo", "pacman", "-Rns", "yazi"]])

    def test_each_runs_once_per_item(self):
        a = Action("t", "t", "t", "pick", ["pipx", "uninstall"], each=True,
                   items=[Item("a", "a"), Item("b", "b")])
        quiet(clean.do_action, a, True)
        self.assertEqual(self.ran, [["pipx", "uninstall", "a"], ["pipx", "uninstall", "b"]])

    def test_custom_validator(self):
        a = Action("t", "t", "t", "pick", ["hf", "cache", "rm"],
                   valid=r"(model|dataset|space)/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?",
                   items=[Item("model/Org/Name", "ok"), Item("model/../../etc", "bad")])
        quiet(clean.do_action, a, True)
        self.assertEqual(self.ran, [["hf", "cache", "rm", "model/Org/Name"]])


class Prompts(unittest.TestCase):
    def test_text_picker_parses_numbers_and_ranges(self):
        import builtins
        items = [Item(str(i), f"item{i}") for i in range(1, 7)]
        orig_have, orig_input = clean.have, builtins.input
        clean.have, builtins.input = (lambda c: False), (lambda prompt="": "1 3-4, 9")
        try:
            chosen, _ = quiet(clean.pick, "hdr", items, False)
        finally:
            clean.have, builtins.input = orig_have, orig_input
        self.assertEqual([i.value for i in chosen], ["1", "3", "4"])   # 9 is out of range

    def test_text_picker_empty_answer_selects_nothing(self):
        import builtins
        orig_have, orig_input = clean.have, builtins.input
        clean.have, builtins.input = (lambda c: False), (lambda prompt="": "")
        try:
            chosen, _ = quiet(clean.pick, "hdr", [Item("a", "a")], False)
        finally:
            clean.have, builtins.input = orig_have, orig_input
        self.assertEqual(chosen, [])


class Dedupe(Case):
    def test_a_path_is_offered_only_once_across_steps(self):
        (self.root / "shared").mkdir()
        (self.root / "only2").mkdir()
        a1 = Action("caches.x", "one", "w", "paths", root=self.root, items=[path_item(self.root / "shared")])
        a2 = Action("leftovers.y", "two", "w", "paths", root=self.root,
                    items=[path_item(self.root / "shared"), path_item(self.root / "only2")])

        class R:
            actions = [a1, a2]
        quiet(clean.run, [R()], dry=True)
        self.assertEqual([i.value for i in a2.items], [str(self.root / "only2")])
        self.assertTrue((self.root / "shared").exists())   # dry run


class CriticalPackages(unittest.TestCase):
    def test_boot_and_update_critical_packages_are_protected(self):
        for n in ("linux", "linux-headers", "linux-lts", "linux-zen-headers", "amd-ucode", "intel-ucode",
                  "nvidia-open", "nvidia-utils", "linux-firmware", "efibootmgr", "limine", "grub",
                  "mkinitcpio", "base", "sudo", "pacman", "pacman-contrib", "yay", "systemd",
                  "networkmanager", "omarchy", "omarchy-keyring", "archlinux-keyring"):
            with self.subTest(n=n):
                self.assertTrue(CRITICAL.fullmatch(n))

    def test_ordinary_packages_are_not(self):
        for n in ("yazi", "gimp", "steam", "ollama", "linuxdoc", "nano", "uv"):
            with self.subTest(n=n):
                self.assertFalse(CRITICAL.fullmatch(n))


class Helpers(unittest.TestCase):
    def test_parse_hf_sizes(self):
        self.assertEqual(_parse_size("22.4G"), int(22.4 * 1024 ** 3))
        self.assertEqual(_parse_size("170.1M"), int(170.1 * 1024 ** 2))
        self.assertEqual(_parse_size(4096), 4096)
        self.assertIsNone(_parse_size("n/a"))

    def test_remote_urls_lose_credentials(self):
        self.assertEqual(clones._clean_url("https://user:ghp_secret@github.com/a/b.git"), "gh:a/b")
        self.assertEqual(clones._clean_url("https://tok@example.com/x.git"), "https://example.com/x")
        self.assertNotIn("secret", clones._clean_url("https://x:secret@gitlab.com/a/b"))

    def test_synced_folders_are_never_disposable(self):
        self.assertIn("synced copy", clones.managed_by(HOME / "Dropbox/stuff/repo"))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitClones(Case):
    def git(self, *a, cwd=None):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
                               "-C", str(cwd or self.repo), *a], capture_output=True, text=True, check=True)

    def setUp(self):
        super().setUp()
        remote = self.d / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "a").write_text("1")
        self.git("add", "a")
        self.git("commit", "-qm", "x")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "origin", "HEAD")
        self.git("fetch", "-q", "origin")

    def test_clean_pushed_clone_is_disposable(self):
        self.assertIsNone(clones._still_disposable(str(self.repo)))

    def test_new_uncommitted_file_blocks_deletion(self):
        (self.repo / "wip").write_text("work")
        self.assertEqual(clones._still_disposable(str(self.repo)), "now has local work")

    def test_unpushed_commit_blocks_deletion(self):
        (self.repo / "b").write_text("2")
        self.git("add", "b")
        self.git("commit", "-qm", "local only")
        self.assertEqual(clones._still_disposable(str(self.repo)), "now has local work")

    def test_stash_blocks_deletion(self):
        (self.repo / "a").write_text("changed")
        self.git("stash", "-q")
        self.assertEqual(clones._still_disposable(str(self.repo)), "now has local work")

    def test_inspecting_a_repo_does_not_touch_its_index(self):
        """Regression: plain `git status` rewrote .git/index and faked 'last used'."""
        idx = self.repo / ".git/index"
        old = time.time() - 400 * 86400
        os.utime(idx, (old, old))
        (self.repo / "a").touch()   # stat change git status would want to refresh
        clones.inspect(self.repo)
        self.assertAlmostEqual(idx.stat().st_mtime, old, delta=1)

    def test_recheck_wired_into_removal(self):
        (self.repo / "wip").write_text("work")
        ok = self.remove(path_item(self.repo), recheck=clones._still_disposable)
        self.assertFalse(ok)
        self.assertTrue((self.repo / "wip").exists())


if __name__ == "__main__":
    unittest.main()
