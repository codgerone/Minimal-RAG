from rag.index.manifest import DocumentEntry
from rag.ingest.timing import StepTimes


def test_step_times_accumulate_per_name_and_survive_exceptions():
    times = StepTimes()
    with times.step("parse"):
        pass
    with times.step("parse"):
        pass
    try:
        with times.step("embed"):
            raise ValueError
    except ValueError:
        pass
    assert set(times.rounded()) == {"parse", "embed"}
    assert all(value >= 0 for value in times.rounded().values())


def test_entries_written_before_timings_still_load():
    entry = DocumentEntry("d", "d.pdf", "d.pdf", "h", "d", 1, 2, 0, "t")
    assert entry.timings == {}
