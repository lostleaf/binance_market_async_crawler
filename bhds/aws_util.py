import logging
import os
import subprocess

import aiohttp
import xmltodict

from util import async_retry_getter, create_aiohttp_session

AWS_TYPE_MAP = {
    'spot': ['data', 'spot'],
    'usdt_futures': ['data', 'futures', 'um'],
    'coin_futures': ['data', 'futures', 'cm'],
}

AWS_TIMEOUT_SEC = 30

PREFIX = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
PATH_API_URL = f'{PREFIX}?delimiter=/&prefix='
DOWNLOAD_URL = f'{PREFIX}/'


def parse_aws_dt_from_filepath(p):
    filename = os.path.basename(p)
    dt = filename.split('.')[0].split('-', 2)[-1].replace('-', '')
    return dt


def aws_get_candle_dir(type_, symbol, time_interval, local=False):
    path_tokens = AWS_TYPE_MAP[type_] + ['daily', 'klines', symbol, time_interval]
    
    if local:
        return os.path.join(*path_tokens) + os.sep

    # As AWS web path
    return '/'.join(path_tokens) + '/'


async def _aio_get(session: aiohttp.ClientSession, url):
    async with session.get(url) as resp:
        data = await resp.text()
    return xmltodict.parse(data)


async def aws_list_dir(path):
    url = PATH_API_URL + path
    base_url = url
    results = []
    async with create_aiohttp_session(AWS_TIMEOUT_SEC) as session:
        while True:
            data = await async_retry_getter(_aio_get, session=session, url=url)
            xml_data = data['ListBucketResult']
            if 'CommonPrefixes' in xml_data:
                results.extend([x['Prefix'] for x in xml_data['CommonPrefixes']])
            elif 'Contents' in xml_data:
                results.extend([x['Key'] for x in xml_data['Contents']])
            if xml_data['IsTruncated'] == 'false':
                break
            url = base_url + '&marker=' + xml_data['NextMarker']

    results = sorted(set(results))
    return results


def aws_download_into_folder(paths, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    paths = [DOWNLOAD_URL + p for p in paths]
    cmd = ['aria2c', '-c', '-d', output_dir, '-Z'] + paths

    subprocess.run(cmd)
