"""Untrusted record text stays text in server-rendered HTML (no browser execution)."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from optpipe.dashboard import create_app


@pytest.mark.parametrize("section,field,route", [
    ("tickets", "thesis", "/s/2026-10-13/tickets"),
    ("idea_log", "description", "/history"),
])
@pytest.mark.parametrize("payload,encoded", [
    ("<script>SECURITY14_SENTINEL</script>",
     "&lt;script&gt;SECURITY14_SENTINEL&lt;/script&gt;"),
    ('<img src=x onerror="SECURITY14_SENTINEL">',
     '&lt;img src=x onerror=&#34;SECURITY14_SENTINEL&#34;&gt;'),
    ('</td><svg onload="SECURITY14_SENTINEL">',
     '&lt;/td&gt;&lt;svg onload=&#34;SECURITY14_SENTINEL&#34;&gt;'),
    ("Ordinary research: AT&T < 5; retain this text.",
     "Ordinary research: AT&amp;T &lt; 5; retain this text."),
])
def test_record_text_is_encoded_without_mutating_the_record(
    tmp_path, pipeline_output, section, field, route, payload, encoded,
):
    record = copy.deepcopy(pipeline_output["record"])
    record[section][0][field] = payload
    original = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    path = tmp_path / "session-2026-10-13.json"
    path.write_bytes(original)

    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        response = client.get(route)

    assert response.status_code == 200
    assert encoded in response.text
    assert payload not in response.text
    assert path.read_bytes() == original
