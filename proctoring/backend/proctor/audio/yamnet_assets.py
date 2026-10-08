"""Pinned MediaPipe YAMNet and its embedded label map. No network access at runtime."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

from .assets import model_dir

MODEL_NAME = "yamnet.tflite"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite"
MODEL_SHA256 = "4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf"
MODEL_VERSION = "mediapipe-yamnet-float32-1"
DOCUMENTATION_URL = "https://ai.google.dev/edge/mediapipe/solutions/audio/audio_classifier"
LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0.txt"
MANIFEST_NAME = "yamnet.manifest.json"
LABEL_FILE = "yamnet_label_list.txt"
SPEECH_CLASSES = {
    "Speech": "speech",
    "Child speech, kid speaking": "child_speech",
    "Conversation": "conversation",
    "Narration, monologue": "narration",
    "Babbling": "babbling",
    "Whispering": "whispering",
}
# These requested names do not exist in this model's 521-class metadata; no fabricated indices.
ABSENT_REQUESTED_CLASSES = ("Male speech, man speaking", "Female speech, woman speaking")


def labels_from_model(data: bytes) -> list[str]:
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError("YAMNet SHA256 mismatch")
    if b"Apache License. Version 2.0 http://www.apache.org/licenses/LICENSE-2.0." not in data:
        raise ValueError("YAMNet embedded license missing")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        labels = archive.read(LABEL_FILE).decode("utf-8").splitlines()
    if len(labels) != 521 or len(set(labels)) != 521 or not SPEECH_CLASSES.keys() <= set(labels):
        raise ValueError("unexpected YAMNet label map")
    return labels


def check(directory: Path | None = None) -> Path:
    directory = directory if directory is not None else model_dir()
    manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    expected = {"sha256": MODEL_SHA256, "version": MODEL_VERSION, "source": MODEL_URL, "license": "Apache-2.0"}
    if any(manifest.get(k) != value for k, value in expected.items()):
        raise ValueError("unexpected YAMNet manifest")
    path = directory / MODEL_NAME
    labels_from_model(path.read_bytes())
    return path
