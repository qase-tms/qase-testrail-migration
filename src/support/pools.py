import functools
import time
from concurrent.futures import ThreadPoolExecutor

import asyncio

try:
    from qase.api_client_v1.exceptions import ApiException as QaseApiException
except ImportError:
    QaseApiException = None

# Max retries when Qase API returns 429 Too Many Requests
QASE_429_MAX_RETRIES = 10
QASE_429_DEFAULT_DELAY = 10


def _qase_call_with_429_retry(fn, *args, logger=None, **kwargs):
    """Execute fn(*args, **kwargs) and retry on Qase API 429 (rate limit)."""
    if QaseApiException is None:
        return fn(*args, **kwargs)
    last_exception = None
    for attempt in range(QASE_429_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except QaseApiException as e:
            last_exception = e
            status = getattr(e, 'status', None) or getattr(e, 'status_code', None)
            if status != 429:
                raise
            if attempt == QASE_429_MAX_RETRIES:
                raise
            delay = QASE_429_DEFAULT_DELAY
            try:
                resp = getattr(e, 'http_resp', None)
                if resp is not None and hasattr(resp, 'headers'):
                    ra = resp.headers.get('Retry-After')
                    if ra is not None:
                        delay = int(ra) if isinstance(ra, (int, float)) else int(ra)
            except Exception:
                pass
            if logger:
                logger.log(f'[Qase API] Rate limit (429), waiting {delay}s before retry (attempt {attempt + 1}/{QASE_429_MAX_RETRIES})')
            else:
                print(f'[Qase API] Rate limit (429), waiting {delay}s before retry (attempt {attempt + 1}/{QASE_429_MAX_RETRIES})', flush=True)
            time.sleep(delay)
    raise last_exception


def _gen_next_with_429_retry(gen, logger=None):
    """Call next(gen) and retry on Qase API 429. Returns None on StopIteration."""
    if QaseApiException is None:
        try:
            return next(gen)
        except StopIteration:
            return None
    last_exception = None
    for attempt in range(QASE_429_MAX_RETRIES + 1):
        try:
            return next(gen)
        except StopIteration:
            return None
        except QaseApiException as e:
            last_exception = e
            status = getattr(e, 'status', None) or getattr(e, 'status_code', None)
            if status != 429:
                raise
            if attempt == QASE_429_MAX_RETRIES:
                raise
            delay = QASE_429_DEFAULT_DELAY
            try:
                resp = getattr(e, 'http_resp', None)
                if resp is not None and hasattr(resp, 'headers'):
                    ra = resp.headers.get('Retry-After')
                    if ra is not None:
                        delay = int(ra) if isinstance(ra, (int, float)) else int(ra)
            except Exception:
                pass
            if logger:
                logger.log(f'[Qase API] Rate limit (429), waiting {delay}s before retry (attempt {attempt + 1}/{QASE_429_MAX_RETRIES})')
            else:
                print(f'[Qase API] Rate limit (429), waiting {delay}s before retry (attempt {attempt + 1}/{QASE_429_MAX_RETRIES})', flush=True)
            time.sleep(delay)
    raise last_exception


class Pools:
    def __init__(
            self,
            qase_pool: ThreadPoolExecutor,
            tr_pool: ThreadPoolExecutor,
            logger=None,
    ):
        self.qase_pool = qase_pool
        self.tr_pool = tr_pool
        self.logger = logger

    def _wrap_qase(self, fn, *args, **kwargs):
        """Submit Qase call with 429 retry wrapper."""
        return self.qase_pool.submit(_qase_call_with_429_retry, fn, *args, logger=self.logger, **kwargs)

    @staticmethod
    async def async_gen(pool: ThreadPoolExecutor, fn, *args, **kwargs):
        def gen_next(gen):
            try:
                return next(gen)
            except StopIteration:
                pass

        gen = await asyncio.wrap_future(pool.submit(fn, *args, **kwargs))
        while True:
            if (i := await asyncio.wrap_future(pool.submit(gen_next, gen))) is None:
                break
            yield i

    @staticmethod
    async def async_gen_all(pool: ThreadPoolExecutor, fn, *args, **kwargs):
        return functools.reduce(lambda x, y: x + y, [_ async for _ in Pools.async_gen(pool, fn, *args, **kwargs)])

    async def _async_gen_qase(self, fn, *args, **kwargs):
        """Like async_gen but wraps fn and each next(gen) with 429 retry."""
        gen = await asyncio.wrap_future(self._wrap_qase(fn, *args, **kwargs))
        while True:
            i = await asyncio.wrap_future(self.qase_pool.submit(_gen_next_with_429_retry, gen, self.logger))
            if i is None:
                break
            yield i

    async def _async_gen_all_qase(self, fn, *args, **kwargs):
        return functools.reduce(lambda x, y: x + y, [_ async for _ in self._async_gen_qase(fn, *args, **kwargs)])

    def tr(self, fn, *args, **kwargs):
        return asyncio.wrap_future(self.tr_pool.submit(fn, *args, **kwargs))

    def qs(self, fn, *args, **kwargs):
        return asyncio.wrap_future(self._wrap_qase(fn, *args, **kwargs))

    async def tr_task(self, fn, *args, **kwargs):
        return await asyncio.wrap_future(self.tr_pool.submit(fn, *args, **kwargs))

    async def qs_task(self, fn, *args, **kwargs):
        return await asyncio.wrap_future(self._wrap_qase(fn, *args, **kwargs))

    def tr_gen(self, fn, *args, **kwargs):
        return self.async_gen(self.tr_pool, fn, *args, **kwargs)

    def qs_gen(self, fn, *args, **kwargs):
        return self._async_gen_qase(fn, *args, **kwargs)

    async def tr_gen_all(self, fn, *args, **kwargs):
        return await self.async_gen_all(self.tr_pool, fn, *args, **kwargs)

    async def qs_gen_all(self, fn, *args, **kwargs):
        return await self._async_gen_all_qase(fn, *args, **kwargs)
