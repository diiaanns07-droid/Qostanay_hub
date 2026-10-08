import json
import numpy as np
import pytest

from proctor.audio import yamnet_assets as assets
from proctor.audio.yamnet import YamnetClassifier, speech_score


@pytest.mark.parametrize("name", list(assets.SPEECH_CLASSES))
def test_each_allowed_speech_class(name):
    result = speech_score({name: 0.4, "Music": 0.99})
    assert result.speech_sum == 0.4 and result.top_class == name


@pytest.mark.parametrize("noise", ["Music", "Silence", "Typing", "Computer keyboard", "Mouse click",
    "Writing", "Rustle", "Air conditioning", "Mechanical fan", "Vehicle", "Speech synthesizer", "Singing"])
def test_other_classes_do_not_contribute(noise):
    assert speech_score({noise: 1.0}).speech_sum == 0.0


def test_overlapping_speech_scores_clipped_for_contract_unit():
    assert speech_score({"Speech": 0.8, "Conversation": 0.7}).speech_sum == 1.0
    for value in [float("nan"), float("inf"), -0.1, 1.1]:
        with pytest.raises(ValueError):
            speech_score({"Speech": value})


def test_tampered_model_and_manifest_rejected(tmp_path):
    (tmp_path / assets.MANIFEST_NAME).write_text(json.dumps({"source": assets.MODEL_URL,
        "sha256": assets.MODEL_SHA256, "version": assets.MODEL_VERSION, "license": "Apache-2.0"}))
    (tmp_path / assets.MODEL_NAME).write_bytes(b"changed model")
    with pytest.raises(ValueError, match="SHA256"):
        assets.check(tmp_path)
    (tmp_path / assets.MANIFEST_NAME).write_text('{}')
    with pytest.raises(ValueError, match="manifest"):
        assets.check(tmp_path)


def test_models_dir_configuration(monkeypatch, tmp_path):
    from proctor.audio.assets import model_dir
    monkeypatch.setenv("QORGAU_MODELS_DIR", str(tmp_path))
    assert model_dir() == tmp_path / "audio"


def test_installed_mediapipe_model_and_embedded_classes():
    try:
        path = assets.check()
    except (OSError, ValueError, RuntimeError):
        pytest.skip("explicit prepare --download --model yamnet needed")
    labels = assets.labels_from_model(path.read_bytes())
    assert {name: labels.index(name) for name in assets.SPEECH_CLASSES} == {
        "Speech": 0, "Child speech, kid speaking": 1, "Conversation": 2,
        "Narration, monologue": 3, "Babbling": 4, "Whispering": 12}
    assert all(name not in labels for name in assets.ABSENT_REQUESTED_CLASSES)
    with YamnetClassifier(path.parent) as classifier:
        result = classifier.classify(np.zeros(15600, dtype=np.float32))
        assert 0 <= result.speech_sum < 0.3
        with pytest.raises(ValueError):
            classifier.classify(np.zeros(512))
