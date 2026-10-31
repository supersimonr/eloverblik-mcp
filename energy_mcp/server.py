"""Read-only local MCP; no token is loaded by this process."""
from mcp.server.fastmcp import FastMCP
from .core import Store

mcp = FastMCP('Eloverblik local', instructions=(
    'Read Danish electricity consumption already synced locally. Dates are Copenhagen dates, '
    'start inclusive/end exclusive. Always report coverage and estimates. Missing data is not zero. '
    'No prices, CO2, battery forecasts or live readings are supplied. Use get_data_status first.'))
READ = {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}


@mcp.tool(annotations=READ)
def get_data_status() -> dict:
    """List locally stored meters, history bounds and last sync; does not contact Eloverblik."""
    return Store().status()


@mcp.tool(annotations=READ)
def get_consumption(meter_id: str, start: str, end: str, group: str = 'day') -> dict:
    """Consumption in kWh and coverage. YYYY-MM-DD dates, end exclusive, max 366 days.

    group is hour/day/month. Hourly labels include UTC offsets to distinguish DST hours.
    """
    return Store().query(meter_id, start, end, group)


@mcp.tool(annotations=READ)
def compare_periods(meter_id: str, start_a: str, end_a: str, start_b: str, end_b: str) -> dict:
    """Compare total kWh; percentages withheld if coverage is partial or baseline is zero.

    Different duration periods should be compared using kWh per covered hour.
    """
    store = Store()
    a, b = store.query(meter_id, start_a, end_a), store.query(meter_id, start_b, end_b)
    complete = a['coverage_pct'] == 100 and b['coverage_pct'] == 100
    return {'a': a, 'b': b,
            'a_kwh_per_covered_hour': a['total_kwh']/a['covered_hours'] if a['covered_hours'] else None,
            'b_kwh_per_covered_hour': b['total_kwh']/b['covered_hours'] if b['covered_hours'] else None,
            'change_b_vs_a_pct': (100*(b['total_kwh']/a['total_kwh']-1)) if complete and a['total_kwh'] else None}


@mcp.tool(annotations=READ)
def get_hourly_profile(meter_id: str, start: str, end: str) -> dict:
    """Mean kWh per fully observed clock-hour in Copenhagen. Partial hours are excluded."""
    data = Store().query(meter_id, start, end, 'hour')
    hours = {h: [] for h in range(24)}
    for bucket in data['buckets']:
        if bucket['covered_hours'] == 1:
            hours[int(bucket['period'][11:13])].append(bucket['kwh'])
    return {'coverage_pct': data['coverage_pct'], 'warning': data['warning'],
            'timezone': data['timezone'], 'includes_estimates': True,
            'hours': [{'hour': h, 'observations': len(v), 'mean_kwh': sum(v)/len(v) if v else None}
                      for h,v in hours.items()]}


def main():
    # STDIO only: no unauthenticated public HTTP endpoint.
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
