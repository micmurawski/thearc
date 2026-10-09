import pytest

from thearcv2.resources import CategoricalScale, CountSummary, LabelScale, NumericScale, NumericSummary

usefulness = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"], ordered=True)


def test_categorical():
    summary = usefulness.summarize([("helpful", 1), ("helpful", 1), ("harmful", 1)])
    assert summary == CountSummary({"harmful": 1, "neutral": 0, "helpful": 2})
    assert usefulness.render(summary) == "(harmful: 1, neutral: 0, helpful: 2)"
    with pytest.raises(ValueError):
        usefulness.validate("great")


def test_no_ratings_means_no_summary():
    assert usefulness.summarize([]) is None


def test_labels_are_normalised_and_counted():
    tags = LabelScale("tags")
    summary = tags.summarize([("Outdated", 1), (" outdated ", 1), ("too  verbose", 1)])
    assert summary.counts == {"outdated": 2, "too verbose": 1}
    assert tags.render(summary) == "(outdated: 2, too verbose: 1)"
    with pytest.raises(ValueError):
        tags.validate("   ")


def test_numeric():
    quality = NumericScale("quality", minimum=0, maximum=1)
    summary = quality.summarize([(0.5, 1), (1, 1)])
    assert summary == NumericSummary(count=2, total=1.5, minimum=0.5, maximum=1.0)
    assert summary.mean == 0.75
    for invalid in (True, 1.5, float("nan"), "0.5"):
        with pytest.raises(ValueError):
            quality.validate(invalid)


@pytest.mark.parametrize(
    ("scale", "values"),
    [
        (usefulness, ["helpful", "harmful", "neutral", "helpful"]),
        (LabelScale("tags"), ["a", "b", "a", "c"]),
        (NumericScale("q"), [0.1, 0.9, 0.4, 0.3]),
    ],
)
def test_summaries_are_mergeable(scale, values):
    left = scale.summarize((value, 1) for value in values[:2])
    right = scale.summarize((value, 1) for value in values[2:])
    assert left.merge(right) == scale.summarize((value, 1) for value in values)


def test_scales_describe_themselves_in_json_schema():
    scale = CategoricalScale("usefulness", {"harmful": "made it worse", "neutral": "", "helpful": "made it better"},
                             ordered=True, description="Effect on the session.")
    assert scale.labels == ("harmful", "neutral", "helpful")
    assert scale.json_schema()["description"] == (
        "Effect on the session.\nOrdered: harmful < neutral < helpful.\n"
        "- harmful: made it worse\n- helpful: made it better")
    assert "description" not in CategoricalScale("plain", ["a", "b"]).json_schema()
    assert NumericScale("score", maximum=5, description="Higher is better.").json_schema()["description"] == (
        "Higher is better.")
    assert LabelScale("tags", description="Free-form tags.").json_schema()["description"] == "Free-form tags."
