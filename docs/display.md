# Display bring-up: how it works and reference

How `screenpad` lights the ScreenPad, every command, the Secure Boot (kernel lockdown) path, and the manual rollback. For installing, see the [install guide](install.md).

Contents: [How it works](#how-it-works) · [Commands](#commands) · [Secure Boot](#secure-boot-kernel-lockdown) · [If the panel stays dark](#if-the-panel-stays-dark) · [Rolling back by hand](#rolling-back-by-hand) · [GNOME placement](#gnome-placement)

## How it works

On the UX480FD the ScreenPad is an HDMI sink on the GTX 1050 Max-Q (`card0-HDMI-A-2` at the time of writing), behind a Toshiba HDMI-to-MIPI bridge. Linux leaves it black for two reasons:

1. The panel boots dark. The in-kernel `asus_wmi` driver already exposes `/sys/class/backlight/asus_screenpad`, and writing it issues the ASUS WMI `SCREENPAD_POWER` / `SCREENPAD_LIGHT` calls, but on this unit they do the opposite of what their names say. The panel shows a picture after asus-wmi sends `SCREENPAD_POWER=0`, and goes dark after any `SCREENPAD_LIGHT` write, which also makes the firmware report the panel as powered. asus-wmi never sends `SCREENPAD_POWER=1`. It chooses its call by comparing the new `bl_power` with the power it reads back from the firmware (`update_screenpad_bl_status()` in `drivers/platform/x86/asus-wmi.c`): it sends `POWER=0` only when `bl_power` becomes 4 while the firmware reports powered, `LIGHT` whenever `bl_power` is 0, and nothing when `bl_power` is 4 and the firmware reports unpowered. The backlight core calls it on every `brightness` write, but on a `bl_power` write only when the value changes. So lighting the panel means `bl_power` 0, then 4 about a second later (`echo 4` alone does it when `bl_power` reads 0). Any later `brightness` write or `bl_power` 0 darkens it again. Nothing times out: the panel stays lit until the next `LIGHT` write.
2. The bridge's EDID has a wrong checksum on its CTA extension block (on the UX580 sister model, `0x3e` where `0x6f` is correct). The NVIDIA driver rejects the whole EDID and reports the output as disconnected. The bridge needs that original CTA/HDMI block, so a hand-written EDID does not work.

`screenpad on` therefore:

1. checks the DMI vendor and product and refuses on any other machine;
2. waits for the nvidia-drm card, its HDMI connector, the backlight and (unless the kernel is in lockdown) the debugfs connector directory, discovering names each time (card numbers change between boots);
3. uses the saved EDID in `/var/lib/screenpad/edid.bin`, or else lights the panel (the bridge probably answers DDC only then), reads it over the NVIDIA DDC buses (the i2c adapters under the NVIDIA PCI device, found by parent device and adapter name), fixes every block checksum, and saves it;
4. hands the EDID to the DRM core, in one of two ways:
   - **kernel not locked down:** writes it to `/sys/kernel/debug/dri/<card>/<connector>/edid_override`, then forces the connector on (`/sys/class/drm/<connector>/status` ← `on`);
   - **kernel in lockdown** (`/sys/kernel/security/lockdown` shows `[integrity]` or `[confidentiality]`, as it does whenever Secure Boot is on): debugfs is closed, so it copies the EDID to `/lib/firmware/screenpad/edid.bin`, makes sure `drm.edid_firmware` maps the connector to that file (setting it for the current boot if the boot parameter is not there yet), re-probes and forces the connector through sysfs (`detect`, then `on`), and checks that the connector now reports an EDID. If the connector has no EDID, it sets the connector back to `detect` and says why.
5. waits until a compositor has enabled the connector (`/sys/class/drm/<connector>/enabled` reads `enabled`), and logs it if that does not happen within `--wait`;
6. lights the panel: `bl_power` 0 plus a rewrite of the current `brightness` (`SCREENPAD_LIGHT`: dark, firmware reports powered, whatever state it was in), 1 s pause, then `bl_power` 4 (`SCREENPAD_POWER=0`: picture). Expect about a second of dark before the picture.

`screenpad off` undoes it: resets the override (when debugfs is usable), drops its entry from the runtime `drm.edid_firmware` value, sets the connector back to `detect`, and darkens the panel (`bl_power` 0 plus a `brightness` rewrite, so that a `SCREENPAD_LIGHT` call goes out).

Nothing persists across a reboot unless the systemd unit is installed or the boot parameter is applied.

## Commands

All of them refuse to run on any machine other than the UX480FD. Everything except `status` and `grub show` needs root.

| Command | What it does |
|---|---|
| `screenpad status` | Shows what it found: backlight, NVIDIA card and connector (with its EDID size), DDC buses, debugfs, lockdown state, `drm.edid_firmware` on the kernel command line and at runtime, saved EDID, `/lib/firmware/screenpad/edid.bin`. Writes nothing. |
| `screenpad capture-edid` | Lights the panel, reads the EDID over DDC (retrying for about 9 s), fixes the checksums, saves `/var/lib/screenpad/edid.bin` and `/lib/firmware/screenpad/edid.bin`. |
| `screenpad capture-edid --from FILE` | Same, but takes the EDID from a file instead of DDC. |
| `screenpad on` | EDID (debugfs override or `drm.edid_firmware`), connector on, wait for a compositor to enable it, light. Captures the EDID first if none is saved. |
| `screenpad off` | The rollback: reset override / runtime `edid_firmware` entry, `detect`, darken. |
| `screenpad replug` | Connector off, 1 s, EDID, connector on, wait for a compositor to enable it, light. Used after resume. `--if-active` makes it do nothing unless `on` ran since boot. |
| `screenpad grub [show]` | Prints the exact `/etc/default/grub` line before and after, and how to apply or remove it. Writes nothing. |
| `screenpad grub apply` | Adds `drm.edid_firmware=HDMI-A-2:screenpad/edid.bin` to `GRUB_CMDLINE_LINUX_DEFAULT`, keeps a copy of the original as `/etc/default/grub.screenpad-backup`, runs `update-grub`. Takes effect at the next boot. |
| `screenpad grub remove` | Deletes exactly that parameter again and runs `update-grub`. |

`on`, `replug` and `capture-edid` take `--wait SECONDS` (default 10), the time to wait for the card and backlight and, for `on` and `replug`, also for the compositor, in all. They also take `--relight-gap SECONDS` (default 1), the pause between the two `bl_power` writes that light the panel. 1 s was proposed, not measured; if the panel stays dark after `on` but lights with a lone `echo 4`, try a longer gap. If the dGPU ever exposes more than one HDMI connector, choose one with `screenpad --connector HDMI-A-2 on`; `grub` uses the same option.

## Secure Boot (kernel lockdown)

### What happened on this unit

The first test on the development UX480FD found two things:

- **DDC does not answer.** `capture-edid` got `[Errno 5] Input/output error` from both NVIDIA DDC buses (`i2c-9`, `i2c-10`). EIO from the NVIDIA i2c adapter means the transfer failed, usually because nothing acknowledged at address `0x50`. That run already waited 2 s after power-on and retried twice, 1 s apart. Whether the bridge needs longer after `SCREENPAD_POWER` cannot be told from here. `capture-edid` now retries for about 9 s after power-on (2 s settle, then up to 8 attempts 1 s apart). That run "powered" the panel with a `SCREENPAD_LIGHT` write, which on this unit leaves it dark (see [How it works](#how-it-works)), so the bridge was probably never on. `capture-edid` now lights the panel before reading, which may make DDC answer; that is untested. **The UX580 EDID from GlassPad is the working source on this unit** (`capture-edid --from ux580-tsb-fixed.bin` succeeded; see the [install guide](install.md#3-get-the-edid)).
- **debugfs is closed.** The machine boots with Secure Boot, so the kernel runs in lockdown mode `[integrity]`. Lockdown refuses to open any writable debugfs file (`fs/debugfs/file.c`, `debugfs_locked_down()`), so `edid_override` fails with `Operation not permitted` and the kernel logs `Lockdown: screenpad.py: debugfs access is restricted`. No userspace tool gets around that.

So with Secure Boot on, the EDID has to come from the kernel's own EDID firmware loader.

### drm.edid_firmware

The DRM core can load an EDID from `/lib/firmware` instead of the display. Checked against the kernel 7.0 source (`drivers/gpu/drm/drm_edid_load.c`, `drm_edid.c`, `drm_connector.c`, `kernel/params.c`):

- **Connector names have no card part.** `drm_edid_load_firmware()` splits the value at commas and compares each `name:file` entry with `connector->name`, using `strncmp` over the length of the given name. A name-less entry is the fallback for all connectors. `connector->name` is `<type>-<n>` (`drm_connector_init()`), with `n` taken from one counter per connector type that every DRM device shares. So the right form is **`drm.edid_firmware=HDMI-A-2:screenpad/edid.bin`**. A card-qualified `card0-HDMI-A-2:…` never matches anything. Because the counter is shared, the Intel and NVIDIA drivers never both have an `HDMI-A-2`. On this boot i915 registered first (7.6 s) and took `HDMI-A-1`, and nvidia-drm followed (9.2 s) with `HDMI-A-2`. Both load from the root file system in parallel through udev, so the number follows whichever registers first. The much larger nvidia stack has lost that race here, but nothing guarantees it. If the names ever swap, a boot parameter naming `HDMI-A-2` would apply to the Intel HDMI port and miss the ScreenPad. `on` repairs that for the running boot: it always writes the runtime value for the connector it discovered and drops any entry that gives `screenpad/edid.bin` to another connector. `status` flags a boot parameter that does not give the NVIDIA connector `screenpad/edid.bin`. If that ever shows up, `sudo screenpad grub remove` and then `grub apply` rewrites the parameter for the current name.
- **The file name is relative to `/lib/firmware`** (it goes to `request_firmware()`), and the EDID must pass the kernel's checksum check, which the fixed EDID does.
- **It is read on every probe**, not once at boot. Each detect of the connector calls `request_firmware()` again, so the file only has to exist by the time the connector is probed after power-on.
- **It can be set at runtime, even under lockdown.** `drm` is built in on Ubuntu, and `/sys/module/drm/parameters/edid_firmware` is mode 0644. Lockdown only blocks writes to hardware parameters (`param_check_unsafe()` checks `KERNEL_PARAM_FL_HWPARAM`), and this is not one. When the boot parameter is missing, `on` writes the runtime value itself, putting `HDMI-A-2:screenpad/edid.bin` first and keeping other entries. That way the [first test](install.md#4-first-test-light-the-panel) works without a reboot. The boot parameter makes the value present from the first probe and independent of this tool. A sysfs write of an empty string is ignored, and a lone newline would make every connector request a firmware named `""`. So `off` writes exactly `,` to clear it, because the kernel skips empty entries.

### Does nvidia-drm 580 use it?

**Yes, according to its source.** The installed driver is 580.178.04, and its nvidia-drm source is published as `kernel-open/nvidia-drm/nvidia-drm-connector.c` at tag `580.178.04` of NVIDIA/open-gpu-kernel-modules (the proprietary package builds the same nvidia-drm source). On every connector detect, `__nv_drm_detect_encoder()` calls the DRM core's `drm_edid_override_connector_update()` on kernels without `connector->override_edid` (that field was removed in 6.2 and is not in 7.0). That function returns the debugfs override if there is one and otherwise `drm_edid_load_firmware()`, that is, `drm.edid_firmware`. nvidia-drm then passes that EDID to NVKMS with `overrideEdid` set, and `forceConnected` follows the sysfs `status` = `on`. The firmware path therefore reaches NVKMS through the same call as the debugfs override, so it should work exactly as well as the debugfs path would have.

What is still unverified on the hardware: whether NVKMS accepts the timing and the bridge lights the panel. The debugfs design had the same open question.

To see it happen, turn on DRM KMS debug for a moment: `echo 0x4 | sudo tee /sys/module/drm/parameters/debug`, run `sudo screenpad on`, then `journalctl -k -b | grep -E 'HDMI-A-2.*(firmware|override)'`. A line `[CONNECTOR:…:HDMI-A-2] adding N modes via fallback override/firmware EDID` means nvidia-drm took it. Turn debug off again with `echo 0 | sudo tee /sys/module/drm/parameters/debug`. A `Requesting EDID firmware "screenpad/edid.bin" failed` error means the file is missing.

### If nvidia-drm does not take it

The signs: `on` reports `has no EDID after forcing it on` while `status` shows the parameter on the kernel command line, the firmware file is present, and no `Requesting EDID firmware … failed` line appears. Then:

1. Roll back: `sudo screenpad off`, then `sudo screenpad grub remove` and reboot.
2. No other path that keeps Secure Boot is known. The remaining route is the debugfs `edid_override` path, which needs a kernel that is not locked down. That is your Secure Boot decision, for example turning Secure Boot off in the firmware setup. This tool never changes Secure Boot. With lockdown off, `on` automatically uses debugfs again.

### Initramfs

Not needed on the tested machine. The EDID only has to be in the initramfs if nvidia-drm probes the connector from there. Ubuntu's prebuilt `linux-modules-nvidia` puts nvidia-drm under `/lib/modules/<kernel>/kernel/nvidia-580/`, and the `framebuffer-nvidia` initramfs hook only copies DKMS builds. On the tested machine the boot log shows nvidia-drm loading after the root file system was mounted. Even if the first probe missed the file, `screenpad on` re-probes once the root file system is up. `install.sh` still checks with `lsinitramfs`: only if `nvidia-drm.ko` is in the current initramfs does it install `/etc/initramfs-tools/hooks/screenpad` (which copies `/lib/firmware/screenpad/edid.bin` in) and run `update-initramfs -u`. In that case, run `sudo update-initramfs -u` again after recapturing the EDID.

### The GRUB change, exactly

`sudo screenpad grub apply` makes this change in `/etc/default/grub` and then runs `sudo update-grub`:

```diff
-GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
+GRUB_CMDLINE_LINUX_DEFAULT="quiet splash drm.edid_firmware=HDMI-A-2:screenpad/edid.bin"
```

`sudo screenpad grub remove` deletes exactly that parameter again and runs `sudo update-grub`. By hand, the same two changes are:

```sh
# apply
sudo sed -i 's|^GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"$|GRUB_CMDLINE_LINUX_DEFAULT="\1 drm.edid_firmware=HDMI-A-2:screenpad/edid.bin"|' /etc/default/grub
sudo update-grub
# remove
sudo sed -i 's| drm.edid_firmware=HDMI-A-2:screenpad/edid.bin||' /etc/default/grub
sudo update-grub
```

`GRUB_CMDLINE_LINUX_DEFAULT` only applies to the normal boot entries, so the **recovery** entries in the GRUB menu boot without the parameter. That is also a rollback if a boot goes wrong. To try the parameter for one boot without editing anything, hold Shift (or press Esc) at power-on to get the GRUB menu, press `e` on the Ubuntu entry, append `drm.edid_firmware=HDMI-A-2:screenpad/edid.bin` to the line starting with `linux`, and press Ctrl+X.

If `/etc/default/grub.d/kdump-tools.cfg` exists (it comes with `kdump-tools`), it extends the variable (it uses `$GRUB_CMDLINE_LINUX_DEFAULT`), so it keeps the parameter. `grub show` warns about any `grub.d` file that replaces the variable instead.

## If the panel stays dark

Nothing times out: the panel goes dark only after a `SCREENPAD_LIGHT` write, that is any `brightness` write or `bl_power` 0 (see [How it works](#how-it-works)). If it ever stays dark, this one line relights it:

```sh
echo 4 | sudo tee /sys/class/backlight/asus_screenpad/bl_power
```

It does nothing if `bl_power` already reads 4; `sudo screenpad replug` relights it from any state. If it lights only that way, try `sudo screenpad on --relight-gap 2`. Also check that `screenpad status` shows the saved EDID with `1080x1920 @ 60.70 Hz` and `HDMI VSDB`.

- **`has no EDID` with the boot parameter applied:** nvidia-drm did not take it; see [If nvidia-drm does not take it](#if-nvidia-drm-does-not-take-it).
- **Never reports connected:** roll back, then get the DSDT (`sudo acpidump -b && iasl -d dsdt.dat && grep -n -iE '50031|50032' dsdt.dsl`) to check whether your unit's power method needs another WMI device ID.

For a bug report, include `screenpad status`, `journalctl -b -u screenpad` and `journalctl -k -b | grep -iE 'edid|HDMI-A-2'`.

## Rolling back by hand

The normal rollback is `sudo screenpad off` (and `sudo screenpad grub remove` if you applied the boot parameter); see the [install guide](install.md#rollback-and-uninstall).

The same steps by hand, if the CLI itself is the problem (replace `card0`, `HDMI-A-2` and the debugfs directory with what `status` printed). With the kernel in lockdown:

```sh
printf , | sudo tee /sys/module/drm/parameters/edid_firmware  # clears the runtime value; printf, not echo (see above); a reboot also clears it
echo detect | sudo tee /sys/class/drm/card0-HDMI-A-2/status
echo 0 | sudo tee /sys/class/backlight/asus_screenpad/bl_power   # darkens it, then a brightness rewrite makes sure:
cat /sys/class/backlight/asus_screenpad/brightness | sudo tee /sys/class/backlight/asus_screenpad/brightness
sudo sed -i 's| drm.edid_firmware=HDMI-A-2:screenpad/edid.bin||' /etc/default/grub && sudo update-grub
```

Without lockdown, reset the debugfs override instead of the parameter:

```sh
D=/sys/kernel/debug/dri/0000:02:00.0/HDMI-A-2
sudo sh -c "printf reset > $D/edid_override"    # printf, not echo: the kernel only accepts exactly "reset"
echo detect | sudo tee /sys/class/drm/card0-HDMI-A-2/status
echo 0 | sudo tee /sys/class/backlight/asus_screenpad/bl_power   # darkens it, then a brightness rewrite makes sure:
cat /sys/class/backlight/asus_screenpad/brightness | sudo tee /sys/class/backlight/asus_screenpad/brightness
```

A reboot also clears everything except an applied boot parameter, unless the unit is installed. If the unit is installed and the panel misbehaves at boot, `sudo systemctl disable screenpad.service`, or at the GRUB menu add `systemd.mask=screenpad.service` to the kernel line for one boot. Boot a recovery entry to start without the parameter.

## GNOME placement

The bridge presents a portrait 1080x1920 mode. In **Settings > Displays**:

1. Choose **Join Displays** if GNOME asks.
2. Select the new monitor (mutter calls it `HDMI-2`; the eDP panel is `eDP-1`).
3. Set **Orientation** so the picture reads upright on the ScreenPad. The panel sits in landscape below the keyboard, so one of the portrait settings applies; the UX580 reference uses a 270° rotation. Try **Portrait Left** first and switch to **Portrait Right** if it is upside down.
4. Drag it **below eDP-1**, horizontally centred, and click **Apply**.

mutter saves this to `~/.config/monitors.xml` and restores it whenever the ScreenPad appears. To get the same layout on the login screen, copy that file to `/var/lib/gdm3/.config/monitors.xml` (owner `gdm`).

GNOME composites on the Intel GPU and copies each frame to the NVIDIA output. That costs some CPU/GPU time and memory bandwidth; check `top` once the panel is on if your machine has little RAM to spare. The copy covers only what changed, so a bad copy can stay on the panel as noise until that area is drawn again; the touch bar's extension repaints the whole ScreenPad now and then to clear it (see [the repaint safeguard](touchbar.md#the-repaint-safeguard)).
