"""Public address checks, DNS pinning, redirect policy and real worker cancellation."""
import multiprocessing
import socket
import time
import unittest
from unittest.mock import Mock, patch

from news import network


def address_records(*addresses):
    return [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
            for address in addresses]


class NetworkTests(unittest.TestCase):
    def test_rejects_private_and_special_ipv4_and_ipv6_before_connecting(self):
        addresses = ("127.0.0.1", "10.1.2.3", "172.16.1.1", "192.168.1.1", "169.254.169.254",
                     "0.0.0.0", "100.64.0.1", "224.0.0.1", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1")
        for address in addresses:
            with self.subTest(address=address), patch("news.network.socket.getaddrinfo", return_value=address_records(address)):
                with self.assertRaisesRegex(ValueError, "private or reserved"):
                    network.public_destination("https://publisher.example/story")

    def test_rejects_mixed_public_private_dns_answers(self):
        with patch("news.network.socket.getaddrinfo", return_value=address_records("93.184.216.34", "127.0.0.1")):
            with self.assertRaises(ValueError):
                network.public_destination("https://publisher.example/story")

    def test_pins_resolved_ip_but_preserves_tls_hostname_and_host_header(self):
        response = Mock(status=200)
        response.read.side_effect = [b"news", b""]
        pool = Mock()
        pool.urlopen.return_value = response
        with patch("news.network.socket.getaddrinfo", return_value=address_records("93.184.216.34")) as resolve, \
                patch("news.network.urllib3.HTTPSConnectionPool", return_value=pool) as make_pool:
            result = network.fetch_once("https://publisher.example/story?q=1", 100, "Reader", time.monotonic() + 10)
        self.assertEqual(result, (200, "", b"news"))
        resolve.assert_called_once()
        self.assertEqual(make_pool.call_args.args, ("93.184.216.34",))
        self.assertEqual(make_pool.call_args.kwargs["server_hostname"], "publisher.example")
        self.assertEqual(make_pool.call_args.kwargs["assert_hostname"], "publisher.example")
        self.assertEqual(pool.urlopen.call_args.kwargs["headers"]["Host"], "publisher.example")
        self.assertFalse(pool.urlopen.call_args.kwargs["redirect"])

    def test_redirect_destination_is_checked_before_its_request(self):
        visited = []

        def perform(function, args, deadline):
            visited.append(args[0])
            return (302, "https://other-publisher.example/new", b"") if len(visited) == 1 else (200, "", b"news")

        guard = Mock(side_effect=lambda url: self.assertEqual(len(visited), 1))
        with patch("news.network.run_with_deadline", side_effect=perform):
            self.assertEqual(network.download("https://publisher.example/old", 100, "Reader", redirect_guard=guard), b"news")
        guard.assert_called_once_with("https://other-publisher.example/new")
        self.assertEqual(visited, ["https://publisher.example/old", "https://other-publisher.example/new"])

    def test_disallowed_redirect_is_never_requested(self):
        with patch("news.network.run_with_deadline", return_value=(302, "/blocked", b"")) as perform:
            with self.assertRaisesRegex(ValueError, "robots"):
                network.download("https://publisher.example/", 100, "Reader",
                                 redirect_guard=Mock(side_effect=ValueError("Disallowed by robots")))
        self.assertEqual(perform.call_count, 1)

    def test_public_redirect_cannot_reach_a_private_address(self):
        response = Mock(status=302)
        response.headers = {"Location": "http://169.254.169.254/internal"}
        pool = Mock()
        pool.urlopen.return_value = response

        def resolve(host, port, **kwargs):
            return address_records("169.254.169.254" if host == "169.254.169.254" else "93.184.216.34")

        with patch("news.network.socket.getaddrinfo", side_effect=resolve), \
                patch("news.network.urllib3.HTTPSConnectionPool", return_value=pool) as https_pool, \
                patch("news.network.urllib3.HTTPConnectionPool") as http_pool, \
                patch("news.network.run_with_deadline", side_effect=lambda function, args, deadline: function(*args)):
            with self.assertRaisesRegex(ValueError, "private or reserved"):
                network.download("https://publisher.example/story", 100, "Reader")
        self.assertEqual(https_pool.call_count, 1)
        http_pool.assert_not_called()

    def test_download_limit_counts_decoded_content(self):
        response = Mock(status=200)
        response.read.side_effect = [b"too much", b""]
        pool = Mock()
        pool.urlopen.return_value = response
        with patch("news.network.socket.getaddrinfo", return_value=address_records("93.184.216.34")), \
                patch("news.network.urllib3.HTTPSConnectionPool", return_value=pool):
            with self.assertRaisesRegex(ValueError, "Response exceeds"):
                network.fetch_once("https://publisher.example/", 5, "Reader", time.monotonic() + 10)

    def test_deadline_terminates_and_joins_a_blocked_worker(self):
        before = {child.pid for child in multiprocessing.active_children()}
        started = time.monotonic()
        with self.assertRaises(network.DeadlineExceeded):
            network.run_with_deadline(time.sleep, (10,), started + 0.2)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, before)


if __name__ == "__main__":
    unittest.main()
