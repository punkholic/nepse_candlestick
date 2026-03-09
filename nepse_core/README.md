# NEPSE API

Unofficial Python API wrapper for Nepal Stock Exchange (NEPSE) data from nepalstock.com.

## Installation

```bash
pip install requests wasmtime
```

**Required:** Place `nepse.wasm` file in one of these locations:
- Same directory as the script
- `/tmp/nepse.wasm`
- `~/.nepse/nepse.wasm`

## Usage

```python
from nepse_core.nepse_api import NepseAPI

api = NepseAPI(verify_ssl=False)

# Market Data
status = api.get_market_status()
summary = api.get_market_summary()
price_volume = api.get_price_volume()

# Today's Price
today_price = api.get_today_price()

# Top Lists
gainers = api.get_top_gainers()
losers = api.get_top_losers()
turnover = api.get_top_turnover()
volume = api.get_top_volume()
transaction = api.get_top_transaction()

# Companies
companies = api.get_company_list()

# Indices
nepse_index = api.get_nepse_index()
sector_indices = api.get_indices()
index_history = api.get_index_history(58)

# Market Details
supply_demand = api.get_supply_demand()
market_cap = api.get_market_cap_by_date()
sector_wise = api.get_sector_wise()

# Security Details
security_details = api.get_security_details(8045)  # by ID
price_history = api.get_price_volume_history(8045)

# Brokers
brokers = api.get_broker_list()

# Misc
trading_average = api.get_trading_average(120)
market_history = api.get_market_history()
news = api.get_news()
floorsheet = api.get_floorsheet()
```

## API Methods

| Method | Description |
|--------|-------------|
| `get_market_status()` | Market open/close status |
| `get_market_summary()` | Daily market summary |
| `get_price_volume()` | Price & volume data |
| `get_today_price()` | Today's prices |
| `get_supply_demand()` | Supply & demand |
| `get_top_gainers()` | Top gaining stocks |
| `get_top_losers()` | Top losing stocks |
| `get_top_turnover()` | Top turnover stocks |
| `get_top_volume()` | Top volume stocks |
| `get_top_transaction()` | Top transaction stocks |
| `get_company_list()` | List of all companies |
| `get_security_classification()` | Security classification |
| `get_floorsheet()` | Today's floorsheet |
| `get_nepse_index()` | NEPSE index data |
| `get_index_history(id)` | Index history |
| `get_market_cap_by_date()` | Market cap by date |
| `get_sector_wise()` | Sector-wise data |
| `get_security_details(id)` | Security details |
| `get_price_volume_history(id)` | Price/volume history |
| `get_trading_history(id, start, end)` | Trading history |
| `get_index_graph(id)` | Index graph data |
| `get_market_graph_data(id)` | Market graph data |
| `get_broker_list()` | Broker list |
| `get_stock_dealers()` | Stock dealers |
| `get_promoter_share()` | Promoter share data |
| `get_indices()` | All indices |
| `get_trading_average(n_days)` | Trading average |
| `get_market_history()` | Market history |
| `get_news()` | Company disclosures |
| `get_live_market()` | Live market data |
| `get_sector_live_indices()` | Live sector indices |

## Technical Details

This API handles NEPSE's custom authentication:
- Token parsing via WebAssembly (nepse.wasm)
- Payload calculation for POST requests

The nepse.wasm file is required and handles the token decryption that NEPSE uses to protect their API.

## Disclaimer

This is an unofficial API wrapper. Not affiliated with NEPSE or nepalstock.com. Use at your own risk.



quant finance 
random forests