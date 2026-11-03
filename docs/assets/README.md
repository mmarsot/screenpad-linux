# README assets

Images the root [README](../../README.md) shows. The banner comes in a light and a dark version, picked by a `<picture>` element, since GitHub strips CSS from READMEs. The README's screenshots are the touch bar's own, in [`docs/images/`](../images/); [Development](../development.md) says how they are made.

| File | What it is | How to remake it |
|---|---|---|
| `banner-light.png`, `banner-dark.png` | The header banner | Render [`banner.html`](banner.html) (`?theme=light` and `?theme=dark`) at 1280×320 with a transparent background, for example `google-chrome --headless=new --hide-scrollbars --window-size=1280,320 --default-background-color=00000000 --screenshot=banner-dark.png "file://$PWD/banner.html?theme=dark"`. It uses the Ubuntu Sans font that Ubuntu installs, and loads nothing from the network. |

The banner draws the laptop's deck from above: the keyboard's last rows, then the ScreenPad with the touch bar on its top strip (push to talk held, mute, screenshot, Files, an empty spot, the page button and the grip) over the touchpad page's faint clock and battery level, as in [`touchbar.png`](../images/touchbar.png) and [`touchbar-touchpad-page.png`](../images/touchbar-touchpad-page.png).

The colours ("night deck"): ink `#0E1726` and slate `#1E293B`, teal `#2DD4BF` (text-safe on light `#0F766E`), amber `#FBBF24` (text-safe `#B45309`), mist `#EEF6F5`, line `#D5E6E3`, soft ink `#475569`. The badges use ink and the text-safe teal and amber.
