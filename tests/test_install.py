"""Tests for install.sh against a fake root (DESTDIR) with stub commands first
on PATH. Skipped when run as root.

Run with: python3 -m unittest discover -s tests"""

import os
import shutil
import subprocess
import sys
import unittest

from screenpad_support import PARAM, REPO, UBUNTU_GRUB, Base, repo_file, tsb_edid
from touchbar_support import js


@unittest.skipIf(os.geteuid() == 0, "runs install.sh, so never as root")
class InstallTests(Base):
    STUBS = ("id", "systemctl", "update-initramfs", "lsinitramfs", "udevadm")
    TOUCHBAR_FILES = (
        "usr/local/bin/screenpad-touchbar",
        "usr/lib/systemd/user/screenpad-touchbar.service",
        "usr/local/share/gnome-shell/extensions/screenpad-touchbar@screenpad",
        "etc/udev/rules.d/70-screenpad-uinput.rules",
        "etc/udev/rules.d/70-screenpad-backlight.rules",
    )
    # Where earlier versions installed the agent app, which now has its own
    # repository and installer.
    OLD_AGENT_APP = (
        "usr/local/bin/screenpad-agents",
        "usr/lib/systemd/user/screenpad-agents.service",
        "usr/local/lib/screenpad/agents.py",
        "usr/local/lib/screenpad/screenpad_agents",
    )

    def setUp(self):
        super().setUp()
        self.s = self.make()
        self.bin = os.path.join(self.tmp.name, "stub-bin")
        self.calls = os.path.join(self.tmp.name, "calls")
        os.makedirs(self.bin)
        for name in self.STUBS:
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write('#!/bin/sh\necho "%s $*" >> "$STUB_CALLS"\n' % name)
                if name == "id":
                    f.write("echo 0\n")
                if name == "lsinitramfs":
                    f.write('cat "$STUB_INITRAMFS" 2>/dev/null\n')
            os.chmod(path, 0o755)
        self.path = self.bin + os.pathsep + os.environ.get("PATH", "")
        for name in self.STUBS:
            # Fail closed: the real commands must never run.
            self.assertEqual(shutil.which(name, path=self.path), os.path.join(self.bin, name))

    def install(self, *argv, initramfs=""):
        listing = os.path.join(self.tmp.name, "initramfs.txt")
        with open(listing, "w") as f:
            f.write(initramfs)
        env = dict(os.environ, DESTDIR=self.s.root, PATH=self.path, STUB_CALLS=self.calls, STUB_INITRAMFS=listing)
        return subprocess.run(
            ["sh", os.path.join(REPO, "install.sh")] + list(argv), env=env, capture_output=True, text=True
        )

    def called(self):
        try:
            with open(self.calls) as f:
                return [line.split()[0:2] for line in f if not line.startswith("id ")]
        except FileNotFoundError:
            return []

    def saved(self):
        os.makedirs(self.s.p("var/lib/screenpad"))
        with open(self.s.p("var/lib/screenpad/edid.bin"), "wb") as f:
            f.write(tsb_edid())

    def resume(self, active):
        """Run the installed resume hook with 'post', systemd-run stubbed; its calls."""
        flag = os.path.join(self.tmp.name, "active")
        if active:
            open(flag, "w").close()
        runs = os.path.join(self.tmp.name, "systemd-run.calls")
        stub = os.path.join(self.bin, "systemd-run")
        with open(stub, "w") as f:
            f.write('#!/bin/sh\necho "$*" >> "%s"\n' % runs)
        os.chmod(stub, 0o755)
        env = dict(os.environ, PATH=self.path, SCREENPAD_ACTIVE_FLAG=flag)
        r = subprocess.run(
            ["sh", self.s.p("usr/lib/systemd/system-sleep/screenpad"), "post"], env=env, capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        try:
            with open(runs) as f:
                return [line.split() for line in f]
        except FileNotFoundError:
            return []

    def test_resume_keeps_a_darkened_display_dark(self):
        # docs/install.md: after resume the hook queues 'screenpad replug
        # --if-active --keep-dark', so a display Fn+F6 turned off stays dark.
        self.saved()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.resume(active=False), [])
        (argv,) = self.resume(active=True)
        self.assertIn("--property=After=nvidia-resume.service", argv)
        tail = argv[argv.index("/usr/local/sbin/screenpad") :]
        self.assertEqual(tail[:4], ["/usr/local/sbin/screenpad", "replug", "--if-active", "--keep-dark"])

    def test_install_copies_the_edid_to_firmware(self):
        self.saved()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.s.read("lib/firmware/screenpad/edid.bin"), tsb_edid())
        self.assertEqual(self.s.read("usr/local/sbin/screenpad"), repo_file("screenpad.py"))
        for rel in (
            "etc/systemd/system/screenpad.service",
            "usr/lib/systemd/system-sleep/screenpad",
            "usr/local/share/doc/screenpad/README.md",
            "usr/local/share/doc/screenpad/docs/install.md",
            "usr/local/share/doc/screenpad/docs/touchbar.md",
        ):
            self.assertTrue(os.path.exists(self.s.p(rel)), rel)
        self.assertFalse(os.path.exists(self.s.p("etc/initramfs-tools/hooks/screenpad")))
        self.assertEqual(
            self.called(),
            [
                ["udevadm", "control"],
                ["udevadm", "trigger"],
                ["udevadm", "trigger"],
                ["lsinitramfs", self.s.p("boot/initrd.img-%s" % os.uname().release)],
                ["systemctl", "daemon-reload"],
                ["systemctl", "enable"],
            ],
        )
        self.assertNotIn("lockdown", r.stdout)
        self.assertEqual(self.s.read("etc/default/grub").decode(), UBUNTU_GRUB)

    def test_install_touchbar(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.s.read("usr/local/bin/screenpad-touchbar"), repo_file("touchbar.py"))
        self.assertTrue(os.access(self.s.p("usr/local/bin/screenpad-touchbar"), os.X_OK))
        self.assertEqual(
            self.s.read("usr/lib/systemd/user/screenpad-touchbar.service"),
            repo_file("systemd/screenpad-touchbar.service"),
        )
        self.assertEqual(
            self.s.read("etc/udev/rules.d/70-screenpad-uinput.rules"), repo_file("udev/70-screenpad-uinput.rules")
        )
        self.assertEqual(
            self.s.read("etc/udev/rules.d/70-screenpad-backlight.rules"),
            repo_file("udev/70-screenpad-backlight.rules"),
        )
        with open(self.calls) as f:
            self.assertIn("udevadm trigger --subsystem-match=backlight --sysname-match=asus_screenpad\n", f.read())
        ext = "gnome-shell/screenpad-touchbar@screenpad"
        for name in os.listdir(os.path.join(REPO, ext)):
            self.assertEqual(
                self.s.read("usr/local/share/gnome-shell/extensions/screenpad-touchbar@screenpad/%s" % name),
                repo_file("%s/%s" % (ext, name)),
            )
        self.assertIn("screenpad-touchbar on", r.stdout)
        # The touch bar is the user's to turn on; install never enables it.
        self.assertNotIn(["systemctl", "--user"], self.called())

    def old_agent_app(self):
        """The agent app as an earlier version installed it."""
        self.s.write("usr/lib/systemd/user/screenpad-agents.service", "[Service]\n")
        self.s.write("usr/local/lib/screenpad/agents.py", "old\n")
        self.s.write("usr/local/lib/screenpad/screenpad_agents/app.py", "old\n")
        os.makedirs(self.s.p("usr/local/bin"), exist_ok=True)
        os.symlink("../lib/screenpad/agents.py", self.s.p("usr/local/bin/screenpad-agents"))

    def test_install_has_no_agent_app(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in self.OLD_AGENT_APP:
            self.assertFalse(os.path.lexists(self.s.p(rel)), rel)
        self.assertNotIn("agent", r.stdout)

    def test_install_leaves_the_old_agent_app_and_says_how_to_remove_it(self):
        self.old_agent_app()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        # Left as it was: it may be running, and nothing stops or disables it.
        self.assertEqual(os.readlink(self.s.p("usr/local/bin/screenpad-agents")), "../lib/screenpad/agents.py")
        self.assertEqual(self.s.read("usr/local/lib/screenpad/agents.py"), b"old\n")
        self.assertEqual(self.s.read("usr/local/lib/screenpad/screenpad_agents/app.py"), b"old\n")
        self.assertEqual(self.s.read("usr/lib/systemd/user/screenpad-agents.service"), b"[Service]\n")
        self.assertNotIn("screenpad-agents", " ".join(" ".join(c) for c in self.called()))
        lines = r.stdout.splitlines()
        self.assertIn(
            "The agent app moved to https://github.com/mmarsot/screenpad-agents and installs per user;", lines
        )
        self.assertIn("  systemctl --user disable --now screenpad-agents", lines)
        self.assertIn("  sudo rm -rf " + " ".join("/" + rel for rel in self.OLD_AGENT_APP), lines)
        self.assertIn("./install.sh: https://github.com/mmarsot/screenpad-agents#install", r.stdout)
        # The printed command removes exactly the old app and nothing the
        # install put there.
        rm = next(line for line in lines if line.startswith("  sudo rm -rf "))
        for path in rm.split()[3:]:
            target = self.s.p(path.lstrip("/"))
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        for rel in self.OLD_AGENT_APP:
            self.assertFalse(os.path.lexists(self.s.p(rel)), rel)
        for rel in ("usr/local/bin/screenpad-touchbar", "usr/local/lib/screenpad/screenpad_app/client.py"):
            self.assertTrue(os.path.exists(self.s.p(rel)), rel)

    def test_install_names_only_the_old_files_still_there(self):
        # A link left dangling still counts; files already removed are not named.
        os.makedirs(self.s.p("usr/local/bin"))
        os.symlink("../lib/screenpad/agents.py", self.s.p("usr/local/bin/screenpad-agents"))
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("  sudo rm -rf /usr/local/bin/screenpad-agents\n", r.stdout)
        self.assertTrue(os.path.islink(self.s.p("usr/local/bin/screenpad-agents")))

    def test_uninstall_removes_the_old_agent_app(self):
        self.old_agent_app()
        self.assertEqual(self.install().returncode, 0)
        r = self.install("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in self.OLD_AGENT_APP:
            self.assertFalse(os.path.lexists(self.s.p(rel)), rel)
        self.assertIn("The agent app an earlier version installed was removed too.", r.stdout)
        self.assertIn("systemctl --user disable --now screenpad-agents", r.stdout)
        # Without it, uninstall does not mention it.
        self.assertEqual(self.install().returncode, 0)
        r = self.install("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("agent", r.stdout)

    def test_install_programs(self):
        # An earlier version installed the programs as single files, and a
        # module it had must not linger in the package.
        self.s.write("usr/local/bin/screenpad-touchbar", "old single file\n")
        self.s.write("usr/local/lib/screenpad/screenpad_touchbar/gone.py", "")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        lib = "usr/local/lib/screenpad"
        for link, script in (
            ("usr/local/sbin/screenpad", "screenpad.py"),
            ("usr/local/bin/screenpad-touchbar", "touchbar.py"),
        ):
            self.assertEqual(os.readlink(self.s.p(link)), "../lib/screenpad/" + script)
            self.assertEqual(self.s.read("%s/%s" % (lib, script)), repo_file(script))
            self.assertTrue(os.access(self.s.p(lib, script), os.X_OK))
        for pkg in ("screenpad_display", "screenpad_touchbar", "screenpad_app"):
            modules = sorted(n for n in os.listdir(os.path.join(REPO, pkg)) if n.endswith(".py"))
            self.assertEqual(sorted(os.listdir(self.s.p(lib, pkg))), modules)
            for name in modules:
                self.assertEqual(self.s.read("%s/%s/%s" % (lib, pkg, name)), repo_file("%s/%s" % (pkg, name)))
        # The installed commands find their package from where they are
        # installed, in isolated mode, run from anywhere.
        for link, prog in (
            ("usr/local/sbin/screenpad", "screenpad"),
            ("usr/local/bin/screenpad-touchbar", "screenpad-touchbar"),
        ):
            run = subprocess.run(
                [sys.executable, "-I", self.s.p(link), "--help"], cwd=self.tmp.name, capture_output=True, text=True
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(run.stdout.startswith("usage: %s " % prog), run.stdout)

    def test_extension_fallback_command_is_installed(self):
        # The extension adds dropped apps with CLI_FALLBACK when the command
        # is not on its PATH, so the install must put the command there.
        cli, fallback = js("contract.js", "[m.CLI, m.CLI_FALLBACK]")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        path = self.s.p(fallback.lstrip("/"))
        self.assertTrue(os.access(path, os.X_OK), fallback)
        run = subprocess.run([sys.executable, "-I", path, "--help"], cwd=self.tmp.name, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.stdout.startswith("usage: %s " % cli), run.stdout)

    def test_install_without_saved_edid(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No saved EDID yet", r.stdout)
        self.assertFalse(os.path.exists(self.s.p("lib/firmware/screenpad/edid.bin")))

    def test_initramfs_only_when_nvidia_drm_is_in_it(self):
        self.saved()
        r = self.install(initramfs="usr/lib/modules/7.0.0-38-generic/kernel/nvidia-580/nvidia-drm.ko.zst\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.s.read("etc/initramfs-tools/hooks/screenpad"), repo_file("initramfs/screenpad"))
        self.assertIn(["update-initramfs", "-u"], self.called())

        r = self.install("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.s.p("etc/initramfs-tools/hooks/screenpad")))
        self.assertEqual(self.called().count(["update-initramfs", "-u"]), 2)

    def test_lockdown_hint(self):
        self.s.write("sys/kernel/security/lockdown", "none [integrity] confidentiality\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("screenpad grub show", r.stdout)

    def test_uninstall_refuses_while_grub_loads_the_edid(self):
        self.saved()
        self.assertEqual(self.install().returncode, 0)
        self.s.write("etc/default/grub", UBUNTU_GRUB.replace('"quiet splash"', '"quiet splash %s"' % PARAM))
        r = self.install("uninstall")
        self.assertEqual(r.returncode, 1)
        self.assertIn("screenpad grub remove", r.stderr)
        self.assertTrue(os.path.exists(self.s.p("usr/local/sbin/screenpad")))

    def test_uninstall_and_purge(self):
        self.saved()
        self.assertEqual(self.install().returncode, 0)
        r = self.install("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in (
            "usr/local/sbin/screenpad",
            "etc/systemd/system/screenpad.service",
            "usr/lib/systemd/system-sleep/screenpad",
            "usr/local/share/doc/screenpad",
            "lib/firmware/screenpad",
            "usr/local/lib/screenpad",
        ) + self.TOUCHBAR_FILES:
            self.assertFalse(os.path.exists(self.s.p(rel)), rel)
        self.assertTrue(os.path.exists(self.s.p("var/lib/screenpad/edid.bin")))
        self.assertEqual(self.install("purge").returncode, 0)
        self.assertFalse(os.path.exists(self.s.p("var/lib/screenpad")))

    def test_refuses_other_machine(self):
        self.s.write("sys/class/dmi/id/product_name", "ZenBook UX425EA")
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("unsupported machine", r.stderr)
        self.assertFalse(os.path.exists(self.s.p("usr/local/sbin/screenpad")))


if __name__ == "__main__":
    unittest.main()
