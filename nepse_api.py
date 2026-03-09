"""
Compatibility shim.

The core NEPSE client has moved to `nepse_core/nepse_api.py`.
Keep importing `NepseAPI` from here if you have existing code.
"""

from nepse_core.nepse_api import NepseAPI

__all__ = ["NepseAPI"]

import os
import requests
import urllib3
from typing import Optional, Dict, Any, List
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://www.nepalstock.com"
API_URL = f"{BASE_URL}/api"

DEFAULT_WASM_PATHS = [
    os.path.join(os.path.dirname(__file__), "nepse.wasm"),
    "/tmp/nepse.wasm",
    os.path.expanduser("~/.nepse/nepse.wasm"),
]

WASM_PATH = None
for path in DEFAULT_WASM_PATHS:
    if os.path.exists(path):
        WASM_PATH = path
        break

DUMMY_DATA = [
    147, 117, 239, 143, 157, 312, 161, 612, 512, 804, 411, 527, 170, 511, 421, 667, 764, 621,
    301, 106, 133, 793, 411, 511, 312, 423, 344, 346, 653, 758, 342, 222, 236, 811, 711, 611,
    122, 447, 128, 199, 183, 135, 489, 703, 800, 745, 152, 863, 134, 211, 142, 564, 375, 793,
    212, 153, 138, 153, 648, 611, 151, 649, 318, 143, 117, 756, 119, 141, 717, 113, 112, 146,
    162, 660, 693, 261, 362, 354, 251, 641, 157, 178, 631, 192, 734, 445, 192, 883, 187, 122,
    591, 731, 852, 384, 565, 596, 451, 772, 624, 691
]

try:
    from wasmtime import Store, Module, Instance
except ImportError:
    Store = Module = Instance = None


class NepseAPI:
    def __init__(self, verify_ssl: bool = False):
        self.session = requests.Session()
        self.session.verify = verify_ssl
        
        retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Referer': f'{BASE_URL}/',
        })
        
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_details: Optional[Dict] = None
        self._market_open_id: Optional[int] = None
        
        self._wasm_store: Optional[Store] = None
        self._wasm_instance: Optional[Instance] = None
    
    def _init_wasm(self) -> None:
        if WASM_PATH is None:
            raise ImportError("nepse.wasm not found. Place it in the same directory as this script or /tmp/")
        
        if self._wasm_store is None:
            self._wasm_store = Store()
            module = Module.from_file(self._wasm_store.engine, WASM_PATH)
            self._wasm_instance = Instance(self._wasm_store, module, [])
    
    def _parse_token(self, token_response: Dict) -> None:
        self._init_wasm()
        
        cdx = self._wasm_instance.exports(self._wasm_store)["cdx"]
        rdx = self._wasm_instance.exports(self._wasm_store)["rdx"]
        bdx = self._wasm_instance.exports(self._wasm_store)["bdx"]
        ndx = self._wasm_instance.exports(self._wasm_store)["ndx"]
        mdx = self._wasm_instance.exports(self._wasm_store)["mdx"]
        
        s1 = token_response['salt1']
        s2 = token_response['salt2']
        s3 = token_response['salt3']
        s4 = token_response['salt4']
        s5 = token_response['salt5']
        
        n = cdx(self._wasm_store, s1, s2, s3, s4, s5)
        l = rdx(self._wasm_store, s1, s2, s4, s3, s5)
        o = bdx(self._wasm_store, s1, s2, s4, s3, s5)
        p = ndx(self._wasm_store, s1, s2, s4, s3, s5)
        q = mdx(self._wasm_store, s1, s2, s4, s3, s5)
        
        access = token_response['accessToken']
        self._access_token = (
            access[:n] + access[n+1:l] + access[l+1:o] + 
            access[o+1:p] + access[p+1:q] + access[q+1:]
        )
        
        i = cdx(self._wasm_store, s2, s1, s3, s5, s4)
        r = rdx(self._wasm_store, s2, s1, s3, s4, s5)
        s = bdx(self._wasm_store, s2, s1, s4, s3, s5)
        t = ndx(self._wasm_store, s2, s1, s4, s3, s5)
        u = mdx(self._wasm_store, s2, s1, s4, s3, s5)
        
        refresh = token_response['refreshToken']
        self._refresh_token = (
            refresh[:i] + refresh[i+1:r] + refresh[r+1:s] + 
            refresh[s+1:t] + refresh[t+1:u] + refresh[u+1:]
        )
        
        self._token_details = {
            'salt1': s1, 'salt2': s2, 'salt3': s3, 'salt4': s4, 'salt5': s5,
            'accessToken': self._access_token,
            'refreshToken': self._refresh_token
        }
    
    def _get_token(self) -> None:
        if self._access_token:
            return
        
        self._init_wasm()
        
        response = self.session.get(f"{API_URL}/authenticate/prove",
            headers={'Origin': BASE_URL, 'Referer': f'{BASE_URL}/'})
        response.raise_for_status()
        data = response.json()
        
        for i in range(1, 6):
            data[f'salt{i}'] = int(data[f'salt{i}'])
        
        self._parse_token(data)
    
    def _get_market_open_id(self) -> int:
        if self._market_open_id is not None:
            return self._market_open_id
        
        self._get_token()
        response = self.session.get(
            f"{API_URL}/nots/nepse-data/market-open",
            headers={'Authorization': f'Salter {self._access_token}'}
        )
        response.raise_for_status()
        data = response.json()
        self._market_open_id = data['id']
        return self._market_open_id
    
    def _calculate_payload(self, which: str = 'default') -> int:
        given_id = self._get_market_open_id()
        today = datetime.now().day
        
        payload_id = DUMMY_DATA[given_id] + given_id + 2 * today
        
        if which == 'stock-live':
            return payload_id
        
        if which == 'sector-live':
            index_value = 3 if payload_id % 10 < 5 else 1
        else:
            index_value = 1 if payload_id % 10 < 5 else 3
        
        payload_id = (
            payload_id
            + self._token_details.get(f"salt{index_value+1}", 0) * today
            - self._token_details.get(f"salt{index_value}", 0)
        )
        
        return payload_id
    
    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self._get_token()
        url = f"{API_URL}{endpoint}"
        headers = {'Authorization': f'Salter {self._access_token}'}
        response = self.session.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    
    def _post(self, endpoint: str, which: str = 'default') -> Any:
        self._get_token()
        url = f"{API_URL}{endpoint}"
        headers = {'Authorization': f'Salter {self._access_token}'}
        payload = {'id': self._calculate_payload(which)}
        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    
    def get_market_status(self) -> Dict[str, Any]:
        data = self._get("/nots/nepse-data/market-open")
        return {"isOpen": data.get("isOpen", "CLOSE") == "OPEN"}
    
    def get_market_summary(self) -> List[Dict]:
        return self._get("/nots/market-summary")
    
    def get_price_volume(self) -> List[Dict]:
        return self._get("/nots/securityDailyTradeStat/58")
    
    def get_today_price(self, page: int = 0, size: int = 500) -> List[Dict]:
        return self._post(f"/nots/nepse-data/today-price?page={page}&size={size}", which='today-price')
    
    def get_supply_demand(self) -> List[Dict]:
        return self._get("/nots/nepse-data/supplydemand")
    
    def get_top_gainers(self) -> List[Dict]:
        return self._get("/nots/top-ten/top-gainer?all=true")
    
    def get_top_losers(self) -> List[Dict]:
        return self._get("/nots/top-ten/top-loser?all=true")
    
    def get_top_turnover(self) -> List[Dict]:
        return self._get("/nots/top-ten/turnover?all=true")
    
    def get_top_volume(self) -> List[Dict]:
        return self._get("/nots/top-ten/trade?all=true")
    
    def get_top_transaction(self) -> List[Dict]:
        return self._get("/nots/top-ten/transaction?all=true")
    
    def get_company_list(self) -> List[Dict]:
        return self._get("/nots/company/list")
    
    def get_security_classification(self) -> List[Dict]:
        return self._get("/nots/security/classification")
    
    def get_floorsheet(self, page: int = 0, size: int = 500) -> List[Dict]:
        return self._post(f"/nots/nepse-data/floorsheet?page={page}&size={size}&sort=contractId,desc", which='floorsheet')
    
    def get_nepse_index(self) -> Dict[str, Any]:
        return self._get("/nots/nepse-index")
    
    def get_index_history(self, index_id: int = 58, start_date: str = None, end_date: str = None) -> List[Dict]:
        if start_date and end_date:
            return self._get(f"/nots/index/history/{index_id}?startDate={start_date}&endDate={end_date}")
        result = self._get(f"/nots/index/history/{index_id}")
        if isinstance(result, dict):
            return result.get('content', [])
        return result
    
    def get_market_cap_by_date(self) -> Dict[str, Any]:
        return self._get("/nots/nepse-data/marcapbydate/")
    
    def get_sector_wise(self) -> List[Dict]:
        return self._get("/nots/sectorwise")
    
    def get_security_details(self, security_id: int) -> Dict[str, Any]:
        return self._get(f"/nots/security/{security_id}")
    
    def get_price_volume_history(
        self,
        security_id: int,
        page: int = 0,
        size: int = 200,
        sort: str = "businessDate,desc",
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"page": page, "size": size}
        if sort:
            params["sort"] = sort
        return self._get(f"/nots/market/security/price/{security_id}", params=params)
    
    def get_trading_history(self, security_id: int, start_date: str, end_date: str) -> List[Dict]:
        return self._get(f"/nots/market/history/security/{security_id}?startDate={start_date}&endDate={end_date}")
    
    def get_index_graph(self, index_id: int = 58) -> Any:
        return self._post(f"/nots/graph/index/{index_id}", which='sector-live')
    
    def get_market_graph_data(self, security_id: int) -> Dict[str, Any]:
        return self._post(f"/nots/market/graphdata/{security_id}")
    
    def get_broker_list(self, page: int = 0, size: int = 500) -> List[Dict]:
        self._get_token()
        url = f"{API_URL}/nots/member"
        headers = {'Authorization': f'Salter {self._access_token}'}
        payload = {
            "memberName": "",
            "contactPerson": "",
            "contactNumber": "",
            "memberCode": "",
            "provinceId": 0,
            "districtId": 0,
            "municipalityId": 0
        }
        response = self.session.post(url, json=payload, headers=headers, params={"page": page, "size": size})
        response.raise_for_status()
        return response.json().get('content', [])
    
    def get_stock_dealers(self) -> List[Dict]:
        return self._get("/nots/member/dealer")
    
    def get_promoter_share(self) -> List[Dict]:
        return self._get("/nots/security/promoters")
    
    def get_indices(self) -> List[Dict]:
        return self._get("/nots/index")
    
    def get_trading_average(self, n_days: int = 120) -> Dict[str, Any]:
        return self._get(f"/nots/nepse-data/trading-average?nDays={n_days}")
    
    def get_market_history(self) -> List[Dict]:
        return self._get("/nots/market-summary-history")
    
    def get_news(self) -> List[Dict]:
        return self._get("/nots/news/companies/disclosure")
    
    def get_live_market(self) -> List[Dict]:
        return self._get("/nots/lives-market")
    
    def get_live_indices(self, index_id: int = 58) -> Any:
        if not (51 <= index_id <= 67):
            raise ValueError(f"'{index_id}' is not a valid index ID. Must be between 51 and 67.")
        return self._post(f"/nots/graph/index/{index_id}", which='sector-live')
    
    def get_sector_live_indices(self, index_id: int = 58) -> Any:
        return self.get_live_indices(index_id)


if __name__ == "__main__":
    api = NepseAPI(verify_ssl=False)
    
   