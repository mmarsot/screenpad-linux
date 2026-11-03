#!/bin/sh
# Install or remove the ScreenPad display helper and touch bar. Run as root.
#   ./install.sh            install and enable the boot unit (does not start it);
#                           the touch bar is turned on per user with
#                           'screenpad-touchbar on'
#   ./install.sh uninstall  stop (runs 'screenpad off'), disable and remove
#   ./install.sh purge      uninstall and also delete the saved EDID
# It never edits GRUB; 'screenpad grub show|apply|remove' does that.
# DESTDIR prefixes every path. It is for the tests, which also put stub
# commands first on PATH.
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
R=${DESTDIR:-}
BIN=/usr/local/sbin/screenpad
# The programs: screenpad.py and touchbar.py with their packages, and the
# client library screenpad_app. BIN and TOUCHBAR are relative symlinks to the
# scripts here.
LIB=/usr/local/lib/screenpad
UNIT=/etc/systemd/system/screenpad.service
HOOK=/usr/lib/systemd/system-sleep/screenpad
DOC=/usr/local/share/doc/screenpad
STATE=/var/lib/screenpad
FIRMWARE=/lib/firmware/screenpad/edid.bin
INITRAMFS_HOOK=/etc/initramfs-tools/hooks/screenpad
GRUB=/etc/default/grub
TOUCHBAR=/usr/local/bin/screenpad-touchbar
TOUCHBAR_UNIT=/usr/lib/systemd/user/screenpad-touchbar.service
# Where earlier versions installed the agent app, which now has its own
# repository and per-user installer. An update leaves it in place and says how
# to remove it; uninstall removes it with the rest.
AGENTS=/usr/local/bin/screenpad-agents
AGENTS_UNIT=/usr/lib/systemd/user/screenpad-agents.service
AGENTS_REPO=https://github.com/mmarsot/screenpad-agents
EXTENSION=/usr/local/share/gnome-shell/extensions/screenpad-touchbar@screenpad
UDEV_RULE=/etc/udev/rules.d/70-screenpad-uinput.rules
BACKLIGHT_RULE=/etc/udev/rules.d/70-screenpad-backlight.rules
PRODUCT="ZenBook UX450FDX_UX480FD"

die() {
    echo "install.sh: $*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || die "run as root"

# drm.edid_firmware loads the file when nvidia-drm probes the connector. Only
# when that happens inside the initramfs does the file need to be in there.
nvidia_in_initramfs() {
    lsinitramfs "$R/boot/initrd.img-$(uname -r)" 2>/dev/null | grep -q '/nvidia-drm\.ko'
}

locked_down() {
    lockdown=$(cat "$R/sys/kernel/security/lockdown" 2>/dev/null || true)
    case "$lockdown" in
    "" | *"[none]"*) return 1 ;;
    *) return 0 ;;
    esac
}

# The old agent app's files that are still there, one per line.
old_agent_app() {
    for f in "$AGENTS" "$AGENTS_UNIT" "$LIB/agents.py" "$LIB/screenpad_agents"; do
        if [ -e "$R$f" ] || [ -L "$R$f" ]; then
            echo "$f"
        fi
    done
}

# Says how to remove the old agent app instead of removing it: it may be
# running, and the user may not have installed its replacement yet.
note_old_agent_app() {
    old=$(old_agent_app)
    [ -n "$old" ] || return 0
    echo "The agent app moved to $AGENTS_REPO and installs per user;"
    echo "this install no longer updates the copy an earlier version put here and leaves it:"
    echo "$old" | sed 's/^/  /'
    echo "To remove it: if you enabled it and have not installed the new one, first, as your user:"
    echo "  systemctl --user disable --now screenpad-agents"
    echo "then:"
    echo "  sudo rm -rf $(echo "$old" | tr '\n' ' ' | sed 's/ $//')"
    echo "The new one installs as your user with its ./install.sh: $AGENTS_REPO#install"
}

# A package directory is replaced whole, so a module an earlier version had
# does not linger.
install_programs() {
    for prog in screenpad touchbar; do
        install -D -m 0755 "$here/$prog.py" "$R$LIB/$prog.py"
    done
    for pkg in screenpad_display screenpad_touchbar screenpad_app; do
        rm -rf "$R$LIB/$pkg"
        for f in "$here/$pkg"/*.py; do
            install -D -m 0644 "$f" "$R$LIB/$pkg/$(basename "$f")"
        done
    done
    install -d -m 0755 "$R$(dirname "$BIN")" "$R$(dirname "$TOUCHBAR")"
    ln -sfn ../lib/screenpad/screenpad.py "$R$BIN"
    ln -sfn ../lib/screenpad/touchbar.py "$R$TOUCHBAR"
}

install_all() {
    product=$(cat "$R/sys/class/dmi/id/product_name" 2>/dev/null || true)
    [ "$product" = "$PRODUCT" ] || die "unsupported machine '$product' (want '$PRODUCT')"
    install_programs
    install -D -m 0644 "$here/systemd/screenpad.service" "$R$UNIT"
    install -D -m 0755 "$here/systemd/screenpad-sleep" "$R$HOOK"
    install -D -m 0644 "$here/README.md" "$R$DOC/README.md"
    for f in "$here"/docs/*.md; do
        install -D -m 0644 "$f" "$R$DOC/docs/$(basename "$f")"
    done
    install -D -m 0644 "$here/systemd/screenpad-touchbar.service" "$R$TOUCHBAR_UNIT"
    for f in "$here"/gnome-shell/screenpad-touchbar@screenpad/*; do
        install -D -m 0644 "$f" "$R$EXTENSION/$(basename "$f")"
    done
    install -D -m 0644 "$here/udev/70-screenpad-uinput.rules" "$R$UDEV_RULE"
    install -D -m 0644 "$here/udev/70-screenpad-backlight.rules" "$R$BACKLIGHT_RULE"
    udevadm control --reload
    udevadm trigger --sysname-match=uinput
    udevadm trigger --subsystem-match=backlight --sysname-match=asus_screenpad
    install -d -m 0755 "$R$STATE"
    if [ -e "$R$STATE/edid.bin" ]; then
        install -D -m 0644 "$R$STATE/edid.bin" "$R$FIRMWARE"
        echo "Copied the saved EDID to $FIRMWARE."
    else
        echo "No saved EDID yet; 'screenpad capture-edid' saves it to $STATE and $FIRMWARE."
    fi
    if nvidia_in_initramfs; then
        install -D -m 0755 "$here/initramfs/screenpad" "$R$INITRAMFS_HOOK"
        update-initramfs -u
        echo "nvidia-drm is in the initramfs, so $FIRMWARE was added to it."
    fi
    systemctl daemon-reload
    systemctl enable screenpad.service
    echo "Installed. Capture the EDID with 'screenpad capture-edid' if you have not yet;"
    echo "the panel comes up at next boot, or now with 'systemctl start screenpad'."
    echo "Touch bar: run 'screenpad-touchbar on' as your user (not root), then log out and in once."
    note_old_agent_app
    if locked_down; then
        echo "The kernel is in lockdown, so the EDID goes through drm.edid_firmware."
        echo "'screenpad grub show' prints the boot parameter change; 'screenpad grub apply' makes it."
    fi
}

uninstall_all() {
    if grep -qs 'drm\.edid_firmware=[^" ]*screenpad/edid\.bin' "$R$GRUB"; then
        die "$GRUB still loads $FIRMWARE at boot; run 'screenpad grub remove' first"
    fi
    if [ -e "$R$UNIT" ]; then
        systemctl disable --now screenpad.service || true
    fi
    rm -f "$R$BIN" "$R$UNIT" "$R$HOOK" "$R$FIRMWARE" "$R$TOUCHBAR" "$R$TOUCHBAR_UNIT" "$R$UDEV_RULE"
    rm -f "$R$BACKLIGHT_RULE"
    old=$(old_agent_app)
    rm -f "$R$AGENTS" "$R$AGENTS_UNIT"
    rm -rf "$R$EXTENSION" "$R$LIB"
    udevadm control --reload
    rmdir "$R$(dirname "$FIRMWARE")" 2>/dev/null || true
    rm -rf "$R$DOC"
    if [ -e "$R$INITRAMFS_HOOK" ]; then
        rm -f "$R$INITRAMFS_HOOK"
        update-initramfs -u
    fi
    systemctl daemon-reload
    echo "A touch bar that is on keeps running until you log out; 'screenpad-touchbar off'"
    echo "(as your user) stops it now, and the plain touchpad comes back."
    if [ -n "$old" ]; then
        echo "The agent app an earlier version installed was removed too. If you enabled it and"
        echo "have not installed $AGENTS_REPO, it stops at log out, or now"
        echo "with 'systemctl --user disable --now screenpad-agents'."
    fi
    echo "Removed. The saved EDID in $STATE is kept; './install.sh purge' deletes it."
}

case "${1:-install}" in
install) install_all ;;
uninstall) uninstall_all ;;
purge)
    uninstall_all
    rm -rf "$R$STATE"
    ;;
*) die "usage: $0 [install|uninstall|purge]" ;;
esac
