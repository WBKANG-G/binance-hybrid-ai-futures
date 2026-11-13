"""Synthetic cancelled-exposure receipt and local intent/effect snapshots."""


def seed(value, stamp):
    import copy
    from datetime import timedelta

    operation = "cd07228d-1f31-4b26-bfe9-0fd2e61eabcc"
    current = value["orders"]["2"]
    current.update(
        state="CANCELED",
        filled="0",
        remaining="0",
        open_quantity="0",
        actual_risk="0",
        position_state="CLOSED",
        exit_state="UNKNOWN",
    )
    original = copy.deepcopy(current)
    original.update(
        open_quantity=current["quantity"], actual_risk="1", position_state="EXIT_PENDING"
    )
    base = copy.deepcopy(value)
    base["orders"]["2"] = copy.deepcopy(original)
    base["requests"].pop("synthetic-sl-2", None)
    base["requests"].pop("synthetic-exit-2", None)
    images = []
    images.append(("DEMO_ENTRY_EFFECT", stamp - timedelta(seconds=4), copy.deepcopy(base)))
    base["requests"]["synthetic-sl-2"] = "SUBMITTING"
    images.append(("DEMO_SL_INTENT", stamp - timedelta(seconds=3), copy.deepcopy(base)))
    base["requests"]["synthetic-sl-2"] = "UNKNOWN"
    images.append(("DEMO_SL_EFFECT", stamp - timedelta(seconds=2), copy.deepcopy(base)))
    base["requests"]["synthetic-exit-2"] = "SUBMITTING"
    images.append(("DEMO_REDUCE_ONLY_INTENT", stamp - timedelta(seconds=1), copy.deepcopy(base)))
    value["cancelled_exposure_corrections"][operation] = dict(
        review_token="1" * 64,
        evidence_hash="2" * 64,
        exchange_fingerprint="3" * 64,
        corrected_at=stamp.isoformat(),
        original_orders={"2": original},
    )
    value["operations"][operation] = "CORRECT_CANCELLED_UNFILLED_EXPOSURE"
    acknowledge = "dd07228d-1f31-4b26-bfe9-0fd2e61eabcc"
    value["recovery_receipts"][acknowledge] = "4" * 64
    value["operations"][acknowledge] = "ACKNOWLEDGE_FLAT_EMERGENCY"
    images.append(("DEMO_CANCELLED_EXPOSURE_CORRECTED", stamp, copy.deepcopy(value)))
    return images
