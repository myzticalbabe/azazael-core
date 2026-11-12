"""Verify the TEST_FAILURE_MANDATE and CONCURRENT_INFERENCE_MANDATE are codified and loadable."""
import os
from pathlib import Path

#: The Codebuff mandate file is the agent client's config, not a repo artifact.
#: It has lived at `<home>/.codebuff/rules.md` and, in one tree layout, two
#: levels above the repo. Resolve it from candidates so the check describes the
#: mandate rather than one machine's directory depth.
_MANDATE_CANDIDATES = (
    Path.home() / ".codebuff" / "rules.md",
    Path(__file__).resolve().parents[1] / ".codebuff" / "rules.md",
    Path(__file__).resolve().parents[2] / ".codebuff" / "rules.md",
)


def _mandate_path():
    for candidate in _MANDATE_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def test_mandate_exists():
    """Verify that both TEST_FAILURE_MANDATE and CONCURRENT_INFERENCE_MANDATE are codified and loadable."""
    file_path = _mandate_path()
    assert file_path is not None, (
        "Mandate file must exist at one of: %s"
        % ", ".join(str(c) for c in _MANDATE_CANDIDATES)
    )

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "TEST_FAILURE_MANDATE" in content, "Mandate must be explicitly defined"
    assert "thinker-with-files-gemini" in content, "Mandate must enforce Gemini usage"
    assert "CONCURRENT_INFERENCE_MANDATE" in content, "CONCURRENT_INFERENCE_MANDATE must be defined"
    assert "spawn_agents" in content, "CONCURRENT_INFERENCE_MANDATE must enforce concurrent spawning"


if __name__ == "__main__":
    test_mandate_exists()
    print("Both mandates verified successfully.")
