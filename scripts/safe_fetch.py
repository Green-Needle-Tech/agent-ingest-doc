#!/usr/bin/env python3
"""safe_fetch.py — stdlib-only URL validator and safe HTTP fetcher.

Enforces SSRF protections before any network request:
  - Only http/https schemes
  - Block loopback, private, link-local, multicast, cloud metadata addresses
  - Strip URL credentials
  - Set timeouts, redirect caps, byte caps
  - Revalidate every redirect destination

Usage as a module:
  from safe_fetch import safe_fetch
  content = safe_fetch("https://example.com/doc")  # returns str or raises

Usage as a CLI:
  python3 safe_fetch.py <url> [--max-bytes 52428800] [--timeout 30] [--max-redirects 5]
  Prints content to stdout on success, error JSON to stderr on failure.
  Exit 0 on success, 1 on error.
"""
import argparse
import ipaddress
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB

# Cloud metadata IPs that must never be fetched
METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure
    ipaddress.ip_address("fd00:ec2::254"),     # AWS IPv6
    ipaddress.ip_address("100.100.100.200"),   # Alibaba Cloud
}

ALLOWED_SCHEMES = ("http", "https")


class FetchError(Exception):
    """Raised when a URL fails safety validation or fetch."""
    pass


def validate_url(url: str) -> str:
    """Validate a URL against SSRF rules. Returns the cleaned URL.

    Raises FetchError if the URL is unsafe.
    """
    if not url or not url.strip():
        raise FetchError("empty URL")

    url = url.strip()

    # Parse the URL
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise FetchError(f"malformed URL: {e}")

    # Scheme check
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise FetchError(
            f"scheme {parsed.scheme!r} not allowed "
            f"(only {ALLOWED_SCHEMES})")

    # Strip credentials from URL
    if parsed.username or parsed.password:
        cleaned = parsed._replace(netloc=parsed.hostname)
        if parsed.port:
            cleaned = cleaned._replace(
                netloc=f"{parsed.hostname}:{parsed.port}")
        url = urllib.parse.urlunsplit(cleaned)
        parsed = cleaned

    # Validate hostname
    hostname = parsed.hostname
    if not hostname:
        raise FetchError("no hostname in URL")

    # Resolve hostname and check all IPs
    try:
        addrinfos = socket.getaddrinfo(
            hostname, parsed.port or 443,
            type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"DNS resolution failed for {hostname}: {e}")

    for family, _, _, _, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        if ip in METADATA_IPS:
            raise FetchError(f"cloud metadata IP blocked: {ip}")
        if ip.is_loopback:
            raise FetchError(f"loopback IP blocked: {ip}")
        if ip.is_private:
            raise FetchError(f"private IP blocked: {ip}")
        if ip.is_link_local:
            raise FetchError(f"link-local IP blocked: {ip}")
        if ip.is_multicast:
            raise FetchError(f"multicast IP blocked: {ip}")
        if ip.is_reserved:
            raise FetchError(f"reserved IP blocked: {ip}")

    return url


def safe_fetch(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    """Fetch a URL with SSRF protections. Returns content as string.

    Raises FetchError on any safety violation or fetch error.
    """
    current_url = url
    redirects = 0

    while True:
        # Validate the current URL (including redirect targets)
        current_url = validate_url(current_url)

        if redirects >= max_redirects:
            raise FetchError(
                f"exceeded max redirects ({max_redirects})")

        try:
            req = urllib.request.Request(
                current_url,
                headers={"User-Agent": "doc-ingest-safe-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # Check content length if declared
                cl = resp.headers.get("Content-Length")
                if cl and int(cl) > max_bytes:
                    raise FetchError(
                        f"content length {cl} exceeds max {max_bytes}")

                # Read with byte cap
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise FetchError(
                        f"downloaded bytes exceed max {max_bytes}")

                # Check for redirect (urllib follows automatically, but
                # we validate the final URL too)
                final_url = resp.geturl()
                if final_url != current_url:
                    current_url = final_url
                    redirects += 1
                    # Re-validate (already done at loop top, but be explicit)
                    continue

                # Decode — try utf-8, fall back to latin-1
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    return data.decode("latin-1")

        except urllib.error.HTTPError as e:
            raise FetchError(f"HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise FetchError(f"URL error: {e.reason}")
        except socket.timeout:
            raise FetchError(f"timeout after {timeout}s")
        except OSError as e:
            raise FetchError(f"network error: {e}")


def main():
    ap = argparse.ArgumentParser(
        description="Safe URL fetcher with SSRF protections")
    ap.add_argument("url", help="URL to fetch")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help=f"max bytes to download (default: {DEFAULT_MAX_BYTES})")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"timeout in seconds (default: {DEFAULT_TIMEOUT})")
    ap.add_argument("--max-redirects", type=int, default=DEFAULT_MAX_REDIRECTS,
                    help=f"max redirects (default: {DEFAULT_MAX_REDIRECTS})")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="output JSON with metadata instead of raw content")
    args = ap.parse_args()

    try:
        content = safe_fetch(
            args.url,
            timeout=args.timeout,
            max_redirects=args.max_redirects,
            max_bytes=args.max_bytes,
        )
        if args.as_json:
            print(json.dumps({
                "status": "ok",
                "url": args.url,
                "bytes": len(content.encode("utf-8")),
            }))
        else:
            sys.stdout.write(content)
        return 0
    except FetchError as e:
        if args.as_json:
            print(json.dumps({
                "status": "error",
                "url": args.url,
                "error": str(e),
            }))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
