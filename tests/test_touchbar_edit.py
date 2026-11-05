"""Tests for writing touchbar.toml in place: the height, adding, moving and removing apps among the items,
and moving items of any kind between the bar and the apps page.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import os
import tempfile
import unittest

from touchbar_support import ConfigHome, config

from screenpad_touchbar.common import ConfigError
from screenpad_touchbar.config import load_config
from screenpad_touchbar.config_edit import add_app, edit_config, remove_item, with_height, with_items
from screenpad_touchbar.defaults import EXAMPLE_CONFIG
from screenpad_touchbar.items import Item

# Items of every kind, with comments of their own.
MIXED = """\
height = 160

# Talk.
[[bar]]
label = "PTT"
mode = "hold"
key = "F14"  # mine

[[bar]]
app = "files.desktop"
label = "Files"

# Agents, from screenpad-agents.
[[bar]]
label = "Agents"
tile = "agents"

[notifications]
mirror = false

[[apps]]
app = "settings.desktop"
"""


def names(items):
    return [i.name if i.kind == "app" else i.label for i in items]


class WithHeightTests(unittest.TestCase):
    def test_edits_only_that_line(self):
        text = '# top\nheight = 160  # mine\n\n[[bar]]\nlabel = "A"\nkey = "a"\n'
        self.assertEqual(with_height(text, 240), text.replace("160  # mine", "240"))
        # No height line yet: one goes in before the first table.
        out = with_height('# top\n[[bar]]\nlabel = "A"\nkey = "a"\n', 96)
        self.assertEqual(out, '# top\nheight = 96\n\n[[bar]]\nlabel = "A"\nkey = "a"\n')
        self.assertEqual(with_height("", 96), "height = 96\n\n")
        # A height inside a table is not the bar's.
        self.assertEqual(config(with_height('[[bar]]\napp = "a"\n', 200)).height, 200)


class WithItemsTests(unittest.TestCase):
    def edit(self, text, where, change):
        old = config(text).items(where)
        out = with_items(text, where, old, change(old))
        return out, config(out)

    def test_adding_keeps_everything_else(self):
        out, c = self.edit(EXAMPLE_CONFIG, "bar", lambda old: old + [Item.app("a.desktop"), Item.app("b.desktop")])
        self.assertEqual(names(c.bar)[3:], ["a.desktop", "b.desktop"])
        # Right after the last item, before the comments about what follows.
        self.assertIn(
            'key = "printscreen"\n\n[[bar]]\napp = "a.desktop"\n\n[[bar]]\napp = "b.desktop"\n\n# An app', out
        )
        self.assertEqual(
            out.replace('\n[[bar]]\napp = "a.desktop"\n\n[[bar]]\napp = "b.desktop"\n', ""), EXAMPLE_CONFIG
        )
        # Removing them gives the file back, without blank lines piling up.
        back = with_items(out, "bar", c.bar, c.bar[:3])
        self.assertEqual(back, EXAMPLE_CONFIG)
        bar = config(back).bar
        again = with_items(back, "bar", bar, bar + [Item.app("a.desktop"), Item.app("b.desktop")])
        self.assertEqual(again, out)

    def test_inserting_before_an_item_takes_its_comments_along(self):
        out, c = self.edit(MIXED, "bar", lambda old: [old[0], Item.app("web.desktop")] + old[1:])
        self.assertEqual(names(c.bar), ["PTT", "web.desktop", "files.desktop", "Agents"])
        self.assertIn('key = "F14"  # mine\n\n[[bar]]\napp = "web.desktop"\n\n[[bar]]\napp = "files.desktop"', out)
        # Before the first, above its comment.
        out, c = self.edit(MIXED, "bar", lambda old: [Item.app("web.desktop")] + old)
        self.assertIn('height = 160\n\n[[bar]]\napp = "web.desktop"\n\n# Talk.\n[[bar]]', out)
        # Before Agents, above "# Agents, ...".
        out, c = self.edit(MIXED, "bar", lambda old: old[:2] + [Item.app("web.desktop")] + old[2:])
        self.assertIn('[[bar]]\napp = "web.desktop"\n\n# Agents, from screenpad-agents.\n[[bar]]', out)
        self.assertEqual(c.notifications.mirror, False)
        self.assertEqual(names(c.apps), ["settings.desktop"])

    def test_removing_and_moving_an_app(self):
        out, c = self.edit(MIXED, "bar", lambda old: [old[0], old[2]])
        self.assertEqual(names(c.bar), ["PTT", "Agents"])
        self.assertIn('key = "F14"  # mine\n\n# Agents, from screenpad-agents.\n[[bar]]', out)
        # A move is a removal and an addition; the app keeps its label.
        out, c = self.edit(MIXED, "bar", lambda old: add_app(old, "files.desktop", 2))
        self.assertEqual([(i.name, i.label) for i in c.bar][1:], [("agents", "Agents"), ("files.desktop", "Files")])
        self.assertIn('[[bar]]\napp = "files.desktop"\nlabel = "Files"\n\n[notifications]', out)

    def test_the_apps_page(self):
        out, c = self.edit(MIXED, "apps", lambda old: old + [Item.app("bitwarden.desktop")])
        self.assertTrue(out.endswith('[[apps]]\napp = "settings.desktop"\n\n[[apps]]\napp = "bitwarden.desktop"\n'))
        self.assertEqual(names(c.apps), ["settings.desktop", "bitwarden.desktop"])
        # A place with no items yet: at the end of the file.
        text = 'height = 100\n[[bar]]\nlabel = "A"\nkey = "a"\n'
        out = with_items(text, "apps", [], [Item.app("x.desktop")])
        self.assertEqual(out, text + '\n[[apps]]\napp = "x.desktop"\n')
        self.assertEqual(with_items("", "bar", [], [Item.app("x.desktop")]), '[[bar]]\napp = "x.desktop"\n')

    def test_writes_any_name_safely(self):
        item = Item.app("a.desktop", label='Say "hi" \\ there', icon="/tmp/a b.svg")
        out = with_items("", "bar", [], [item])
        self.assertEqual(config(out).bar[0].spec(), item.spec())

    def test_refuses_any_other_change(self):
        c = config(MIXED)
        for new in (
            # Another kind of item added, a key removed, two items swapped.
            c.bar + [Item("key", "B", mode="tap", key="b", keys=(48,))],
            c.bar[1:],
            [c.bar[2], c.bar[1], c.bar[0]],
        ):
            with self.assertRaises(ConfigError):
                with_items(MIXED, "bar", c.bar, new)

    def test_moving_items_between_the_lists(self):
        c = config(MIXED)
        lines = MIXED.splitlines(keepends=True)
        ptt = lines[lines.index("# Talk.\n") + 1 : lines.index('key = "F14"  # mine\n') + 1]
        # A key moves to the apps page with its block as it is, under [[apps]].
        out = with_items(MIXED, "apps", c.apps, c.apps + [c.bar[0]], {id(c.bar[0]): ptt})
        self.assertTrue(
            out.endswith('app = "settings.desktop"\n\n[[apps]]\nlabel = "PTT"\nmode = "hold"\nkey = "F14"  # mine\n')
        )
        out = with_items(out, "bar", c.bar, c.bar[1:], {id(c.bar[0]): ptt})
        moved = config(out)
        self.assertEqual([i.spec() for i in moved.apps], [i.spec() for i in c.apps + [c.bar[0]]])
        self.assertEqual(names(moved.bar), ["files.desktop", "Agents"])
        # Its comment stays where it was, as when an app is removed.
        self.assertIn("# Talk.\n\n[[bar]]\napp", out)
        # Without its block, a key is not written nor removed.
        for name, old, new in (("apps", c.apps, c.apps + [c.bar[0]]), ("bar", c.bar, c.bar[1:])):
            with self.assertRaises(ConfigError):
                with_items(MIXED, name, old, new)

    def test_add_app(self):
        a, b, c, key = Item.app("a"), Item.app("b"), Item.app("c"), Item("key", "K", mode="tap", key="k", keys=(37,))
        self.assertEqual(names(add_app([], "a")), ["a"])
        self.assertEqual(names(add_app([a], "b")), ["a", "b"])
        # At a spot, the items after it move along.
        self.assertEqual(names(add_app([a, key], "c", 0)), ["c", "a", "K"])
        self.assertEqual(names(add_app([a], "c", 4)), ["a", "c"])
        # Full: added at an app's spot it replaces it; at another item's,
        # or without a spot, there is no room.
        full = [a, b, c, key, Item.app("d"), Item.app("e")]
        self.assertEqual(names(add_app(full, "f", 1)), ["a", "f", "c", "K", "d", "e"])
        for spot in (3, None):
            with self.assertRaises(ConfigError):
                add_app(full, "f", spot)
        # One already there moves.
        self.assertEqual(names(add_app([a, b, c], "a", 2)), ["b", "c", "a"])
        self.assertEqual(names(add_app([a, b], "a")), ["b", "a"])
        # Put back where it is: the same list, nothing to write.
        same = [a, b]
        self.assertIs(add_app(same, "a", 0), same)
        with self.assertRaises(ConfigError):
            add_app([], "a", 6)
        # The apps page has eight.
        self.assertEqual(len(add_app([Item.app(str(n)) for n in range(7)], "x", limit=8)), 8)

    def test_remove_item_takes_any_kind_but_only_the_one_asked_for(self):
        a, key = Item.app("a"), Item("key", "K", mode="tap", key="k", keys=(37,))
        self.assertEqual(remove_item([key, a], 1), [key])
        self.assertEqual(remove_item([key, a], 0), [a])
        for index in (-1, 2):
            with self.assertRaises(ConfigError):
                remove_item([key, a], index)
        # The item the caller saw must still be the one there.
        self.assertEqual(remove_item([key, a], 0, Item("key", "K", mode="tap", key="k", keys=(37,))), [a])
        with self.assertRaises(ConfigError):
            remove_item([key, a], 0, a)


class EditConfigTests(unittest.TestCase):
    def test_edit_config(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            # No file yet: it starts from the default.
            c = edit_config(lambda c: {"bar": add_app(c.bar, "a.desktop")})
            self.assertEqual(names(c.bar)[3:], ["a.desktop"])
            with open(path) as f:
                text = f.read()
            self.assertEqual(text.replace('\n[[bar]]\napp = "a.desktop"\n', ""), EXAMPLE_CONFIG)
            c = edit_config(lambda c: {"height": 320})
            self.assertEqual((c.height, names(c.bar)[3:]), (320, ["a.desktop"]))
            c = edit_config(lambda c: {"apps": add_app(c.apps, "b.desktop", limit=8)})
            self.assertEqual((load_config()[0].height, names(c.apps)), (320, ["b.desktop"]))
            c = edit_config(lambda c: {"bar": remove_item(c.bar, 3)})
            with open(path) as f:
                self.assertEqual(
                    f.read(), EXAMPLE_CONFIG.replace("height = 160", "height = 320") + '\n[[apps]]\napp = "b.desktop"\n'
                )
            # A broken file is not touched.
            with open(path, "w") as f:
                f.write("height = 'x'\n")
            with self.assertRaises(ConfigError) as cm:
                edit_config(lambda c: {"height": 200})
            self.assertIn("fix this first", str(cm.exception))
            with open(path) as f:
                self.assertEqual(f.read(), "height = 'x'\n")

    def test_moving_and_swapping_items(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(MIXED)
            # The key and the tile go to the apps page, the app there to the bar.
            c = edit_config(lambda c: {"bar": [c.bar[1], c.apps[0]], "apps": [c.bar[0], c.bar[2]]})
            self.assertEqual((names(c.bar), names(c.apps)), (["files.desktop", "settings.desktop"], ["PTT", "Agents"]))
            with open(path) as f:
                text = f.read()
            self.assertTrue(
                text.endswith(
                    '[notifications]\nmirror = false\n\n[[apps]]\nlabel = "PTT"\nmode = "hold"\nkey = "F14"  # mine\n'
                    '\n[[apps]]\nlabel = "Agents"\ntile = "agents"\n'
                ),
                text,
            )
            # Swapped back, in the same places: every block where it began.
            c = edit_config(lambda c: {"bar": [c.apps[0], c.bar[0], c.apps[1]], "apps": [c.bar[1]]})
            self.assertEqual(
                [i.spec() for i in c.bar + c.apps], [i.spec() for i in config(MIXED).bar + config(MIXED).apps]
            )
            # The same item twice is refused, the file untouched.
            with open(path) as f:
                before = f.read()
            with self.assertRaises(ConfigError):
                edit_config(lambda c: {"apps": c.apps + [c.bar[0]]})
            with open(path) as f:
                self.assertEqual(f.read(), before)

    def test_refuses_an_unsafe_edit(self):
        # A [[bar]]-looking line inside a multi-line string would be cut.
        text = 'height = 100\n[[bar]]\nlabel = """\n[[bar]]\n"""\nkey = "a"\n'
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(text)
            with self.assertRaises(ConfigError) as cm:
                edit_config(lambda c: {"bar": add_app(c.bar, "a.desktop")})
            self.assertIn("change it by hand", str(cm.exception))
            with open(path) as f:
                self.assertEqual(f.read(), text)
            # An inline array of tables has no blocks to edit either.
            text = 'bar = [{app = "a.desktop"}]\n'
            with open(path, "w") as f:
                f.write(text)
            with self.assertRaises(ConfigError):
                edit_config(lambda c: {"bar": add_app(c.bar, "b.desktop")})
            with open(path) as f:
                self.assertEqual(f.read(), text)


if __name__ == "__main__":
    unittest.main()
