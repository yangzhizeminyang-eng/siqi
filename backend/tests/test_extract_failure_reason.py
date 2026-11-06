"""P0-2：测试失败原因应当是真实错误，而不是被 pip 装配噪音淹没。"""
from app.main import _extract_test_failure_reason


class _R:
    def __init__(self, stdout="", stderr="", exit_code=2):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


def test_prefers_real_error_over_pip_noise():
    r = _R(
        stdout="tests/test_auth_api.py::xxx FAILED\nAssertionError: expected 1 == 2",
        stderr="WARNING: Running pip as the 'root' user can result in broken permissions.\n[notice] A new release of pip is available.",
    )
    reason = _extract_test_failure_reason(r)
    assert "AssertionError" in reason
    assert "Running pip" not in reason


def test_keeps_real_pip_install_error():
    # 真装配失败(ReadTimeout)应当保留, 因为它就是真实失败原因
    r = _R(stdout="", stderr="pip._vendor.urllib3.exceptions.ReadTimeoutError: Read timed out.")
    reason = _extract_test_failure_reason(r)
    assert "ReadTimeoutError" in reason or "Read timed out" in reason


def test_module_not_found_taken_from_stdout():
    r = _R(stdout="ModuleNotFoundError: No module named 'jwt'", stderr="WARNING: pip is being invoked as root")
    reason = _extract_test_failure_reason(r)
    assert "ModuleNotFoundError" in reason
    assert "WARNING" not in reason