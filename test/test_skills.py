import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib.core import skills


class SkillsToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "skills"
        patcher = mock.patch.dict(os.environ, {skills.SKILLS_DIR_ENV: str(self.dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, name="my-skill", **overrides):
        args = {
            "action": "save",
            "name": name,
            "description": "Does a thing.",
            "content": "Step one.\nStep two.",
        }
        args.update(overrides)
        return skills.skills_manage(**args)

    def test_list_on_missing_directory_is_empty(self):
        result = skills.skills_manage(action="list")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["skills"], [])

    def test_save_then_get_round_trip(self):
        saved = self.save()
        self.assertTrue(saved["saved"])
        self.assertTrue(saved["created"])
        got = skills.skills_manage(action="get", name="my-skill")
        self.assertEqual(got["name"], "my-skill")
        self.assertEqual(got["description"], "Does a thing.")
        self.assertTrue(got["invokable"])
        self.assertEqual(got["body"].strip(), "Step one.\nStep two.")
        self.assertTrue(got["content"].startswith("---\nname: my-skill\n"))

    def test_list_returns_sorted_entries(self):
        self.save("b-skill", description="Second.")
        self.save("a-skill", description="First.", invokable=False)
        result = skills.skills_manage(action="list")
        self.assertEqual([s["name"] for s in result["skills"]], ["a-skill", "b-skill"])
        self.assertFalse(result["skills"][0]["invokable"])
        self.assertEqual(result["skills"][1]["description"], "Second.")

    def test_save_refuses_overwrite_by_default(self):
        self.save()
        again = self.save(content="Different.")
        self.assertEqual(again["error_code"], "already_exists")
        got = skills.skills_manage(action="get", name="my-skill")
        self.assertIn("Step one.", got["body"])

    def test_save_with_overwrite_replaces(self):
        self.save()
        again = self.save(content="Different.", overwrite=True)
        self.assertTrue(again["saved"])
        self.assertFalse(again["created"])
        got = skills.skills_manage(action="get", name="my-skill")
        self.assertEqual(got["body"].strip(), "Different.")
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_invalid_names_are_rejected_and_write_nothing(self):
        outside = Path(self._tmp.name) / "escaped.md"
        for bad in ["../escaped", "a/b", "A-Upper", "-lead", "", "x" * 65, "dot.name", "..\\evil"]:
            with self.subTest(name=bad):
                result = self.save(bad)
                self.assertIn(result["error_code"], {"invalid_name", "missing_argument"})
                result = skills.skills_manage(action="get", name=bad)
                self.assertIn(result["error_code"], {"invalid_name", "missing_argument"})
        self.assertFalse(outside.exists())
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_get_unknown_skill(self):
        result = skills.skills_manage(action="get", name="nope")
        self.assertEqual(result["error_code"], "not_found")

    def test_get_requires_name(self):
        result = skills.skills_manage(action="get")
        self.assertEqual(result["error_code"], "missing_argument")

    def test_save_requires_description_and_content(self):
        self.assertEqual(self.save(description=None)["error_code"], "missing_argument")
        self.assertEqual(self.save(description="  ")["error_code"], "missing_argument")
        self.assertEqual(self.save(content=None)["error_code"], "missing_argument")
        self.assertEqual(self.save(content="\n ")["error_code"], "missing_argument")

    def test_description_must_be_single_short_line(self):
        self.assertEqual(self.save(description="a\nb")["error_code"], "invalid_description")
        self.assertEqual(self.save(description="x" * 301)["error_code"], "invalid_description")

    def test_content_with_frontmatter_is_rejected(self):
        result = self.save(content="---\nname: x\n---\nbody")
        self.assertEqual(result["error_code"], "content_has_frontmatter")

    def test_oversized_skill_is_rejected(self):
        result = self.save(content="x" * (skills.MAX_SKILL_BYTES + 1))
        self.assertEqual(result["error_code"], "skill_too_large")

    def test_crlf_frontmatter_is_parsed(self):
        self.dir.mkdir(parents=True)
        (self.dir / "win.md").write_bytes(
            b"---\r\nname: win\r\ndescription: From Windows.\r\ninvokable: false\r\n---\r\n\r\nBody\r\n"
        )
        got = skills.skills_manage(action="get", name="win")
        self.assertEqual(got["description"], "From Windows.")
        self.assertFalse(got["invokable"])
        self.assertEqual(got["body"].strip(), "Body")

    def test_binary_skill_file_is_reported_not_raised(self):
        self.dir.mkdir(parents=True)
        (self.dir / "bad.md").write_bytes(b"\xff\xfe\x00bad")
        listed = skills.skills_manage(action="list")
        self.assertEqual(listed["errors"][0]["error_code"], "binary_file")
        got = skills.skills_manage(action="get", name="bad")
        self.assertEqual(got["error_code"], "binary_file")


class BaselineSkillsTests(unittest.TestCase):
    """Run against the real project skills directory (read-only)."""

    def test_baseline_skills_are_listed(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(skills.SKILLS_DIR_ENV, None)
            result = skills.skills_manage(action="list")
            names = {s["name"] for s in result["skills"]}
            self.assertTrue({"grill-me", "handoff"} <= names)
            got = skills.skills_manage(action="get", name="handoff")
            self.assertIn("handoff", got["description"].lower())


if __name__ == "__main__":
    unittest.main()