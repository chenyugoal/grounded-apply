from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from grounded_apply.services.profile_interview import INTERVIEW_TOPICS, build_interview


class ProfileInterviewTests(unittest.TestCase):
    def test_default_is_three_basic_questions_and_a_continuation(self) -> None:
        result = build_interview()
        self.assertEqual(result["topic"], "all")
        self.assertEqual(result["depth"], 1)
        self.assertEqual(len(result["questions"]), 3)
        self.assertEqual(result["next_cursor"], result["questions"][-1]["id"])
        self.assertFalse(result["finished"])
        self.assertEqual(result["remaining_questions"], result["total_questions"] - 3)

    def test_paging_covers_every_topic_once_without_missing_or_repeating_questions(self) -> None:
        for depth in (1, 2, 3):
            with self.subTest(depth=depth):
                cursor = None
                questions = []
                while True:
                    result = build_interview(depth=depth, after=cursor, limit=3)
                    questions.extend(result["questions"])
                    self.assertEqual(result["remaining_questions"],
                                     result["total_questions"] - len(questions))
                    if result["finished"]:
                        self.assertIsNone(result["next_cursor"])
                        break
                    cursor = result["next_cursor"]
                ids = [question["id"] for question in questions]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(len(ids), result["total_questions"])
                self.assertEqual(list(dict.fromkeys(q["topic"] for q in questions)),
                                 list(INTERVIEW_TOPICS))
                self.assertTrue(all(q["depth"] == depth for q in questions))

    def test_topic_and_depth_are_exact_round_filters(self) -> None:
        seen = set()
        for topic in INTERVIEW_TOPICS:
            for depth in (1, 2, 3):
                with self.subTest(topic=topic, depth=depth):
                    result = build_interview(topic, depth, limit=10)
                    self.assertTrue(result["finished"])
                    self.assertGreaterEqual(len(result["questions"]), 2)
                    self.assertTrue(all(q["topic"] == topic and q["depth"] == depth
                                        for q in result["questions"]))
                    for question in result["questions"]:
                        self.assertNotIn(question["id"], seen)
                        seen.add(question["id"])

    def test_cursor_can_skip_a_question_without_accepting_or_storing_answers(self) -> None:
        first = build_interview("research", 2, limit=1)
        second = build_interview("research", 2, after=first["next_cursor"], limit=1)
        self.assertNotEqual(first["questions"][0]["id"], second["questions"][0]["id"])
        self.assertTrue(second["finished"])
        self.assertFalse(second["answers_stored"])
        self.assertIsNone(second["next_cursor"])

    def test_last_question_cursor_yields_empty_finished_page(self) -> None:
        full = build_interview("publications", 3, limit=10)
        result = build_interview("publications", 3, after=full["questions"][-1]["id"])
        self.assertEqual(result["questions"], [])
        self.assertEqual(result["remaining_questions"], 0)
        self.assertTrue(result["finished"])
        self.assertIsNone(result["next_cursor"])

    def test_cursor_cannot_cross_topic_or_depth_or_contain_arbitrary_text(self) -> None:
        cursor = build_interview("experience", 1)["questions"][0]["id"]
        for arguments in ({"topic": "research", "after": cursor},
                          {"topic": "experience", "depth": 2, "after": cursor},
                          {"after": "unknown.private-candidate-answer"},
                          {"after": ""}, {"after": 1}, {"after": []}):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(ValueError, "^Interview cursor must identify") as error:
                    build_interview(**arguments)
                self.assertNotIn("private-candidate-answer", str(error.exception))

    def test_invalid_bounds_and_type_aliases_fail_without_coercion(self) -> None:
        invalid = (
            {"topic": ""}, {"topic": "All"}, {"topic": "unknown"},
            {"topic": None}, {"topic": []}, {"depth": 0}, {"depth": 4},
            {"depth": True}, {"depth": 1.0}, {"depth": "1"},
            {"limit": 0}, {"limit": -1}, {"limit": 11}, {"limit": True},
            {"limit": 3.0}, {"limit": "3"},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                build_interview(**arguments)
        self.assertEqual(len(build_interview(limit=1)["questions"]), 1)
        self.assertEqual(len(build_interview(limit=10)["questions"]), 10)

    def test_response_is_json_safe_with_explicit_no_storage_and_closed_question_shape(self) -> None:
        result = build_interview("education", 3)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["catalog_version"], 1)
        self.assertIs(result["answers_stored"], False)
        self.assertIs(result["profile_read"], False)
        for question in result["questions"]:
            self.assertEqual(set(question), {"id", "topic", "depth", "prompt", "why"})
            self.assertTrue(question["prompt"].endswith("?"))
            self.assertTrue(question["why"])
        guidance = " ".join(result["guidance"])
        self.assertIn("skip", guidance)
        self.assertIn("stop", guidance)
        self.assertIn("does not save answers", guidance)
        self.assertIn("exact-source import", guidance)
        self.assertIn("explicit approval", guidance)
        self.assertIn("does not establish a complete profile", guidance)

    def test_independent_calls_are_deterministic_and_caller_mutation_cannot_change_catalogue(self) -> None:
        original = build_interview()
        changed = build_interview()
        changed["questions"][0]["prompt"] = "Untrusted caller replacement"
        changed["questions"].clear()
        changed["guidance"].clear()
        self.assertEqual(build_interview(), original)

    def test_plan_reads_no_files_profile_environment_or_network(self) -> None:
        with ExitStack() as stack:
            for target in ("builtins.open", "os.open", "os.getenv", "pathlib.Path.read_text",
                           "pathlib.Path.write_text", "sqlite3.connect", "socket.create_connection"):
                stack.enter_context(patch(target, side_effect=AssertionError("Unexpected external access")))
            result = build_interview("projects", 3, limit=1)
            next_page = build_interview("projects", 3, after=result["next_cursor"])
        self.assertEqual(len(result["questions"]), 1)
        self.assertTrue(next_page["finished"])

    def test_questions_do_not_request_sensitive_eligibility_or_identity_attestations(self) -> None:
        forbidden = ("work authorization", "sponsorship", "citizenship", "security clearance",
                     "criminal", "disability", "veteran status", "race", "ethnicity",
                     "government identifier", "social security", "passport", "password",
                     "electronic signature", "date of birth")
        for topic in INTERVIEW_TOPICS:
            for depth in (1, 2, 3):
                result = build_interview(topic, depth, limit=10)
                for question in result["questions"]:
                    for phrase in forbidden:
                        with self.subTest(question=question["id"], phrase=phrase):
                            self.assertNotIn(phrase, question["prompt"].lower())

    def test_deep_questions_preserve_ownership_metrics_status_and_uncertainty(self) -> None:
        checks = (
            ("experience", 2, "personally"), ("experience", 3, "measured"),
            ("research", 3, "limitations"), ("projects", 2, "prototype"),
            ("publications", 1, "preprint"), ("education", 2, "completion status"),
            ("skills", 3, "limited experience"), ("preferences", 3, "temporary"),
        )
        for topic, depth, phrase in checks:
            with self.subTest(topic=topic, depth=depth):
                prompts = " ".join(q["prompt"] for q in build_interview(topic, depth)["questions"])
                self.assertIn(phrase, prompts)
