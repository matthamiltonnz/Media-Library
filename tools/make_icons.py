#!/usr/bin/env python3
"""Draw the add-on store artwork: media_collection/icon.png and logo.png.

Home Assistant asks for a 128x128 icon and a 250x100 logo, both PNG with an
alpha channel. The artwork is a film strip with a green play mark - the same
idea as the `mdi:movie-open` panel icon - on a dark plate, so it stays legible
on the light and the dark theme in the add-on store.

Nothing is fetched and no drawing library is used: shapes are rasterised with
4x4 supersampling and the PNG chunks are written by hand, so this runs on the
Python 3 that ships with the add-on (and on a bare macOS).

    python3 tools/make_icons.py
"""

import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(os.path.dirname(HERE), "media_collection")

PLATE = (15, 23, 42, 255)      # slate-900 plate
STRIP = (248, 250, 252, 255)   # near-white film strip
MARK = (22, 163, 74, 255)      # green-600 play mark
GREY = (148, 163, 184, 255)    # slate-400 "text" lines in the logo
DIM = (100, 116, 139, 255)     # slate-500
CLEAR = (0, 0, 0, 0)

SAMPLES = 4


def rrect(x0, y0, x1, y1, r):
    """A rounded rectangle: a cross of two rectangles plus the four corners."""

    def inside(x, y):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        cx = min(max(x, x0 + r), x1 - r)
        cy = min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    return inside


def triangle(ax, ay, bx, by, cx, cy):
    """A filled triangle, tested with the sign of the three edge normals."""

    def side(px, py, qx, qy, x, y):
        return (qx - px) * (y - py) - (qy - py) * (x - px)

    def inside(x, y):
        d1 = side(ax, ay, bx, by, x, y)
        d2 = side(bx, by, cx, cy, x, y)
        d3 = side(cx, cy, ax, ay, x, y)
        return (d1 >= 0 and d2 >= 0 and d3 >= 0) or (d1 <= 0 and d2 <= 0 and d3 <= 0)

    return inside


def render(width, height, shapes):
    """Rasterise (colour, predicate) shapes, last one wins, edges sampled."""
    pixels = bytearray()
    step = 1.0 / SAMPLES
    for py in range(height):
        for px in range(width):
            red = green = blue = alpha = 0.0
            for sy in range(SAMPLES):
                y = py + (sy + 0.5) * step
                for sx in range(SAMPLES):
                    x = px + (sx + 0.5) * step
                    colour = CLEAR
                    for fill, inside in shapes:
                        if inside(x, y):
                            colour = fill
                    red += colour[0] * colour[3]
                    green += colour[1] * colour[3]
                    blue += colour[2] * colour[3]
                    alpha += colour[3]
            count = SAMPLES * SAMPLES
            if alpha:
                pixels += bytes((
                    int(round(red / alpha)),
                    int(round(green / alpha)),
                    int(round(blue / alpha)),
                    int(round(alpha / count)),
                ))
            else:
                pixels += b"\0\0\0\0"
    return bytes(pixels)


def png(width, height, pixels):
    """Wrap raw RGBA rows in the four PNG chunks we need."""
    rows = b"".join(
        b"\x00" + pixels[y * width * 4:(y + 1) * width * 4]
        for y in range(height)
    )

    def chunk(tag, data):
        body = struct.pack(">I", len(data)) + tag + data
        return body + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def strip_shapes(x0, y0, x1, y1, holes, mark):
    """Film strip: white bar, dark perforations left and right, green play."""
    r = max(4, (y1 - y0) // 5)
    shapes = [(STRIP, rrect(x0, y0, x1, y1, r))]
    hole_h = (y1 - y0) // 7
    hole_w = max(4, (x1 - x0) // 11)
    gap = (y1 - y0) // 10
    for hx in holes:
        for hy in (y0 + gap, y1 - gap - hole_h):
            shapes.append((PLATE, rrect(hx, hy, hx + hole_w, hy + hole_h, 1)))
    if mark:
        mx0, my0, mx1, my1 = mark
        shapes.append((MARK, triangle(mx0, my0, mx0, my1, mx1, (my0 + my1) / 2.0)))
    return shapes


def icon():
    width = height = 128
    holes = [26, 43, 77, 94]
    shapes = [(PLATE, rrect(0, 0, 128, 128, 28))]
    shapes += strip_shapes(18, 44, 110, 84, holes, (54, 52, 74, 76))
    return width, height, render(width, height, shapes)


def logo():
    width, height = 250, 100
    holes = [27, 44, 72, 89]
    shapes = [(PLATE, rrect(0, 0, 250, 100, 20))]
    shapes += strip_shapes(20, 30, 100, 70, holes, (52, 36, 70, 64))
    # three "text" lines, so the mark reads as a catalogue next to the film
    for x1, top, colour in ((232, 28, STRIP), (212, 44, GREY), (188, 60, DIM)):
        shapes.append((colour, rrect(118, top, x1, top + 12, 6)))
    return width, height, render(width, height, shapes)


def main():
    for name, make in (("icon.png", icon), ("logo.png", logo)):
        width, height, pixels = make()
        path = os.path.join(ADDON, name)
        with open(path, "wb") as handle:
            handle.write(png(width, height, pixels))
        print("wrote %s (%dx%d, %d bytes)" % (path, width, height, os.path.getsize(path)))


if __name__ == "__main__":
    main()
