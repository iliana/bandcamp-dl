# bandcamp-dl

This is a Python script that will download your entire Bandcamp collection to your working directory. For albums, it downloads ZIP files, and for tracks, it downloads the track.

It downloads items in FLAC format by default. Use `--format` to change the format; known format identifiers are: 
`aac-hi`
`aiff-lossless`
`alac`
`flac`
`mp3-320`
`mp3-v0`
`vorbis`
`wav`

If you have [browser-cookie3](https://pypi.org/project/browser-cookie3/) the script will attempt to pull your bandcamp.com `identity` cookie and username from Firefox or Chrome.
If this doesn't work for you, provide the value of your `identity` cookie as a raw or Base64 string to `--identity`.
browser-cookie3 exceptions are visible with `-v`/`--verbose`.

This script only downloads items you've purchased.
Because this uses undocumented and unsupported APIs, it may break at any time.
**The author will not provide support for this script.**
