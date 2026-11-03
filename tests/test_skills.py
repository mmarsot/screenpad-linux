"""Checks the agent skills: front matter, the AGENTS.md index, the symlink, links.

Run with: python3 -m unittest discover -s tests
"""

import os
import re
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SKILLS = os.path.join(REPO, ".agents", "skills")
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
LINK = re.compile(r"\]\(([^)\s]+)\)")
# A backticked repository path: has a slash, is not absolute, a variable, a
# URL or a pattern.
REPO_PATH = re.compile(r"`([A-Za-z0-9_.@-]+/[A-Za-z0-9_.@/-]*)`")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def skill_names():
    return sorted(n for n in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, n)))


def anchors(markdown):
    """GitHub's heading anchors in a Markdown file."""
    out = set()
    for line in markdown.splitlines():
        m = re.match(r"#{1,6} +(.*?) *#*$", line)
        if m:
            text = re.sub(r"[^\w\- ]", "", m.group(1).lower())
            out.add(text.replace(" ", "-"))
    return out


class SkillTests(unittest.TestCase):
    def documents(self):
        """(path, text) of AGENTS.md and every skill."""
        docs = [os.path.join(REPO, "AGENTS.md")]
        docs += [os.path.join(SKILLS, n, "SKILL.md") for n in skill_names()]
        return [(p, read(p)) for p in docs]

    def test_claude_reads_the_same_skills(self):
        link = os.path.join(REPO, ".claude", "skills")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), "../.agents/skills")
        self.assertEqual(read(os.path.join(REPO, "CLAUDE.md")).strip(), "@AGENTS.md")

    def test_front_matter(self):
        self.assertIn("contributing", skill_names())
        for name in skill_names():
            text = read(os.path.join(SKILLS, name, "SKILL.md"))
            m = FRONT_MATTER.match(text)
            assert m is not None, name
            fields = dict(line.partition(": ")[::2] for line in m.group(1).splitlines())
            self.assertEqual(fields.get("name"), name)
            self.assertGreater(len(fields.get("description", "")), 40, name)

    def test_index_lists_every_skill(self):
        index = read(os.path.join(REPO, "AGENTS.md"))
        listed = re.findall(r"^\| `([a-z0-9-]+)` \|", index, re.M)
        self.assertEqual(sorted(listed), skill_names())

    def test_links_resolve(self):
        for path, text in self.documents():
            for target in LINK.findall(text):
                if re.match(r"[a-z]+:", target):
                    continue
                file, _, anchor = target.partition("#")
                full = os.path.normpath(os.path.join(os.path.dirname(path), file)) if file else path
                self.assertTrue(os.path.exists(full), "%s: %s" % (path, target))
                if anchor:
                    self.assertIn(anchor, anchors(read(full)), "%s: %s" % (path, target))

    def test_repository_paths_exist(self):
        for path, text in self.documents():
            for name in REPO_PATH.findall(text):
                self.assertTrue(os.path.exists(os.path.join(REPO, name)), "%s: %s" % (path, name))


if __name__ == "__main__":
    unittest.main()
