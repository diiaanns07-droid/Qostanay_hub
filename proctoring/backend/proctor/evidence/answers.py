"""Validate externally submitted answers against the same local exam as A01."""
from proctor_contracts.interfaces import ProctorError, StorageError
from proctor_contracts.v1 import AnswerUpsert, ErrorCode, ExamDefinition
from proctor.settings import PROCTORING_ROOT, Settings


class InvalidAnswer(ProctorError):
    # Also works on BOOTSTRAP, where base ProctorError still defaults to HTTP 500.
    http_status = 422


def load_exam(settings: Settings) -> ExamDefinition:
    # A01 currently has no public get_exam() in BackendContext. This is its exact
    # documented local source/fallback, not a second exam definition.
    path = settings.exam_path if settings.exam_path.is_file() else (
        PROCTORING_ROOT / "contracts/fixtures/v1/ExamDefinition.demo_min.json")
    try:
        return ExamDefinition.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StorageError(ErrorCode.STORAGE_ERROR, "exam definition is unavailable") from None


def validate_answer(exam: ExamDefinition, exam_id: str, question_id: str, body: AnswerUpsert) -> None:
    if exam.exam_id != exam_id:
        raise InvalidAnswer(ErrorCode.INVALID_ARGUMENT, "answer exam does not match the session")
    question = next((q for q in exam.questions if q.question_id == question_id), None)
    if question is None:
        raise InvalidAnswer(ErrorCode.INVALID_ARGUMENT, "unknown question_id")
    value = body.value
    if question.kind.value == "short_text":
        if not isinstance(value, str) or len(value) > (question.max_length or 4000):
            raise InvalidAnswer(ErrorCode.INVALID_ARGUMENT, "invalid text answer or length")
        return
    if not isinstance(value, list):
        raise InvalidAnswer(ErrorCode.INVALID_ARGUMENT, "choice answer must contain option ids")
    allowed = {option.option_id for option in question.options}
    if any(option not in allowed for option in value) or len(value) != len(set(value)):
        raise InvalidAnswer(ErrorCode.INVALID_ARGUMENT, "unknown or duplicate option_id")
    if question.kind.value == "single_choice" and len(value) > 1:
        raise InvalidAnswer(ErrorCode.INVALID_ARGUMENT, "single_choice accepts at most one option")
