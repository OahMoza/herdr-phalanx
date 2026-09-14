import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SKILLS = {"herdr-phalanx", "herdr-runtime-init", "herdr-agent-bus"}


class TestSkillDiscovery(unittest.TestCase):
    def test_default_scan_finds_three_peer_skills(self):
        self.assertFalse(ROOT.joinpath("SKILL.md").exists(), "a root SKILL.md stops the default deep scan")

        names = set()
        for path in ROOT.glob("skills/*/SKILL.md"):
            frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
            match = re.search(r"^name:\s*([^\n]+)$", frontmatter, re.MULTILINE)
            self.assertIsNotNone(match, path)
            names.add(match.group(1).strip().strip('"'))

        self.assertEqual(EXPECTED_SKILLS, names)


if __name__ == "__main__":
    unittest.main()
