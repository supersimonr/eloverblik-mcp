import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import date
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from energy_mcp.core import Store

ROOT = Path(__file__).resolve().parents[1]
METER = '123456789012345678'


class MCPTests(unittest.TestCase):
    def test_real_stdio_roundtrip(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                db = str(Path(tmp)/'readings.sqlite3')
                store = Store(db)
                store.replace(METER,date(2025,1,1),date(2025,1,2),
                              [(METER,'2025-01-01T00:00:00+00:00',3600,2.5,'A04')])
                env = {**os.environ, 'ELOVERBLIK_DB':db}
                env.pop('ELOVERBLIK_REFRESH_TOKEN',None)
                params = StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_mcp.py')],env=env,cwd=tmp)
                async with stdio_client(params) as (read,write):
                    async with ClientSession(read,write) as session:
                        await session.initialize()
                        listing=await session.list_tools()
                        self.assertEqual(len(listing.tools),4)
                        self.assertTrue(all(t.annotations.readOnlyHint for t in listing.tools))
                        status=await session.call_tool('get_data_status',{})
                        self.assertFalse(status.isError)
                        self.assertNotIn('SECRET',str(status))
                        result=await session.call_tool('get_consumption',{'meter_id':METER,'start':'2025-01-01','end':'2025-01-02'})
                        self.assertFalse(result.isError)
                        data = result.structuredContent or json.loads(result.content[0].text)
                        self.assertEqual(data['total_kwh'],2.5)
                        self.assertLess(data['coverage_pct'],100)
                        bad=await session.call_tool('get_consumption',{'meter_id':METER,'start':'2025-01-02','end':'2025-01-01'})
                        self.assertTrue(bad.isError)
        asyncio.run(run())
