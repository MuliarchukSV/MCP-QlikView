"""Entry point: ``python -m mcp_qlikview`` and ``mcp-qlikview`` console script."""

from __future__ import annotations


def main() -> None:
    from mcp_qlikview.server import run

    run()


if __name__ == "__main__":
    main()
