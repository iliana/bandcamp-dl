#!/usr/bin/env python3

import argparse
import base64
import binascii
import glob
import html
import json
import logging
import os
import os.path
import sys
import urllib.parse
import urllib.request

try:
    import browser_cookie3
except ImportError:
    COOKIE_FN = []
else:
    COOKIE_FN = [browser_cookie3.chrome, browser_cookie3.firefox]


USER_AGENT = (
    "bandcamp-dl/0.0 (https://github.com/iliana/bandcamp-dl) Python-urllib/"
    + "{0}.{1}".format(*sys.version_info[:2])
)
CLEAR = "\033[K"


def get_identity(identity):
    if identity:
        try:
            data = base64.b64decode(identity)
            if identity.encode("utf-8") != base64.b64encode(data):
                data = identity
        except binascii.Error:
            data = identity
        return urllib.parse.quote(data, safe="")

    for cookie_fn in COOKIE_FN:
        try:
            cookies = cookie_fn(domain_name="bandcamp.com")
        except:  # noqa=E722
            logging.info("%s failed", cookie_fn, exc_info=sys.exc_info())
            continue
        for cookie in cookies:
            if cookie.name == "identity" and cookie.domain == ".bandcamp.com":
                return cookie.value


def build_request(url, identity=None, *args, **kwargs):
    req = urllib.request.Request(url, *args, **kwargs)
    if identity:
        req.add_header("cookie", f"identity={identity}")
    req.add_header("user-agent", USER_AGENT)
    return req


def bc_json(path, identity, data=None, **kwargs):
    url = urllib.parse.urljoin("https://bandcamp.com/api/", path)
    if kwargs:
        url += "?" + urllib.parse.urlencode(kwargs)
    logging.info(f"fetch {url} as json")
    if data:
        data = json.dumps(data).encode("utf-8")
    with urllib.request.urlopen(build_request(url, identity, data)) as f:
        return json.load(f)


def bc_download(url, identity, format):
    logging.info(f"fetch {url} as html")
    with urllib.request.urlopen(build_request(url, identity)) as f:
        for line in f.readlines():
            line = line.decode(f.headers.get_content_charset())
            if "pagedata" in line and "data-blob" in line:
                break
    blob = json.loads(html.unescape(line.split('data-blob="')[1].split('"')[0]))
    for item in blob["digital_items"]:
        eprint(
            f"{CLEAR}{item['artist']} - {item['title']} ({item['download_id']}): ",
            end="",
        )
        if already_downloaded(item["download_id"]):
            eprint("already downloaded")
            continue
        eprint("starting...", end="\r")
        # munge the download URL to request the correct URL for the bcbits CDN
        url = item["downloads"][format]["url"]
        split = urllib.parse.urlsplit(url.replace("/download/", "/statdownload/"))
        query = urllib.parse.parse_qsl(split.query)
        query.append((".vrs", 1))
        split = split._replace(query=urllib.parse.urlencode(query))
        url = urllib.parse.urlunsplit(split)
        # then fetch that to get the bcbits URL
        req = build_request(url, identity)
        req.add_header("accept", "application/json")
        logging.info(f"fetch {url} as json")
        with urllib.request.urlopen(req) as f:
            data = json.load(f)
        yield data["download_url"]


def download_file(url):
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    logging.info(f"download {url}")
    with urllib.request.urlopen(build_request(url)) as f:
        for x in f.headers["content-disposition"].split(";"):
            x = x.strip()
            if x.startswith("filename="):
                filename = x.split('"')[1]
        split = filename.rsplit(".", 1)
        filename = f"{split[0]} ({query['id'][0]}).{split[1]}"
        length = int(f.headers["content-length"])
        try:
            with open(filename, "wb") as t:
                read = 0
                while True:
                    buf = f.read(16 * 1024)
                    if not buf:
                        eprint()
                        break
                    t.write(buf)
                    read += len(buf)
                    eprint(f"{CLEAR}{filename}: {(read * 100) // length}%", end="\r")
        except:  # noqa=E722
            os.remove(filename)
            raise


def already_downloaded(id):
    return len(glob.glob(f"*({id})*")) > 0


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format", help="Format to download (default: %(default)s)", default="flac"
    )
    parser.add_argument(
        "--identity", help='Value of the "identity" cookie (raw or Base64)'
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Be verbose",
        action="store_const",
        dest="loglevel",
        const=logging.INFO,
    )
    args = parser.parse_args()
    logging.basicConfig(level=args.loglevel)

    identity = get_identity(args.identity)
    if identity is None:
        eprint("Failed to load identity cookie for bandcamp.com")
        sys.exit(1)

    summary = bc_json("fan/2/collection_summary", identity)
    data = dict(username=summary["collection_summary"]["username"], platform="nix")
    while True:
        res = bc_json("orderhistory/1/get_items", identity, data)
        if res.get("error") == "invalid_crumb":
            data["crumb"] = res["crumb"]
            continue

        for item in res["items"]:
            if item["download_id"]:
                eprint(
                    f"{item['artist_name']} - {item['item_title']} "
                    f"({item['download_id']}): ",
                    end="",
                )
                if already_downloaded(item["download_id"]):
                    eprint("already downloaded")
                else:
                    eprint("starting...", end="\r")
                    for url in bc_download(item["download_url"], identity, args.format):
                        download_file(url)

        if res["last_token"] is None:
            break
        data["last_token"] = res["last_token"]
