import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from affiliations import (  # noqa: E402
    apply_affiliations,
    load_target_schools,
    match_school,
    normalize_name,
    paper_doi,
)


class MatchSchoolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schools = load_target_schools(
            pathlib.Path(__file__).resolve().parents[1] / "config" / "target_schools.json"
        )

    def test_loads_all_target_schools(self):
        self.assertEqual(len(self.schools), 11)
        self.assertTrue(all(school.get("zh") for school in self.schools))

    def test_matches_full_names(self):
        cases = {
            "Princeton University": "Princeton",
            "Massachusetts Institute of Technology": "MIT",
            "Stanford University": "Stanford",
            "University of Chicago": "Chicago",
            "New York University": "NYU",
            "Columbia University": "Columbia",
            "Cornell University": "Cornell",
            "University of California, Berkeley": "UC Berkeley",
            "Carnegie Mellon University": "CMU",
            "Northwestern University": "Northwestern",
            "Duke University": "Duke",
        }
        for institution, expected in cases.items():
            self.assertEqual(match_school(institution, self.schools)["en"], expected, institution)

    def test_short_aliases_need_whole_word(self):
        # Smithsonian 里含 "mit"，Northeastern 里含 "north..."，都不能算命中
        self.assertIsNone(match_school("Smithsonian Institution", self.schools))
        self.assertIsNone(match_school("Northeastern University", self.schools))
        self.assertIsNone(match_school("University of British Columbia", self.schools))
        self.assertEqual(match_school("MIT Sloan School of Management", self.schools)["en"], "MIT")

    def test_normalizes_punctuation_and_ampersand(self):
        self.assertEqual(normalize_name("  Univ. of X & Y "), "univ of x and y")


class PaperDoiTest(unittest.TestCase):
    def test_arxiv_id_becomes_datacite_doi(self):
        self.assertEqual(paper_doi({"id": "2403.00819v2"}), "10.48550/arxiv.2403.00819")

    def test_doi_url_is_stripped(self):
        self.assertEqual(
            paper_doi({"doi": "https://doi.org/10.1080/07350015.2026.2696490"}),
            "10.1080/07350015.2026.2696490",
        )

    def test_unknown_paper_has_no_doi(self):
        self.assertEqual(paper_doi({"id": "openalex:W123"}), "")


class ApplyAffiliationsTest(unittest.TestCase):
    def test_writes_matches_and_institutions(self):
        schools = load_target_schools(
            pathlib.Path(__file__).resolve().parents[1] / "config" / "target_schools.json"
        )
        paper = {"id": "x"}
        authorships = [
            {"name": "A", "insts": ["Duke University"]},
            {"name": "B", "insts": ["University of Vienna"]},
        ]
        matches = apply_affiliations(paper, authorships, schools)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["school"], "Duke")
        self.assertEqual(paper["author_schools"][0]["i"], 0)
        self.assertEqual(paper["author_institutions"][1]["name"], "B")

    def test_no_match_clears_field(self):
        schools = load_target_schools(
            pathlib.Path(__file__).resolve().parents[1] / "config" / "target_schools.json"
        )
        paper = {"id": "x", "author_schools": [{"i": 0, "school": "Duke"}]}
        apply_affiliations(paper, [{"name": "A", "insts": ["University of Vienna"]}], schools)
        self.assertNotIn("author_schools", paper)


if __name__ == "__main__":
    unittest.main()
