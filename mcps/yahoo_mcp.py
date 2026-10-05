# /// script
# requires-python = ">=3.11"
# dependencies = ["yahoo-finance-mcp==0.1.2", "mcp[cli]==1.30.0"]
# ///
"""Locked upstream Yahoo entrypoint; no repository account or credentials access."""
from yahoo_finance_mcp.server import main

if __name__ == '__main__':
    main()
