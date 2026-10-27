"""Bounded public-Internet requests. Workers never open the news database.

An idle socket timeout is not an overall deadline. Each blocking operation runs
in a disposable process which the parent can stop and join before continuing.
Results use a temporary file, so a partial pipe message cannot block the parent.
"""
import ipaddress
import multiprocessing
import pickle
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import urllib3

MAX_REDIRECTS = 5
DEFAULT_DOWNLOAD_SECONDS = 30


class DeadlineExceeded(TimeoutError):
    pass


def remaining_seconds(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded("Source preparation deadline exceeded")
    return remaining


def _worker(function, args, result_path):
    try:
        result = (True, function(*args))
    except Exception as error:
        result = (False, f"{type(error).__name__}: {error}")
    with open(result_path, "wb") as output:
        pickle.dump(result, output, protocol=pickle.HIGHEST_PROTOCOL)


def run_with_deadline(function, args, deadline):
    """Run pure work under an enforceable deadline; no background work survives."""
    remaining_seconds(deadline)
    with tempfile.TemporaryDirectory(prefix="gathering-fetch-") as directory:
        result_path = Path(directory) / "result"
        process = multiprocessing.get_context("spawn").Process(
            target=_worker, args=(function, args, result_path), daemon=True,
        )
        process.start()
        try:
            process.join(max(0, deadline - time.monotonic()))
            if process.is_alive():
                raise DeadlineExceeded("Source preparation deadline exceeded")
            if process.exitcode != 0 or not result_path.is_file():
                raise ValueError("Download or parser worker exited without a result")
            # This file was written only by our child, not downloaded or uploaded.
            with result_path.open("rb") as result_file:
                succeeded, result = pickle.load(result_file)
            remaining_seconds(deadline)
            if not succeeded:
                raise ValueError(result)
            return result
        finally:
            if process.is_alive():
                process.terminate()
                process.join(0.5)
                if process.is_alive():
                    process.kill()
                    process.join()
            process.close()


def public_destination(url):
    """Resolve once and reject the whole host if ANY address is non-public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        raise ValueError("Only public HTTP(S) destinations without credentials are allowed")
    host = parts.hostname.rstrip(".").encode("idna").decode("ascii")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = []
    for result in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(result[4][0])
        check = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
        if (not check.is_global or check.is_multicast or check.is_reserved
                or check.is_loopback or check.is_link_local or check.is_unspecified):
            raise ValueError("Destination resolves to a private or reserved address")
        if str(address) not in addresses:
            addresses.append(str(address))
    if not addresses:
        raise ValueError("Destination has no public address")
    return parts, host, port, addresses


def fetch_once(url, max_bytes, user_agent, deadline):
    """One response, no automatic redirects and no second DNS lookup by HTTP."""
    parts, host, port, addresses = public_destination(url)
    authority = f"[{host}]" if ":" in host else host
    if parts.port:
        authority += f":{port}"
    target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    last_error = None
    for address in addresses[:4]:
        remaining = remaining_seconds(deadline)
        options = {"timeout": urllib3.Timeout(connect=min(10, remaining), read=min(20, remaining)),
                   "retries": False}
        if parts.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(
                address, port=port, server_hostname=host, assert_hostname=host,
                cert_reqs="CERT_REQUIRED", **options,
            )
        else:
            pool = urllib3.HTTPConnectionPool(address, port=port, **options)
        response = None
        try:
            response = pool.urlopen("GET", target, headers={"Host": authority, "User-Agent": user_agent},
                                    redirect=False, preload_content=False, retries=False)
            if response.status in (301, 302, 303, 307, 308):
                return response.status, response.headers.get("Location", ""), b""
            if not 200 <= response.status < 300:
                raise ValueError(f"Publisher returned HTTP {response.status}")
            chunks, size = [], 0
            while True:
                remaining_seconds(deadline)
                chunk = response.read(64 * 1024, decode_content=True)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"Response exceeds {max_bytes // 1_000_000} MB limit")
                chunks.append(chunk)
            return response.status, "", b"".join(chunks)
        except urllib3.exceptions.HTTPError as error:
            last_error = error
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise ValueError(f"Cannot download publisher response: {last_error}")


def download(url, max_bytes, user_agent, deadline=None, redirect_guard=None):
    """Check every redirect before making its request, under one shared budget."""
    deadline = time.monotonic() + DEFAULT_DOWNLOAD_SECONDS if deadline is None else deadline
    current, visited = url, set()
    for hop in range(MAX_REDIRECTS + 1):
        remaining_seconds(deadline)
        if current in visited:
            raise ValueError("Publisher redirect loop")
        visited.add(current)
        status, location, content = run_with_deadline(fetch_once, (current, max_bytes, user_agent, deadline), deadline)
        if status not in (301, 302, 303, 307, 308):
            return content
        if not location or hop == MAX_REDIRECTS:
            raise ValueError("Publisher redirect is missing a destination or exceeds the redirect limit")
        destination = urljoin(current, location)
        if urlsplit(destination).scheme not in ("http", "https"):
            raise ValueError("Publisher redirected to a non-HTTP(S) destination")
        # For HTML, the collector checks robots and pacing on the new path/host.
        # fetch_once then validates and pins that destination's DNS addresses.
        if redirect_guard is not None:
            redirect_guard(destination)
        current = destination
    raise ValueError("Publisher redirect limit exceeded")
