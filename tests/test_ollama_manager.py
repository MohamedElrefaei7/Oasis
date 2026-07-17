"""Tests for oasis.llm.manager — ensure_ollama() lifecycle helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from oasis.llm.manager import _model_available, _server_running, _start_server, ensure_ollama
from oasis.llm.ollama import DEFAULT_MODEL, OllamaProvider

# ---------------------------------------------------------------------------
# _server_running
# ---------------------------------------------------------------------------


def test_server_running_returns_true_on_200() -> None:
    with patch("oasis.llm.manager.urllib.request.urlopen"):
        assert _server_running() is True


def test_server_running_returns_false_on_error() -> None:
    with patch("oasis.llm.manager.urllib.request.urlopen", side_effect=OSError):
        assert _server_running() is False


def test_server_running_returns_false_on_connection_refused() -> None:
    import urllib.error

    with patch(
        "oasis.llm.manager.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        assert _server_running() is False


# ---------------------------------------------------------------------------
# _model_available
# ---------------------------------------------------------------------------


def _make_run(stdout: str):
    m = MagicMock()
    m.stdout = stdout
    return m


def test_model_available_true_when_in_list() -> None:
    with patch("oasis.llm.manager.subprocess.run", return_value=_make_run("llama3.2:3b   abc123\n")):
        assert _model_available("llama3.2:3b") is True


def test_model_available_false_when_not_in_list() -> None:
    with patch("oasis.llm.manager.subprocess.run", return_value=_make_run("phi3:mini\n")):
        assert _model_available("llama3.2:3b") is False


def test_model_available_false_on_subprocess_error() -> None:
    with patch("oasis.llm.manager.subprocess.run", side_effect=FileNotFoundError):
        assert _model_available("llama3.2:3b") is False


def test_model_available_false_on_timeout() -> None:
    import subprocess

    with patch("oasis.llm.manager.subprocess.run", side_effect=subprocess.TimeoutExpired("ollama", 5)):
        assert _model_available("llama3.2:3b") is False


# ---------------------------------------------------------------------------
# _start_server
# ---------------------------------------------------------------------------


def test_start_server_returns_false_when_binary_missing() -> None:
    with patch("oasis.llm.manager.shutil.which", return_value=None):
        assert _start_server() is False


def test_start_server_spawns_ollama_serve() -> None:
    with patch("oasis.llm.manager.shutil.which", return_value="/usr/bin/ollama"), \
         patch("oasis.llm.manager.subprocess.Popen") as mock_popen, \
         patch("oasis.llm.manager._server_running", return_value=True):
        _start_server()
    mock_popen.assert_called_once()
    args = mock_popen.call_args[0][0]
    assert args == ["ollama", "serve"]


def test_start_server_returns_true_when_server_comes_up() -> None:
    with patch("oasis.llm.manager.shutil.which", return_value="/usr/bin/ollama"), \
         patch("oasis.llm.manager.subprocess.Popen"), \
         patch("oasis.llm.manager._server_running", return_value=True):
        assert _start_server() is True


def test_start_server_returns_false_when_server_never_comes_up() -> None:
    with patch("oasis.llm.manager.shutil.which", return_value="/usr/bin/ollama"), \
         patch("oasis.llm.manager.subprocess.Popen"), \
         patch("oasis.llm.manager._server_running", return_value=False), \
         patch("oasis.llm.manager._STARTUP_TIMEOUT", 0.0):
        assert _start_server() is False


def test_start_server_uses_devnull_for_output() -> None:
    import subprocess

    with patch("oasis.llm.manager.shutil.which", return_value="/usr/bin/ollama"), \
         patch("oasis.llm.manager.subprocess.Popen") as mock_popen, \
         patch("oasis.llm.manager._server_running", return_value=True):
        _start_server()
    _, kwargs = mock_popen.call_args
    assert kwargs.get("stdout") == subprocess.DEVNULL
    assert kwargs.get("stderr") == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# ensure_ollama
# ---------------------------------------------------------------------------


def _patch_all(*, server: bool, start: bool, model: bool):
    """Return a context manager that patches the three internal helpers."""
    return (
        patch("oasis.llm.manager._server_running", return_value=server),
        patch("oasis.llm.manager._start_server", return_value=start),
        patch("oasis.llm.manager._model_available", return_value=model),
    )


def test_ensure_ollama_returns_provider_when_all_ready() -> None:
    with patch("oasis.llm.manager._server_running", return_value=True), \
         patch("oasis.llm.manager._model_available", return_value=True), \
         patch("oasis.llm.ollama.instructor.from_openai"), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        result = ensure_ollama()
    assert isinstance(result, OllamaProvider)


def test_ensure_ollama_returns_none_when_server_wont_start() -> None:
    with patch("oasis.llm.manager._server_running", return_value=False), \
         patch("oasis.llm.manager._start_server", return_value=False):
        assert ensure_ollama() is None


def test_ensure_ollama_starts_server_when_not_running() -> None:
    with patch("oasis.llm.manager._server_running", return_value=False), \
         patch("oasis.llm.manager._start_server", return_value=True) as mock_start, \
         patch("oasis.llm.manager._model_available", return_value=True), \
         patch("oasis.llm.ollama.instructor.from_openai"), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        ensure_ollama()
    mock_start.assert_called_once()


def test_ensure_ollama_does_not_start_when_already_running() -> None:
    with patch("oasis.llm.manager._server_running", return_value=True), \
         patch("oasis.llm.manager._start_server") as mock_start, \
         patch("oasis.llm.manager._model_available", return_value=True), \
         patch("oasis.llm.ollama.instructor.from_openai"), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        ensure_ollama()
    mock_start.assert_not_called()


def test_ensure_ollama_returns_none_when_model_not_available() -> None:
    with patch("oasis.llm.manager._server_running", return_value=True), \
         patch("oasis.llm.manager._model_available", return_value=False):
        assert ensure_ollama() is None


def test_ensure_ollama_uses_default_model() -> None:
    with patch("oasis.llm.manager._server_running", return_value=True), \
         patch("oasis.llm.manager._model_available", return_value=True) as mock_avail, \
         patch("oasis.llm.ollama.instructor.from_openai"), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        ensure_ollama()
    mock_avail.assert_called_once_with(DEFAULT_MODEL)


def test_ensure_ollama_passes_model_to_provider() -> None:
    with patch("oasis.llm.manager._server_running", return_value=True), \
         patch("oasis.llm.manager._model_available", return_value=True), \
         patch("oasis.llm.ollama.instructor.from_openai"), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        result = ensure_ollama(model="phi3:mini")
    assert result is not None
    assert result._model == "phi3:mini"


def test_ensure_ollama_custom_model_checked_for_availability() -> None:
    with patch("oasis.llm.manager._server_running", return_value=True), \
         patch("oasis.llm.manager._model_available", return_value=False) as mock_avail:
        ensure_ollama(model="phi3:mini")
    mock_avail.assert_called_once_with("phi3:mini")
