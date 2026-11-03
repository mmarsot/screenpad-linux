"""Light the first-generation ASUS ScreenPad on the ZenBook UX480FD.

The panel is an HDMI sink on the NVIDIA dGPU behind a Toshiba HDMI-to-MIPI
bridge. It needs three things the stock stack does not do:

1. An EDID the NVIDIA driver accepts. The bridge serves one with a bad CTA
   extension checksum, so we fix every block checksum and hand the result to
   the DRM core: through the debugfs edid_override, or, when the kernel is in
   lockdown (Secure Boot) and debugfs is closed, through the drm.edid_firmware
   parameter and /lib/firmware/screenpad/edid.bin.
2. The connector forced on.
3. Lighting through ASUS WMI once a compositor drives the output, by stepping
   the in-kernel asus_screenpad backlight's bl_power 0 -> 4 (see light()).

Run as root. Commands: on, off, replug, status, capture-edid, grub.
"""
