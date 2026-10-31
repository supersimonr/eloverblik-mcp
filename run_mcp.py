"""Absolute-path entry point for desktop MCP clients (works from any cwd)."""
import os
from dotenv import dotenv_values
from energy_mcp.core import ROOT

# Forward only DB configuration; never load the Eloverblik token into the environment.
settings = dotenv_values(ROOT / '.env')
if settings.get('ELOVERBLIK_DB'):
    os.environ.setdefault('ELOVERBLIK_DB', settings['ELOVERBLIK_DB'])
del settings
from energy_mcp.server import main

if __name__ == '__main__':
    main()
