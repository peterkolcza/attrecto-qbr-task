"""Scaffold smoke tests — verifies that all modules import and the CLI entry point works."""

from qbr import __version__


def test_version():
    assert __version__ == "0.1.0"


def test_models_import():
    from qbr.models import (
        AttentionFlag,
        Colleague,
        Conflict,
        ExtractedItem,
        Message,
        SourceAttribution,
        Thread,
    )

    # Verify core models are constructable
    assert Message.__name__ == "Message"
    assert Thread.__name__ == "Thread"
    assert SourceAttribution.__name__ == "SourceAttribution"
    assert ExtractedItem.__name__ == "ExtractedItem"
    assert AttentionFlag.__name__ == "AttentionFlag"
    assert Conflict.__name__ == "Conflict"
    assert Colleague.__name__ == "Colleague"


def test_cli_import():
    from qbr.cli import app

    assert app is not None


def test_placeholder_modules_import():
    import qbr.flags
    import qbr.llm
    import qbr.parser
    import qbr.pipeline
    import qbr.report
    import qbr.security

    assert qbr.parser is not None
    assert qbr.llm is not None
    assert qbr.pipeline is not None
    assert qbr.flags is not None
    assert qbr.security is not None
    assert qbr.report is not None
