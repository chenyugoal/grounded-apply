from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from grounded_apply.services.source_rotation import (
    SOURCE_ROTATION_POLICY, SourceRotation, source_indices, validate_rotation_ledger,
    validate_source_rotation,
)


class SourceRotationTests(unittest.TestCase):
    def test_legacy_order_is_exact_even_when_manual_gaps_are_first(self) -> None:
        self.assertEqual(source_indices(("manual", "greenhouse", "manual", "ashby"), None), (0, 1, 2, 3))

    def test_automatic_sources_rotate_and_manual_gaps_keep_declared_order(self) -> None:
        providers = ("manual", "greenhouse", "manual", "ashby", "lever_eu")
        expected = ((1, 3, 4, 0, 2), (3, 4, 1, 0, 2), (4, 1, 3, 0, 2), (1, 3, 4, 0, 2))
        for generation, order in enumerate(expected, 1):
            with self.subTest(generation=generation):
                self.assertEqual(source_indices(providers, SourceRotation(generation, generation + 10)), order)

    def test_one_draft_slot_reaches_every_source_despite_four_candidate_slots(self) -> None:
        providers = ("greenhouse",) * 32
        first_opportunities = []
        for generation in range(1, 33):
            order = source_indices(providers, SourceRotation(generation, generation))
            self.assertEqual(set(order), set(range(32)))
            captured = order[:4]
            first_opportunities.append(captured[0])
        # Even if the first four candidates are all persistent blockers, a
        # source beyond today's capture cap receives tomorrow's first slot.
        self.assertEqual(first_opportunities, list(range(32)))

    def test_manual_sources_do_not_spend_automatic_rotation_opportunities(self) -> None:
        providers = ("manual", "greenhouse", "manual", "ashby", "manual")
        self.assertEqual([source_indices(providers, SourceRotation(i, i))[0] for i in range(1, 5)], [1, 3, 1, 3])

    def test_all_manual_and_single_automatic_scopes_have_stable_order(self) -> None:
        self.assertEqual(source_indices(("manual", "manual"), SourceRotation(4, 20)), (0, 1))
        self.assertEqual(source_indices(("manual", "netflix", "manual"), SourceRotation(4, 20)), (1, 0, 2))

    def test_retries_keep_order_after_future_reservations_or_retry_epoch_gaps(self) -> None:
        providers = ("greenhouse", "ashby", "lever")
        first = SourceRotation(1, 4)
        order = source_indices(providers, first)
        ledger = validate_rotation_ledger((SourceRotation(3, 18), first, SourceRotation(2, 9)), lease_epoch=24)
        self.assertEqual([value.generation for value in ledger], [1, 2, 3])
        self.assertEqual(source_indices(providers, first), order)
        self.assertEqual(source_indices(providers, ledger[1]), (1, 2, 0))

    def test_largest_counter_is_bounded_and_order_uses_generation_not_epoch(self) -> None:
        self.assertEqual(source_indices(("greenhouse", "ashby", "lever"), SourceRotation(2, 1_000_000_000)), (1, 2, 0))
        self.assertEqual(source_indices(("greenhouse",), SourceRotation(1_000_000_000, 1_000_000_000)), (0,))

    def test_model_round_trip_is_closed_detached_and_immutable(self) -> None:
        value = SourceRotation(1, 3)
        shape = value.to_dict()
        self.assertEqual(shape, {"schema_version": 1, "policy": SOURCE_ROTATION_POLICY, "generation": 1, "reserved_epoch": 3})
        self.assertEqual(validate_source_rotation(shape), value)
        shape["generation"] = 2
        self.assertEqual(value.generation, 1)
        with self.assertRaises(FrozenInstanceError):
            value.generation = 2

    def test_bad_counter_and_envelope_shapes_fail_closed(self) -> None:
        for generation, epoch in ((0, 1), (1, 0), (2, 1), (-1, 1), (True, 1), (1, True), (1.0, 1), (1, "2"),
            (1, 1_000_000_001), (1_000_000_001, 1_000_000_001)):
            with self.subTest(generation=generation, epoch=epoch), self.assertRaises(ValueError):
                SourceRotation(generation, epoch)
        valid = SourceRotation(1, 1).to_dict()
        for value in (None, [], {**valid, "unknown": 1}, {key: item for key, item in valid.items() if key != "policy"},
            {**valid, "schema_version": True}, {**valid, "schema_version": 2}, {**valid, "policy": "source_rotation@2"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_source_rotation(value)

    def test_ledger_rejects_duplicate_gap_reused_epoch_reverse_epoch_and_future_epoch(self) -> None:
        cases = (
            (SourceRotation(1, 1), SourceRotation(1, 2)),
            (SourceRotation(2, 2),),
            (SourceRotation(1, 1), SourceRotation(3, 3)),
            (SourceRotation(1, 3), SourceRotation(2, 3)),
            (SourceRotation(1, 4), SourceRotation(2, 3)),
            (SourceRotation(1, 5),),
        )
        for reservations in cases:
            with self.subTest(reservations=reservations), self.assertRaises(ValueError):
                validate_rotation_ledger(reservations, lease_epoch=4)

    def test_ledger_empty_history_and_legacy_lease_epochs_are_valid(self) -> None:
        self.assertEqual(validate_rotation_ledger((), lease_epoch=0), ())
        self.assertEqual(validate_rotation_ledger((), lease_epoch=12), ())
        self.assertEqual(validate_rotation_ledger((SourceRotation(1, 13),), lease_epoch=13), (SourceRotation(1, 13),))
        for values, epoch in (([], 0), (({},), 1), ((), -1), ((), True), ((), "1")):
            with self.subTest(values=values, epoch=epoch), self.assertRaises(ValueError):
                validate_rotation_ledger(values, lease_epoch=epoch)

    def test_provider_shape_and_source_budget_are_validated_without_expanding_routes(self) -> None:
        for providers in ((), ("greenhouse",) * 33, ["greenhouse"], (None,), (True,), ("",), ("../manual",),
            ("manual\n",), ("MANUAL",), ("x" * 65,)):
            with self.subTest(providers=providers), self.assertRaises(ValueError):
                source_indices(providers, None)
        with self.assertRaises(ValueError):
            source_indices(("greenhouse",), SourceRotation(1, 1).to_dict())


if __name__ == "__main__":
    unittest.main()
