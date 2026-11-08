import unittest

from x_insight.cases.new_case_demographics import age_at_registration, normalize_name


class NewCaseDemographicsTest(unittest.TestCase):
    def test_birth_year_boundaries_and_types(self):
        self.assertEqual(age_at_registration(1926, 2026), 100)
        self.assertEqual(age_at_registration(2008, 2026), 18)
        for value in (1925, 2009, True, "2000", 2000.0, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                age_at_registration(value, 2026)

    def test_names_are_normalized_without_rewriting_punctuation(self):
        self.assertEqual(normalize_name("  E\u0301mile  "), "Émile")
        self.assertEqual(normalize_name("علی رضایی"), "علی رضایی")
        for value in ("", " ", "A-B", "O'Neil", "A\tB", "A\u200bB", "A1", "a" * 101):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_name(value)


if __name__ == "__main__":
    unittest.main()
