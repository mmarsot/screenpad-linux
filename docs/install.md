# Install guide

From a fresh checkout to a lit ScreenPad with the touch bar, then updating and removing it. Every command is meant to be copied as is. Commands with `sudo` need root; the touch bar commands run as your own user, never with `sudo`.

Contents:
- [Prerequisites](#prerequisites)
- Install: [1. Get the code](#1-get-the-code) · [2. Look first](#2-look-before-touching-anything) · [3. Get the EDID](#3-get-the-edid) · [4. First test](#4-first-test-light-the-panel) · [5. Install](#5-install-and-make-it-permanent) · [6. Touch bar](#6-turn-on-the-touch-bar) · [7. Verify](#7-verify)
- [Configuration](#configuration) · [Updating](#updating) · [Rollback and uninstall](#rollback-and-uninstall)

## Prerequisites

| What | Needed | Check |
|---|---|---|
| Laptop | ASUS ZenBook **UX480FD** (first-generation ScreenPad, GeForce GTX 1050 Max-Q). Both tools refuse any other DMI product name | `cat /sys/class/dmi/id/product_name` prints `ZenBook UX450FDX_UX480FD` |
| OS | Ubuntu 26.04 LTS was tested (kernel 7.0). Other recent Ubuntu releases may work but are untested | `lsb_release -ds; uname -r` |
| Desktop | GNOME Shell **50** on Wayland, for the touch bar (the extension declares GNOME 50 only). The display part works with any compositor | `gnome-shell --version; echo $XDG_SESSION_TYPE` |
| GPU driver | The NVIDIA proprietary driver with nvidia-drm, as Ubuntu installs it (tested: 580.178.04) | `cat /proc/driver/nvidia/version` |
| ScreenPad backlight | The in-kernel `asus_wmi` driver, loaded by default | `ls /sys/class/backlight/asus_screenpad` |
| Tools | `python3` 3.11 or newer, `git`, `curl` | `python3 --version` |
| Touch bar input access | Your user in group `input`, so the touch bar daemon can read the touchpad (members of `input` can read every input device, keyboards included) | `id -nG \| grep -w input` |

To join group `input`:

```sh
sudo usermod -aG input "$USER"
```

Then log out and in once (or do it later together with the touch bar's re-login in step 6).

**Before you start:** save your work. When the ScreenPad appears as a new monitor, GNOME may rearrange your displays. Everything up to step 5 is undone by a reboot, except the boot parameter, which `sudo ./screenpad.py grub remove` undoes.

## 1. Get the code

```sh
git clone https://github.com/mmarsot/screenpad-linux.git
cd screenpad-linux
```

Steps 2 to 4 run the tool straight from the checkout (`./screenpad.py`), before anything is installed.

## 2. Look before touching anything

```sh
sudo ./screenpad.py status
```

It writes nothing (root is only needed to see debugfs on a kernel that is not locked down). On the tested machine with Secure Boot on, it shows:

- `card: card0 (0000:02:00.0)` (the card number may differ)
- `connector: card0-HDMI-A-2 status=disconnected … edid=0 bytes`
- `ddc buses: i2c-9 i2c-10`
- `lockdown: integrity; using drm.edid_firmware`
- `boot param: none on the kernel command line`

If it refuses with `unsupported machine`, your laptop is not a UX480FD; see [Supported hardware](../README.md#supported-hardware-and-what-was-tested).

## 3. Get the EDID

The panel's bridge reports an EDID with a broken checksum, so the tool needs a fixed copy (see [How it works](display.md#how-it-works)). Reading it from the panel over DDC failed with `Input/output error` on the tested unit, so use the UX580 dump published by the [GlassPad](https://github.com/kan0p/asus-screenpad-linux) project. It matched the tested UX480FD:

```sh
curl -LO https://github.com/kan0p/asus-screenpad-linux/raw/HEAD/edid/ux580-tsb-fixed.bin
sudo ./screenpad.py capture-edid --from ux580-tsb-fixed.bin
```

Expect `EDID from ux580-tsb-fixed.bin: TSB 8888, 2 block(s), 1080x1920 @ 60.70 Hz, 140.00 MHz, HDMI VSDB, checksums ok`, and that it saved `/var/lib/screenpad/edid.bin` and `/lib/firmware/screenpad/edid.bin`.

To try reading it from your own panel instead (it retries for about 9 s), run `sudo ./screenpad.py capture-edid` without `--from`. If that fails, use the file above.

## 4. First test: light the panel

```sh
sudo ./screenpad.py on
```

With Secure Boot on, expect these lines:

- `kernel lockdown (integrity): debugfs is closed, using drm.edid_firmware`
- a note that the parameter was set for this boot only
- `card0-HDMI-A-2 forced on and lit; status connected`

There is about a second of dark before the picture. Within a few seconds a new 1080x1920 monitor appears in **Settings > Displays**. Place and rotate it as described in [GNOME placement](display.md#gnome-placement): the ScreenPad sits below the laptop screen, rotated so the picture reads upright (Portrait Left or Right).

If it says `has no EDID` and `The boot parameter is not applied yet`, apply the boot parameter now (`sudo ./screenpad.py grub apply`, explained in step 5), reboot, and run `sudo ./screenpad.py on` again. If the panel stays dark, see [If the panel stays dark](display.md#if-the-panel-stays-dark).

To undo the test: `sudo ./screenpad.py off`, or reboot.

## 5. Install and make it permanent

```sh
sudo ./install.sh
sudo systemctl start screenpad
```

`install.sh` copies the tools and units into place and enables the boot unit; it does not start it and never edits GRUB. It installs:

| File | Purpose |
|---|---|
| `/usr/local/sbin/screenpad` | the display CLI (`screenpad.py`) |
| `/etc/systemd/system/screenpad.service` | runs `screenpad on --wait 30` at boot, after the display manager; `systemctl stop screenpad` runs `screenpad off` |
| `/usr/lib/systemd/system-sleep/screenpad` | relights the panel after resume (`screenpad replug --if-active`) |
| `/var/lib/screenpad/`, `/lib/firmware/screenpad/edid.bin` | the saved, fixed EDID and the copy the kernel loads |
| `/etc/initramfs-tools/hooks/screenpad` | only if nvidia-drm is in your initramfs (it is not on stock Ubuntu); followed by `update-initramfs -u` |
| `/usr/local/bin/screenpad-touchbar` | the touch bar daemon and CLI (`touchbar.py`) |
| `/usr/lib/systemd/user/screenpad-touchbar.service` | the touch bar's systemd user unit, installed but not enabled |
| `/usr/local/share/gnome-shell/extensions/screenpad-touchbar@screenpad/` | the GNOME Shell extension that draws the bar |
| `/etc/udev/rules.d/70-screenpad-uinput.rules` | lets the user at the seat use `/dev/uinput` for the touch bar's virtual touchpad and keys |
| `/usr/local/share/doc/screenpad/` | this documentation |

**With Secure Boot on** (`install.sh` says so when the kernel is in lockdown), add the boot parameter so the kernel loads the EDID from the first probe:

```sh
screenpad grub
sudo screenpad grub apply
```

The first command prints the exact change to `/etc/default/grub`. The second makes it, keeps the original as `/etc/default/grub.screenpad-backup`, and runs `update-grub`. Both are explained in [The GRUB change, exactly](display.md#the-grub-change-exactly). Then reboot:

```sh
sudo reboot
```

After the reboot, the panel lights by itself about when you log in, after a second of dark. `screenpad status` should now show `boot param: drm.edid_firmware=HDMI-A-2:screenpad/edid.bin on the kernel command line`.

Without Secure Boot, skip the boot parameter: `screenpad on` uses debugfs instead.

## 6. Turn on the touch bar

As your user, not root:

```sh
screenpad-touchbar on
```

It writes the default config to `~/.config/screenpad/touchbar.toml`, enables the GNOME Shell extension, and enables and starts the user unit. **Log out and in once**: GNOME loads a newly installed extension only at login.

After logging in, check the extension:

```sh
gnome-extensions info screenpad-touchbar@screenpad
```

It should show `State: ACTIVE`.

## 7. Verify

```sh
screenpad status
screenpad-touchbar status
```

`screenpad-touchbar status` should show the following:

- `touchpad: /dev/input/eventN`, with no `not readable`
- `screenpad: on`
- `uinput: writable`
- `unit: screenpad-touchbar.service active`
- `daemon: pid …, touchpad grabbed`

Then, on the ScreenPad:

1. A dark bar with three buttons, three empty shortcut spots (`+`) and a grip is drawn along its top edge (the edge nearest the screen hinge).
2. Touching or sliding inside the bar never moves the pointer. Below it, the panel works as a touchpad, following GNOME's touchpad settings.
3. Hold the microphone button: it turns red while held.
4. Drag an app icon from the dock onto a `+` spot: the app is added. Tap it to open the app.
5. Drag the grip down and up: the bar grows and shrinks.
6. `screenpad-touchbar off`: the bar disappears and the whole panel is a touchpad again. `screenpad-touchbar on` brings the bar back.

To check that touches land where the bar is drawn, `screenpad-touchbar probe` prints the position and spot of every touch without grabbing the pad (Ctrl+C to stop).

If something is off, see [Troubleshooting](touchbar.md#troubleshooting) for the touch bar or [If the panel stays dark](display.md#if-the-panel-stays-dark) for the display.

## Configuration

The bar's height, buttons and shortcuts are in `~/.config/screenpad/touchbar.toml`. Changes apply within a second of saving. See [configuration.md](configuration.md), including how to use the Push to talk button with your voice app.

## Updating

From your checkout:

```sh
git pull
sudo ./install.sh
systemctl --user restart screenpad-touchbar
```

- `install.sh` replaces the installed files; it keeps your saved EDID, your boot parameter and your touch bar config.
- If the update changed the GNOME Shell extension, log out and in once. This command lists the extension files the update changed; if it prints nothing, no re-login is needed:

  ```sh
  git diff --stat ORIG_HEAD HEAD -- gnome-shell
  ```

- A display fix takes effect at the next boot. To apply it now, run `sudo systemctl restart screenpad`. The panel goes dark for a moment.

## Rollback and uninstall

**Turn things off without uninstalling:**

```sh
screenpad-touchbar off
sudo screenpad off
```

The first command gives the panel back as a plain touchpad, at once. The second turns the ScreenPad display off until the next boot. To keep it off at boot too: `sudo systemctl disable screenpad`.

**Uninstall:**

1. Remove the boot parameter first, if you applied it. `install.sh` refuses to uninstall while GRUB still loads the EDID file:

   ```sh
   sudo screenpad grub remove
   ```

2. Turn off the touch bar, as your user:

   ```sh
   screenpad-touchbar off
   ```

3. Uninstall, from the checkout:

   ```sh
   sudo ./install.sh uninstall
   ```

   This stops the unit (which turns the ScreenPad off), then removes every installed file, including the touch bar's. It keeps the saved EDID in `/var/lib/screenpad`.

   To also delete the saved EDID, run `purge` instead:

   ```sh
   sudo ./install.sh purge
   ```

4. Reboot, so the kernel drops the boot parameter and its runtime EDID setting:

   ```sh
   sudo reboot
   ```

5. Optionally, remove your touch bar config:

   ```sh
   rm -r ~/.config/screenpad
   ```

   GNOME keeps the ScreenPad's monitor layout in `~/.config/monitors.xml`, which is harmless once the monitor is gone.

If a boot goes wrong while the boot parameter is applied, choose a **recovery** entry in the GRUB menu (hold Shift or press Esc at power-on): it boots without the parameter. To skip the boot unit for one boot, add `systemd.mask=screenpad.service` to the kernel line from the GRUB menu (`e`, then Ctrl+X). The [display reference](display.md#rolling-back-by-hand) has the manual rollback commands, for when the CLI itself is the problem.
