import pytest

from proctor.audio.live import phase_report


@pytest.mark.parametrize("phase,present,expected", [
    ("silence", 4, True), ("silence", 5, False),
    ("speech_nearby", 28, True), ("speech_nearby", 27, False),
    ("whisper_1m", 20, None),
])
def test_numeric_criteria_and_operator_confirmation_stay_separate(phase, present, expected):
    rows = [{"voice_like": "present" if i < present else "absent", "voice_probability": 0.6 if i < present else 0.1}
            for i in range(40)]
    diag = [dict(silero_present=i < 10, yamnet_present=i < 20, energy_present=False,
                 final_present=i < present, yamnet_class="Whispering") for i in range(40)]
    result = phase_report(phase, 0, 20000, rows, diag)
    assert result["numeric_criterion_met"] is expected
    assert result["acoustic_conditions"] == "operator confirmation pending"
    assert result["detectors"]["silero"]["fraction"] == 0.25
    assert result["detectors"]["yamnet"]["fraction"] == 0.5
    assert result["yamnet_only_present"] == 10


def test_missing_or_unknown_observations_cannot_pass_silence():
    assert phase_report("silence", 0, 20000, [], [])["numeric_criterion_met"] is None
    unknown = [{"voice_like": "unknown", "voice_probability": None}] * 40
    assert phase_report("silence", 0, 20000, unknown, [])["numeric_criterion_met"] is False
    incomplete = [{"voice_like": "absent", "voice_probability": 0.01}] * 4
    assert phase_report("silence", 0, 20000, incomplete, [])["numeric_criterion_met"] is False
