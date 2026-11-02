"""HttpFetcher is the only network code: it issues one GET with a timeout and nothing else.
The transport is mocked; no network is used."""
import pytest
import requests

from optpipe.providers.base import DataUnavailable
from optpipe.providers.fetchers import HttpFetcher


class _Resp:
    def __init__(self, status=200, text="ok"):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture
def transport(monkeypatch):
    class Calls(list):
        resp = _Resp()
    calls = Calls()

    def fake_get(url, **kw):
        calls.append(("GET", url, kw))
        return calls.resp

    def forbidden(*a, **kw):
        raise AssertionError("HttpFetcher used a non-GET transport call")

    monkeypatch.setattr(requests, "get", fake_get)
    for name in ("post", "put", "patch", "delete", "request", "head", "options"):
        monkeypatch.setattr(requests, name, forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    return calls


def test_get_issues_a_single_get_with_timeout(transport):
    r = HttpFetcher("test-agent", timeout_s=7).get("nasdaq_info", "JPM", "https://example.com/info")
    assert transport == [("GET", "https://example.com/info",
                          {"headers": transport[0][2]["headers"], "timeout": 7})]
    assert transport[0][2]["headers"]["User-Agent"] == "test-agent"
    assert r.kind == "live" and r.text == "ok" and r.url == "https://example.com/info"


def test_http_error_becomes_data_unavailable(transport):
    transport.resp = _Resp(503)
    with pytest.raises(DataUnavailable, match="GET failed"):
        HttpFetcher("ua").get("nasdaq_chain", "JPM", "https://example.com/oc")
    assert [c[0] for c in transport] == ["GET"]
