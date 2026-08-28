"""Unit tests for public status page RSS 2.0 incident feed."""

from unittest import mock
import time

from caddy_mon import status_page


def test_status_feed_xml_generates_valid_rss():
    fake_incidents = [
        {
            "ts": time.time() - 300,
            "host": "pilv.example.com",
            "event_type": "DOWN",
            "details": "Connection refused on 192.168.1.5:11000",
        },
        {
            "ts": time.time() - 100,
            "host": "pilv.example.com",
            "event_type": "RECOVERED",
            "details": "Latency: 15.2ms",
        },
    ]

    with mock.patch("caddy_mon.status_page.get_recent_incidents", return_value=fake_incidents), \
         mock.patch.object(status_page, "_state", {"sites": [], "last_update": time.time()}), \
         mock.patch("caddy_mon.status_page.get_all_maintenance", return_value={}):

        response = status_page.status_feed_xml()
        xml_content = response.body.decode("utf-8") if hasattr(response, "body") else response.content.decode("utf-8")

        assert '<?xml version="1.0" encoding="UTF-8"?>' in xml_content
        assert '<rss version="2.0">' in xml_content
        assert '<channel>' in xml_content
        assert '<title>' in xml_content
        assert 'pilv.example.com' in xml_content
        assert '<item>' in xml_content
