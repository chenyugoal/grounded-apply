from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from grounded_apply.services.search_filters import PreparationFilters, validate_preparation_filters


class SearchFilterTests(unittest.TestCase):
    def test_defaults_preserve_known_and_unknown_locations_and_round_trip(self) -> None:
        filters = validate_preparation_filters({})
        self.assertEqual(filters.to_dict(), {"title_excludes": [], "location_contains": [],
            "location_excludes": [], "missing_location": "include"})
        self.assertIsNone(filters.assess(title="Fictional Engineer", location=None))
        self.assertIsNone(filters.assess(title="Fictional Engineer", location="Fictional City"))
        self.assertEqual(validate_preparation_filters(filters.to_dict()), filters)

    def test_title_exclusion_wins_before_all_location_rules(self) -> None:
        filters = PreparationFilters(title_excludes=("senior",), location_contains=("Preferred",),
            location_excludes=("Blocked",), missing_location="exclude")
        for location in (None, "Preferred City", "Other City", "Blocked City"):
            with self.subTest(location=location):
                self.assertEqual(filters.assess(title="Fictional SENIOR Research Engineer", location=location), "title_excluded")

    def test_location_exclusion_wins_over_inclusion_and_inclusion_is_or(self) -> None:
        filters = PreparationFilters(location_contains=("Preferred", "Another"), location_excludes=("Blocked",))
        self.assertEqual(filters.assess(title="Fictional Engineer", location="Preferred but Blocked"), "location_excluded")
        self.assertEqual(filters.assess(title="Fictional Engineer", location="Other City"), "location_not_matched")
        for location in ("Preferred City", "Another City", "Preferred City; Another City"):
            with self.subTest(location=location):
                self.assertIsNone(filters.assess(title="Fictional Engineer", location=location))

    def test_missing_include_bypasses_location_text_rules_but_not_title_exclusion(self) -> None:
        filters = PreparationFilters(title_excludes=("Intern",), location_contains=("Preferred",), location_excludes=("Remote",))
        self.assertIsNone(filters.assess(title="Fictional Engineer", location=None))
        self.assertEqual(filters.assess(title="Fictional Intern", location=None), "title_excluded")

    def test_missing_exclude_is_an_explicit_choice_even_without_location_terms(self) -> None:
        filters = validate_preparation_filters({"missing_location": "exclude"})
        self.assertEqual(filters.assess(title="Fictional Engineer", location=None), "location_unknown_excluded")
        self.assertIsNone(filters.assess(title="Fictional Engineer", location="Fictional City"))

    def test_unicode_casefold_matches_without_geographic_aliases(self) -> None:
        filters = PreparationFilters(title_excludes=("STRAẞE",), location_contains=("STRASSE", "MÜNCHEN"))
        self.assertEqual(filters.assess(title="Fictional Straße Engineer", location="Fictional City"), "title_excluded")
        for location in ("Straße", "München"):
            with self.subTest(location=location):
                self.assertIsNone(filters.assess(title="Fictional Engineer", location=location))
        self.assertEqual(filters.assess(title="Fictional Engineer", location="Munich"), "location_not_matched")

    def test_remote_is_literal_text_and_does_not_mean_worldwide_eligibility(self) -> None:
        exact_text = PreparationFilters(location_contains=("Remote—US",))
        self.assertIsNone(exact_text.assess(title="Fictional Engineer", location="Remote—US"))
        for location in ("Remote—UK", "Worldwide", "United States", "Remote-US"):
            with self.subTest(location=location):
                self.assertEqual(exact_text.assess(title="Fictional Engineer", location=location), "location_not_matched")
        broad_text = PreparationFilters(location_contains=("Remote",))
        # A broad substring even matches negated wording. This deliberately
        # remains a text preference, never a remote-work eligibility classifier.
        self.assertIsNone(broad_text.assess(title="Fictional Engineer", location="Not Remote—US office only"))
        self.assertEqual(set(exact_text.to_dict()), {"title_excludes", "location_contains", "location_excludes", "missing_location"})

    def test_exclusions_are_substrings_not_inferred_seniority(self) -> None:
        filters = PreparationFilters(title_excludes=("Senior",))
        self.assertIsNone(filters.assess(title="Fictional Engineer IV", location=None))
        self.assertEqual(filters.assess(title="Fictional Seniority Researcher", location=None), "title_excluded")

    def test_policy_is_immutable_and_does_not_retain_mutable_input(self) -> None:
        source = {"location_contains": ["Preferred"]}
        filters = validate_preparation_filters(source)
        source["location_contains"].append("Other")
        exported = filters.to_dict()
        exported["location_contains"].append("Other")
        self.assertEqual(filters.location_contains, ("Preferred",))
        with self.assertRaises(FrozenInstanceError):
            filters.missing_location = "exclude"

    def test_closed_fields_list_types_and_missing_policy_are_validated(self) -> None:
        bad = (None, [], True, {"remote_only": True}, {"schema_version": 1}, {"missing_location": "guess"},
            {"missing_location": None}, {"missing_location": []}, {"missing_location": True})
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_preparation_filters(value)
        for key in ("title_excludes", "location_contains", "location_excludes"):
            for value in (None, "Preferred", ("Preferred",), {}, True):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_preparation_filters({key: value})

    def test_empty_untrimmed_control_and_oversized_terms_fail_closed(self) -> None:
        bad = ("", " ", " Preferred", "Preferred ", "x" * 129, "a\nb", "a\tb", "a\x00b", "a\x7fb",
            "a\x85b", "a\u200bb", "a\u202eb", "a\ud800b", None, True, 4)
        for key in ("title_excludes", "location_contains", "location_excludes"):
            for term in bad:
                with self.subTest(key=key, term=repr(term)), self.assertRaises(ValueError):
                    validate_preparation_filters({key: [term]})
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_preparation_filters({key: ["Preferred"] * 21})
            self.assertEqual(len(getattr(validate_preparation_filters({key: ["x" * 128] * 20}), key)), 20)

    def test_direct_model_construction_rejects_mutable_or_invalid_policy(self) -> None:
        for values in ({"title_excludes": ["Senior"]}, {"location_contains": ("",)},
            {"location_excludes": ("x" * 129,)}, {"missing_location": "unknown"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                PreparationFilters(**values)

    def test_invalid_source_metadata_is_not_silently_treated_as_missing(self) -> None:
        filters = PreparationFilters()
        for title in (None, True, "", " ", "x" * 513, "é" * 257, "line\nbreak", "bad\x00title", "\ud800"):
            with self.subTest(title=repr(title)), self.assertRaises(ValueError):
                filters.assess(title=title, location=None)
        for location in (True, "", " ", "x" * 2049, "é" * 1025, "line\nbreak", "bad\x7flocation", "\ud800"):
            with self.subTest(location=repr(location)), self.assertRaises(ValueError):
                filters.assess(title="Fictional Engineer", location=location)


if __name__ == "__main__":
    unittest.main()
