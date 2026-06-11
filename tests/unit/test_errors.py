from applyme.errors import (
    ApplyError,
    AutofillConflict,
    PermanentError,
    RetryableError,
    SolverAuthError,
    SolverUnavailable,
)


def test_retryable_and_permanent_are_apply_errors():
    assert issubclass(RetryableError, ApplyError)
    assert issubclass(PermanentError, ApplyError)


def test_specific_errors_classify_correctly():
    assert issubclass(SolverAuthError, PermanentError)  # bad API key must NOT be retried
    assert issubclass(AutofillConflict, PermanentError)
    assert issubclass(SolverUnavailable, PermanentError)  # delisted/unsolvable provider — never retried
