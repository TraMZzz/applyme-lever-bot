from applyme.errors import (
    ApplyError,
    PermanentError,
    RetryableError,
    SolverAuthError,
    SolverTimeout,
    SolverUnavailable,
)


def test_retryable_and_permanent_are_apply_errors():
    assert issubclass(RetryableError, ApplyError)
    assert issubclass(PermanentError, ApplyError)


def test_specific_errors_classify_correctly():
    assert issubclass(SolverAuthError, PermanentError)  # bad API key must NOT be retried
    assert issubclass(SolverTimeout, RetryableError)  # a solver timeout is transient
    assert issubclass(SolverUnavailable, PermanentError)  # delisted/unsolvable provider — never retried
