import asyncio
import logging
from abc import ABC, abstractmethod, abstractclassmethod
from datetime import timedelta
from decimal import Decimal
from typing import Tuple

import pandas as pd

from util import DEFAULT_TZ, async_retry_getter, now_time


def get_from_filters(filters, filter_type, field_name):
    for f in filters:
        if f['filterType'] == filter_type:
            return f[field_name]


class BinanceMarketApi(ABC):
    '''
    BinanceMarketApi 类为抽象类
    '''

    # 每次最多获取的K线数量
    MAX_ONCE_CANDLES = 1500

    # 最省权重的K线数量，https://binance-docs.github.io/apidocs/futures/en/#kline-candlestick-data
    WEIGHT_EFFICIENT_ONCE_CANDLES = 499

    # 每分钟权重上限
    MAX_MINUTE_WEIGHT = 2400

    def __init__(self, aiohttp_session, candle_close_timeout_sec):
        '''
        构造函数，接收 aiohttp Session 和K线闭合超时时间 candle_close_timeout_sec
        '''
        self.session = aiohttp_session
        self.candle_close_timeout_sec = candle_close_timeout_sec

    @abstractclassmethod
    def parse_syminfo(cls, info):
        '''
        抽象函数，解析 exchange info 中每个 symbol 交易规则，币U本位有所不同
        '''
        pass

    @abstractmethod
    async def aioreq_timestamp_and_weight(self) -> Tuple[int, int]:
        '''
        抽象函数, /time 接口具体 http 调用
        '''
        pass

    @abstractmethod
    async def aioreq_candle(self, symbol, interval, **kwargs) -> list:
        '''
        抽象函数, /klines 接口具体 http 调用
        '''
        pass

    @abstractmethod
    async def aioreq_exchange_info(self) -> dict:
        '''
        抽象函数, /exchangeInfo 接口具体 http 调用
        '''
        pass

    @abstractmethod
    async def aioreq_premium_index(self) -> list:
        '''
        抽象函数, /premiumIndex 接口具体 http 调用
        '''
        pass

    async def get_timestamp_and_weight(self) -> Tuple[pd.Timestamp, int]:
        '''
        从 /time 接口的返回值中，解析出当前服务器时间和已消耗权重
        '''
        ts, wei = await async_retry_getter(self.aioreq_timestamp_and_weight)
        ts = pd.to_datetime(ts, unit='ms', utc=True).astimezone(DEFAULT_TZ)
        return ts, wei

    async def get_funding_rate(self) -> pd.DataFrame:
        data = await self.aioreq_premium_index()
        # 如果 lastFundingRate 不能转换为浮点数，则转换为 nan
        data = [{
            'symbol': d['symbol'],
            'fundingRate': pd.to_numeric(d['lastFundingRate'], errors='coerce')
        } for d in data]
        df = pd.DataFrame.from_records(data)
        return df

    async def get_candle(self, symbol, interval, **kwargs) -> pd.DataFrame:
        '''
        从 /klines 接口返回值中，解析出K线数据并转换为 dataframe
        '''
        data = await async_retry_getter(lambda: self.aioreq_candle(symbol, interval, **kwargs))
        columns = [
            'candle_begin_time',
            'open',
            'high',
            'low',
            'close',
            'volume',
            'close_time',
            'quote_volume',
            'trade_num',
            'taker_buy_base_asset_volume',
            'taker_buy_quote_asset_volume',
            'ignore',
        ]
        df = pd.DataFrame(data, columns=columns, dtype=float)
        df['candle_begin_time'] = pd.to_datetime(df['candle_begin_time'], unit='ms', utc=True).dt.tz_convert(DEFAULT_TZ)
        df['close_time'] = pd.to_datetime(df['close_time'], unit='ms', utc=True).dt.tz_convert(DEFAULT_TZ)
        df.drop(columns='ignore', inplace=True)
        return df

    async def fetch_recent_closed_candle(self, symbol, interval, run_time, limit=5) -> Tuple[pd.DataFrame, bool]:
        '''
        获取 run_time 周期闭合K线，原理为反复获取K线，直到K线闭合或超时
        返回值为 tuple(K线df, 是否闭合布尔值)
        '''
        expire_sec = self.candle_close_timeout_sec
        is_closed = False
        while True:
            df = await self.get_candle(symbol, interval, limit=limit)

            if df['candle_begin_time'].max() >= run_time:
                is_closed = True
                break

            if now_time() - run_time > timedelta(seconds=expire_sec):
                # logging.warning(f'Candle may not closed in {expire_sec}sec {symbol} {interval}')
                break

            await asyncio.sleep(1)
        return df[df['candle_begin_time'] < run_time], is_closed

    async def get_syminfo(self):
        '''
        从 /exchangeinfo 接口的返回值中，解析出当前每个symbol交易规则
        '''
        exg_info = await async_retry_getter(self.aioreq_exchange_info)
        results = dict()
        for info in exg_info['symbols']:
            results[info['symbol']] = self.parse_syminfo(info)
        return results


class BinanceUsdtFutureMarketApi(BinanceMarketApi):

    async def aioreq_timestamp_and_weight(self):
        url = 'https://fapi.binance.com/fapi/v1/time'
        async with self.session.get(url) as resp:
            weight = int(resp.headers['X-MBX-USED-WEIGHT-1M'])
            timestamp = (await resp.json())['serverTime']
        return timestamp, weight

    async def aioreq_candle(self, symbol, interval, **kwargs):
        params = {
            'symbol': symbol,
            'interval': interval,
        }
        params.update(kwargs)
        url = 'https://fapi.binance.com/fapi/v1/klines'

        async with self.session.get(url, params=params) as resp:
            results = await resp.json()
        return results

    async def aioreq_exchange_info(self):
        url = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
        async with self.session.get(url) as resp:
            results = await resp.json()
        return results

    async def aioreq_premium_index(self):
        url = 'https://fapi.binance.com/fapi/v1/premiumIndex'
        async with self.session.get(url) as resp:
            results = await resp.json()
        return results

    @classmethod
    def parse_syminfo(cls, info):
        filters = info['filters']
        return {
            'symbol': info['symbol'],
            'contract_type': info['contractType'],
            'status': info['status'],
            'base_asset': info['baseAsset'],
            'quote_asset': info['quoteAsset'],
            'margin_asset': info['marginAsset'],
            'price_tick': Decimal(get_from_filters(filters, 'PRICE_FILTER', 'tickSize')),
            'face_value': Decimal(get_from_filters(filters, 'LOT_SIZE', 'stepSize')),
            'min_notional_value': Decimal(get_from_filters(filters, 'MIN_NOTIONAL', 'notional'))
        }


class BinanceCoinFutureMarketApi(BinanceMarketApi):

    async def aioreq_timestamp_and_weight(self):
        url = 'https://dapi.binance.com/dapi/v1/time'
        async with self.session.get(url) as resp:
            weight = int(resp.headers['X-MBX-USED-WEIGHT-1M'])
            timestamp = (await resp.json())['serverTime']
        return timestamp, weight

    async def aioreq_candle(self, symbol, interval, **kwargs):
        params = {
            'symbol': symbol,
            'interval': interval,
        }
        params.update(kwargs)
        url = 'https://dapi.binance.com/dapi/v1/klines'

        async with self.session.get(url, params=params) as resp:
            results = await resp.json()
        return results

    async def aioreq_exchange_info(self):
        url = 'https://dapi.binance.com/dapi/v1/exchangeInfo'
        async with self.session.get(url) as resp:
            results = await resp.json()
        return results

    async def aioreq_premium_index(self):
        url = 'https://dapi.binance.com/dapi/v1/premiumIndex'
        async with self.session.get(url) as resp:
            results = await resp.json()
        return results

    @classmethod
    def parse_syminfo(cls, info):
        filters = info['filters']
        return {
            'symbol': info['symbol'],
            'contract_type': info['contractType'],
            'status': info['contractStatus'],
            'base_asset': info['baseAsset'],
            'quote_asset': info['quoteAsset'],
            'margin_asset': info['marginAsset'],
            'price_tick': Decimal(get_from_filters(filters, 'PRICE_FILTER', 'tickSize')),
            'face_value': Decimal(get_from_filters(filters, 'LOT_SIZE', 'stepSize'))
        }

class BinanceUsdtSpotMarketApi(BinanceMarketApi):

    async def aioreq_timestamp_and_weight(self):
        url = 'https://api.binance.com/api/v3/time'
        async with self.session.get(url) as resp:
            weight = int(resp.headers['X-MBX-USED-WEIGHT-1M'])
            timestamp = (await resp.json())['serverTime']
        return timestamp, weight

    async def aioreq_candle(self, symbol, interval, **kwargs):
        params = {
            'symbol': symbol,
            'interval': interval,
        }
        params.update(kwargs)
        url = 'https://api.binance.com/api/v3/klines'

        async with self.session.get(url, params=params) as resp:
            results = await resp.json()
        return results

    async def aioreq_exchange_info(self):
        url = 'https://api.binance.com/api/v3/exchangeInfo'
        async with self.session.get(url) as resp:
            results = await resp.json()
        return results

    async def aioreq_premium_index(self):
        pass

    @classmethod
    def parse_syminfo(cls, info):
        filters = info['filters']
        return {
            'symbol': info['symbol'],
            'status': info['status'],
            'base_asset': info['baseAsset'],
            'quote_asset': info['quoteAsset'],
            'price_tick': Decimal(get_from_filters(filters, 'PRICE_FILTER', 'tickSize')),
            'face_value': Decimal(get_from_filters(filters, 'LOT_SIZE', 'stepSize')),
            'min_notional_value': Decimal(get_from_filters(filters, 'NOTIONAL', 'minNotional'))
        }
