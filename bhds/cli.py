import asyncio

from .aws_candle import convert_aws_candle_csv, get_aws_candle, verify_aws_candle
from .quantclass_candle import convert_quantclass_candle_csv
from .compare import compare_aws_quantclass_candle


class Bhds:
    """
    Binance Historical Data Service

    Supports types: spot, usdt_futures, coin_futures
    """

    def get_aws_candle(self, typ, time_interval, *symbols):
        """
        Downloads daily candlestick data from Binance's AWS data center. All available dates will be downloaded.
        """
        for symbol in symbols:
            asyncio.run(get_aws_candle(typ, symbol, time_interval))

    def verify_aws_candle(self, typ, time_interval):
        """
        Verifies the integrity of all AWS candlestick data and deletes incorrect data.
        """
        verify_aws_candle(typ, time_interval)

    def convert_aws_candle_csv(self, typ, time_interval):
        """
        Converts and merges downloaded candlestick data into Pandas Feather format.
        """
        convert_aws_candle_csv(typ, time_interval)

    def convert_quantclass_candle_csv(self, typ, time_interval):
        """
        Converts quantclass candlestick data into Pandas Feather format.
        """
        convert_quantclass_candle_csv(typ, time_interval)

    def compare_aws_quantclass_candle(self, typ, time_interval, *symbols):
        """
        Compare AWS candle with Quantclass
        """
        for symbol in symbols:
            compare_aws_quantclass_candle(typ, time_interval, symbol)
