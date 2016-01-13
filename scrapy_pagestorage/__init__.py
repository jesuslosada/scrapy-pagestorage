"""
Middleware for implementing visited pages storage using hubstorage
"""
import os
from cgi import parse_qsl

from hubstorage import ValueTooLarge
from hubstorage.utils import urlpathjoin
from scrapy.exceptions import NotConfigured, IgnoreRequest
from scrapy.utils.request import request_fingerprint
from scrapy.http import TextResponse
from scrapy import log
from slybot.item import SlybotItem

from sh_scrapy.hsref import hsref


_COLLECTION_NAME = "Pages"


class PageStorageMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        enabled, on_error_enabled = _get_enabled_status(crawler.settings)
        if enabled or on_error_enabled:
            return cls(crawler)
        raise NotConfigured

    def __init__(self, crawler):
        settings = crawler.settings
        mode = 'cs'
        if settings['PAGE_STORAGE_MODE'] == 'VERSIONED_CACHE':
            mode = 'vcs'
        self.enabled, self.on_error_enabled = _get_enabled_status(settings)
        self.limits = {
            'all': crawler.settings.getint('PAGE_STORAGE_LIMIT'),
            'error': crawler.settings.getint('PAGE_STORAGE_ON_ERROR_LIMIT'),
        }
        self.counters = {
            'all': 0,
            'error': 0,
        }
        self.cookies_seen = set()
        endpoint = urlpathjoin(hsref.project.collections.url,
                               mode, _COLLECTION_NAME)
        log.msg("HubStorage: writing pages to %s" % endpoint)
        hsref.job.metadata.apipost('collection',
                                   jl=urlpathjoin(mode, _COLLECTION_NAME))
        self._writer = hsref.client.batchuploader.create_writer(
            endpoint, content_encoding='gzip', size=20)

    def process_spider_input(self, response, spider):
        if self.enabled and (self.counters['all'] < self.limits['all']):
            self.counters['all'] += 1
            self.save_response(response, spider)

    def process_spider_exception(self, response, exception, spider):
        if (self.on_error_enabled and
                not isinstance(exception, IgnoreRequest) and
                self.counters['error'] < self.limits['error']):
            self.counters['error'] += 1
            self.save_response(response, spider)

    def save_response(self, response, spider):
        if isinstance(response, TextResponse):
            fp = request_fingerprint(response.request)
            payload = {
                "_key": fp,
                "_jobid": hsref.job.key,
                "_type": "_pageitem",
                "_encoding": response.encoding,
                "url": response.url,
            }
            self._set_cookies(payload, response)

            if response.request.method == 'POST':
                payload["postdata"] = dict(parse_qsl(response.request.body))

            if len(response.body) > self._writer.maxitemsize:
                spider.log("Page not saved, body too large: <%s>" %
                           response.url, level=log.WARNING)
                return

            payload["body"] = response.body_as_unicode()
            try:
                self._writer.write(payload)
            except ValueTooLarge as exc:
                spider.log("Page not saved, %s: <%s>" %
                           (exc, response.url), level=log.WARNING)

    def process_spider_output(self, response, result, spider):
        fp = request_fingerprint(response.request)
        try:
            for r in result:
                if isinstance(r, SlybotItem):
                    r["_cached_page_id"] = fp
                yield r
        except Exception as exc:
            self.process_spider_exception(response, exc, spider)
            raise

    def _set_cookies(self, payload, response):
        cookies = []
        for cookie in [x.split(';', 1)[0]
                       for x in response.headers.getlist('Set-Cookie')]:
            if cookie not in self.cookies_seen:
                self.cookies_seen.add(cookie)
                cookies.append(cookie)
        if cookies:
            payload["cookies"] = cookies


def _get_enabled_status(settings):
    enabled = settings.getbool('PAGE_STORAGE_ENABLED')
    autospider = (os.environ.get('SHUB_SPIDER_TYPE') in ('auto', 'portia'))
    on_error_enabled = settings.getbool('PAGE_STORAGE_ON_ERROR_ENABLED')
    return (enabled or autospider), on_error_enabled