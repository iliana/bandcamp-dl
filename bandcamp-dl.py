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
import zipfile
from collections import namedtuple

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

Item = namedtuple("Item", ["artist", "title", "id", "download_url", "tracks"])


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
        req.add_header("cookie", "identity={}".format(identity))
    req.add_header("user-agent", USER_AGENT)
    return req


def bc_json(path, identity, data=None):
    url = urllib.parse.urljoin("https://bandcamp.com/api/", path)
    logging.info("fetch {} as json".format(url))
    if data:
        data = json.dumps(data).encode("utf-8")
    with urllib.request.urlopen(build_request(url, identity, data)) as f:
        return json.loads(f.read().decode("utf-8"))


def bc_pagedata(url, identity):
    logging.info("fetch {} as html".format(url))
    with urllib.request.urlopen(build_request(url, identity)) as f:
        for line in f.readlines():
            line = line.decode(f.headers.get_content_charset())
            if "pagedata" in line and "data-blob" in line:
                break
    return json.loads(html.unescape(line.split('data-blob="')[1].split('"')[0]))


def bc_download(url, identity, format):
    items = bc_pagedata(url, identity)["digital_items"]
    assert len(items) == 1
    url = items[0]["downloads"][format]["url"]
    # munge the download URL to request the correct URL for the bcbits CDN
    split = urllib.parse.urlsplit(url.replace("/download/", "/statdownload/"))
    query = urllib.parse.parse_qsl(split.query)
    query.append((".vrs", 1))
    split = split._replace(query=urllib.parse.urlencode(query))
    url = urllib.parse.urlunsplit(split)
    # then fetch that to get the bcbits URL
    req = build_request(url, identity)
    req.add_header("accept", "application/json")
    logging.info("fetch {} as json".format(url))
    with urllib.request.urlopen(req) as f:
        data = json.loads(f.read().decode("utf-8"))
    return data["download_url"]


def download_file(item, url):
    logging.info("download {}".format(url))
    with urllib.request.urlopen(build_request(url)) as f:
        for x in f.headers["content-disposition"].split(";"):
            x = x.strip()
            if x.startswith("filename*=UTF-8''"):
                filename = urllib.parse.unquote(x.split("''", 1)[1])
        split = filename.rsplit(".", 1)
        filename = "{split[0]} ({item.id}).{split[1]}".format(split=split, item=item)
        size = int(f.headers["content-length"])
        try:
            with open(filename, "wb") as t:
                at = 0
                while True:
                    buf = f.read(16 * 1024)
                    if not buf:
                        break
                    t.write(buf)
                    at += len(buf)
                    progress(filename, at=at, size=size)
        except:  # noqa=E722
            os.remove(filename)
            raise


def collection(identity):
    summary = bc_json("fan/2/collection_summary", identity)["collection_summary"]
    pagedata = bc_pagedata(
        "https://bandcamp.com/{}".format(summary["username"]), identity
    )

    for kind in ("collection", "hidden"):
        yield from items(
            {
                "items": pagedata["item_cache"][kind].values(),
                "redownload_urls": pagedata["collection_data"]["redownload_urls"],
            }
        )
        data = {
            "fan_id": summary["fan_id"],
            "older_than_token": pagedata["{}_data".format(kind)]["last_token"],
        }
        while True:
            res = bc_json("fancollection/1/{}_items".format(kind), identity, data)
            yield from items(res)
            if res["more_available"]:
                data["older_than_token"] = res["last_token"]
            else:
                break


def items(data):
    for item in data["items"]:
        # some items are not actually downloadable! these can be detected with
        # a null featured_track
        if item["featured_track"] is None:
            continue
        sid = "{sale_item_type}{sale_item_id}".format(**item)
        yield Item(
            artist=item["band_name"],
            title=item["item_title"],
            id=item["tralbum_id"],
            download_url=data["redownload_urls"][sid],
            tracks=item["num_streamable_tracks"],
        )


def already_downloaded(item):
    g = glob.glob("*({})*".format(item.id))
    if g:
        # redownload for pre-orders / albums with new tracks: if this is a zip,
        # get the track count and compare against the item's streamable tracks
        # count. if the former is less than the latter, delete and redownload.
        if g[0].rsplit(".", 1)[1] == "zip":
            with zipfile.ZipFile(g[0]) as z:
                count = len(list(filter(is_track, z.namelist())))
            if item.tracks > count:
                logging.info(
                    "remove %s (%s tracks, now has %s)", g[0], count, item.tracks
                )
                os.remove(g[0])
                return False
        progress(g[0], skip=True)
        return True
    else:
        return False


def is_track(filename):
    return any(
        filename.rsplit(".", 1)[1] == ext
        for ext in ("flac", "mp3", "m4a", "ogg", "wav", "aiff")
    )


def progress(item, skip=None, at=None, size=None):
    if isinstance(item, Item):
        data = "{} - {}".format(item.artist, item.title)
        if item.id:
            data += " ({})".format(item.id)
    else:
        data = item

    if skip:
        state = "already downloaded"
    elif at:
        state = "{}%".format(at * 100 // size)
    else:
        state = "starting..."

    print("{}{}: {}".format(CLEAR, data, state), file=sys.stderr, end="\r", flush=True)
    if skip or (at and at == size):
        print(file=sys.stderr)


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
        print("Failed to load identity cookie for bandcamp.com", file=sys.stderr)
        sys.exit(1)

    for item in collection(identity):
        if already_downloaded(item):
            continue
        progress(item)
        download_file(item, bc_download(item.download_url, identity, args.format))
