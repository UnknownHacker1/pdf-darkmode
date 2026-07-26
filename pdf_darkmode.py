#!/usr/bin/env python3
"""
pdf_darkmode.py: turn any PDF into a dark-mode copy that's easy to read at night.

A plain color invert has two problems, and this script avoids both.

  1. Hue-preserving lightness inversion.
     A plain 255-c invert flips a blue heading to orange and a red warning to
     cyan. This inverts only the lightness of each pixel and keeps the hue,
     using the exact per-pixel reflection:
         c' = 255 - max(r,g,b) - min(r,g,b) + c
     White backgrounds go dark, black text goes light, and a blue link stays
     a readable blue.

  2. No harsh black-on-white glare.
     Pure black behind pure white text "halates" (the text glows and smears)
     at night. This lifts blacks to a soft charcoal and pulls whites down to a
     gentle off-white, so contrast stays readable without burning your eyes.

  3. Photos still look like photos.
     Inverting a photograph or a microscope image looks awful. The script finds
     continuous-tone and colorful images and keeps them in their natural colors
     (dimmed a little so they don't glare), while still inverting line art,
     charts and diagrams, which look better dark anyway.

  4. Warm night themes (optional).
     Amber-tinted themes cut blue light for late reading.

Two conversion modes: 'image' rasterizes each page (works on anything, scans
included), and 'text' recolors the content in place so the output text stays
selectable. The table of contents and bookmarks are carried across either way.

Requires PyMuPDF (fitz), Pillow, numpy:
    pip install pymupdf pillow numpy

Usage:
    python pdf_darkmode.py paper.pdf
    python pdf_darkmode.py paper.pdf -o paper_night.pdf --theme warm
    python pdf_darkmode.py paper.pdf --mode text
    python pdf_darkmode.py book.pdf --theme black --dpi 200
    python pdf_darkmode.py scan.pdf --images invert
"""

import argparse
import io
import os
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("Missing PyMuPDF.  Install with:  pip install pymupdf")

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("Missing Pillow/numpy.  Install with:  pip install pillow numpy")


# --------------------------------------------------------------------------- #
#  Themes: floor = darkest a background gets, ceil = brightest text gets.
#  warmth in [0,1] tilts the palette toward amber (cuts blue light).
# --------------------------------------------------------------------------- #
THEMES = {
    # neutral soft dark, the default; works for almost everything
    "charcoal": dict(floor=20, ceil=224, warmth=0.0),
    # near-OLED pure dark, higher contrast (great on OLED / very dark rooms)
    "black":    dict(floor=4,  ceil=240, warmth=0.0),
    # amber-tinted, low blue light; easiest on the eyes late at night
    "warm":     dict(floor=22, ceil=214, warmth=0.45),
    # stronger sepia/candlelight feel
    "sepia":    dict(floor=26, ceil=205, warmth=0.75),
    # gentle, low-contrast grey-on-grey for very light sensitivity
    "dim":      dict(floor=32, ceil=196, warmth=0.15),
}


def lightness_invert(arr):
    """Invert per-pixel lightness while preserving hue & chroma.

    arr: HxWx3 uint8.  Returns float array in [0,255].
    """
    a = arr.astype(np.int16)
    mx = a.max(axis=2, keepdims=True)
    mn = a.min(axis=2, keepdims=True)
    out = 255 - mx - mn + a          # reflect each channel about its midpoint
    return np.clip(out, 0, 255).astype(np.float32)


def apply_night_palette(inv, floor, ceil, warmth):
    """Squeeze the inverted [0,255] range into [floor, ceil] and warm-tint it."""
    out = floor + inv * ((ceil - floor) / 255.0)
    if warmth > 0.0:
        # push toward amber: keep red, ease green a touch, cut blue
        r_gain = 1.0 + 0.06 * warmth
        g_gain = 1.0 - 0.04 * warmth
        b_gain = 1.0 - 0.32 * warmth
        out[..., 0] *= r_gain
        out[..., 1] *= g_gain
        out[..., 2] *= b_gain
    return np.clip(out, 0, 255)


def looks_photographic(region):
    """Heuristic: is this image region a photo / continuous-tone (True) or
    line-art / chart / text (False)?

    Photos have lots of midtones and/or high colorfulness.  Line art is
    mostly near-black plus near-white and low colorfulness, and actually looks
    better inverted, so we leave those inverted.
    """
    if region.size == 0:
        return False
    r = region[..., 0].astype(np.float32)
    g = region[..., 1].astype(np.float32)
    b = region[..., 2].astype(np.float32)

    # fraction of pixels sitting in the midtones (not near pure black/white)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    midtone_frac = np.mean((lum > 40) & (lum < 215))

    # Hasler-Susstrunk colorfulness
    rg = r - g
    yb = 0.5 * (r + g) - b
    colorfulness = (np.sqrt(rg.std() ** 2 + yb.std() ** 2)
                    + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))

    return midtone_frac > 0.18 or colorfulness > 22.0


def restore_images(page, arr, inv, zoom, mode, photo_dim):
    """Paste natural-color images back over the inverted page.

    mode: 'smart'  -> only restore photographic images
          'keep'   -> restore every raster image (all natural)
          'invert' -> restore nothing (everything stays inverted)
    """
    if mode == "invert":
        return 0

    h, w = inv.shape[:2]
    restored = 0
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        infos = page.get_image_info()

    for info in infos:
        bbox = info.get("bbox")
        if not bbox:
            continue
        x0 = max(0, int(bbox[0] * zoom))
        y0 = max(0, int(bbox[1] * zoom))
        x1 = min(w, int(bbox[2] * zoom))
        y1 = min(h, int(bbox[3] * zoom))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue

        region = arr[y0:y1, x0:x1]
        if mode == "smart" and not looks_photographic(region):
            continue

        # natural colors, dimmed a touch so bright photos don't glare at night
        inv[y0:y1, x0:x1] = region.astype(np.float32) * photo_dim
        restored += 1
    return restored


def invert_page_png(page, zoom, floor, ceil, warmth, images_mode, photo_dim):
    """Render a page, dark-mode it as a bitmap, return PNG bytes."""
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    arr = arr.reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:                       # grayscale -> RGB
        arr = np.repeat(arr, 3, axis=2)
    elif pix.n == 4:                     # drop alpha if present
        arr = arr[..., :3]
    arr = np.ascontiguousarray(arr)

    inv = lightness_invert(arr)
    inv = apply_night_palette(inv, floor, ceil, warmth)
    restored = restore_images(page, arr, inv, zoom, images_mode, photo_dim)
    final = np.clip(inv, 0, 255).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(final, "RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue(), restored


def convert_image(src_path, out_path, dpi, theme, images, photo_dim, verbose=True):
    """Rasterizing mode: render every page and invert the pixels. Works on
    anything, scans included; the output text is not selectable."""
    cfg = THEMES[theme]
    floor, ceil, warmth = cfg["floor"], cfg["ceil"], cfg["warmth"]
    zoom = dpi / 72.0

    src = fitz.open(src_path)
    dst = fitz.open()
    total_restored = 0

    for i, page in enumerate(src):
        png, restored = invert_page_png(page, zoom, floor, ceil, warmth,
                                        images, photo_dim)
        total_restored += restored
        newpage = dst.new_page(width=page.rect.width, height=page.rect.height)
        newpage.insert_image(page.rect, stream=png)
        if verbose:
            print(f"  page {i + 1}/{src.page_count}", end="\r", flush=True)

    try:
        toc = src.get_toc()
        if toc:
            dst.set_toc(toc)
    except Exception:
        pass

    dst.save(out_path, deflate=True, garbage=4)
    dst.close()
    src.close()
    if verbose:
        size_mb = os.path.getsize(out_path) / 1e6
        print(f"\n  done: {out_path}  ({size_mb:.1f} MB, image mode, "
              f"{total_restored} image(s) kept natural)")


# --------------------------------------------------------------------------- #
#  Text-preserving mode: rewrite the colour operators inside each page's
#  content stream, leaving glyphs, fonts and positions untouched. Text stays
#  selectable and files stay small. Works on born-digital PDFs; pages that are
#  really scans (or use constructs we can't safely rewrite) fall back to the
#  image path automatically.
# --------------------------------------------------------------------------- #
def _cmyk_to_rgb(c, m, y, k):
    return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))


def invert_color01(r, g, b, floor, ceil, warmth):
    """Same hue-preserving lightness inversion + night palette, on 0..1 RGB."""
    mx, mn = max(r, g, b), min(r, g, b)
    fl, ce = floor / 255.0, ceil / 255.0

    def one(c):
        v = min(1.0, max(0.0, 1 - mx - mn + c))
        return fl + v * (ce - fl)

    r2, g2, b2 = one(r), one(g), one(b)
    if warmth > 0:
        r2 *= 1 + 0.06 * warmth
        g2 *= 1 - 0.04 * warmth
        b2 *= 1 - 0.32 * warmth
    clip = lambda v: min(1.0, max(0.0, v))
    return clip(r2), clip(g2), clip(b2)


def _tokenize_content(s):
    """Tokenize a PDF content stream (latin-1 str). Returns a list of tokens,
    or None if it contains an inline image (BI) we shouldn't rewrite."""
    toks, i, n = [], 0, len(s)
    WS = " \t\r\n\x00\x0c"
    DELIM = "()<>[]{}/%"
    while i < n:
        c = s[i]
        if c in WS:
            i += 1
        elif c == "%":                                   # comment to line end
            while i < n and s[i] not in "\r\n":
                i += 1
        elif c == "(":                                   # string literal
            depth, i, buf = 0, i + 1, "("
            while i < n:
                ch = s[i]
                if ch == "\\":
                    buf += s[i:i + 2]; i += 2; continue
                buf += ch; i += 1
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        break
                    depth -= 1
            toks.append(buf)
        elif c == "<":
            if i + 1 < n and s[i + 1] == "<":
                toks.append("<<"); i += 2
            else:                                        # hex string
                j = i; i += 1
                while i < n and s[i] != ">":
                    i += 1
                i += 1
                toks.append(s[j:i])
        elif c == ">":
            if i + 1 < n and s[i + 1] == ">":
                toks.append(">>"); i += 2
            else:
                toks.append(">"); i += 1
        elif c in "[]{}":
            toks.append(c); i += 1
        elif c == "/":                                   # name
            j = i; i += 1
            while i < n and s[i] not in WS and s[i] not in DELIM:
                i += 1
            toks.append(s[j:i])
        else:                                            # number or operator
            j = i
            while i < n and s[i] not in WS and s[i] not in DELIM:
                i += 1
            tok = s[j:i]
            if tok == "BI":                              # inline image -> bail
                return None
            toks.append(tok)
    return toks


_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")
_COLOR_OPS = {"g": 1, "G": 1, "rg": 3, "RG": 3, "k": 4, "K": 4}


def _remap_content_stream(data, floor, ceil, warmth):
    """Rewrite device-colour operators to their dark-mode equivalents.
    Returns new stream text, or None if it can't be handled safely."""
    toks = _tokenize_content(data)
    if toks is None:
        return None
    out = []
    for tok in toks:
        n = _COLOR_OPS.get(tok)
        if n and len(out) >= n and all(_NUM.match(x) for x in out[-n:]):
            vals = [float(x) for x in out[-n:]]
            del out[-n:]
            if n == 1:
                col = (vals[0], vals[0], vals[0])
            elif n == 3:
                col = (vals[0], vals[1], vals[2])
            else:
                col = _cmyk_to_rgb(*vals)
            r, g, b = invert_color01(*col, floor, ceil, warmth)
            op = "RG" if tok.isupper() else "rg"         # emit as device RGB
            out.append(f"{r:.4f} {g:.4f} {b:.4f} {op}")
        else:
            out.append(tok)
    return " ".join(out)


def _image_dominates(page, frac=0.8):
    """True if a single embedded image covers most of the page (i.e. a scan)."""
    area = page.rect.width * page.rect.height
    if area <= 0:
        return False
    for im in page.get_image_info():
        b = im.get("bbox")
        if b and (b[2] - b[0]) * (b[3] - b[1]) > frac * area:
            return True
    return False


def convert_text(src_path, out_path, dpi, theme, images, photo_dim, verbose=True):
    """Text-preserving mode: recolour content in place; keep selectable text."""
    cfg = THEMES[theme]
    floor, ceil, warmth = cfg["floor"], cfg["ceil"], cfg["warmth"]
    zoom = dpi / 72.0
    bg = invert_color01(1, 1, 1, floor, ceil, warmth)     # dark page colour
    # The PDF default fill colour is black; text drawn without an explicit
    # colour op relies on it. Re-base the page's default to the inverted
    # black (light) so that implicitly-coloured text still turns light.
    dr, dg, db = invert_color01(0, 0, 0, floor, ceil, warmth)
    default = f"{dr:.4f} {dg:.4f} {db:.4f} rg {dr:.4f} {dg:.4f} {db:.4f} RG\n"

    doc = fitz.open(src_path)
    preserved = rasterized = 0

    for i, page in enumerate(doc):
        ok = False
        if page.get_text().strip() and not _image_dominates(page):
            try:
                updates = {}
                for j, xref in enumerate(page.get_contents()):
                    new = _remap_content_stream(
                        doc.xref_stream(xref).decode("latin-1"),
                        floor, ceil, warmth)
                    if new is None:
                        raise ValueError("unhandled content stream")
                    if j == 0:                    # set light default up front
                        new = default + new
                    updates[xref] = new
                for xref, new in updates.items():
                    doc.update_stream(xref, new.encode("latin-1"))
                # dark background *behind* everything (photos stay on top)
                page.draw_rect(page.rect, color=None, fill=bg, overlay=False)
                preserved += 1
                ok = True
            except Exception:
                ok = False
        if not ok:                                        # scan / unhandled
            png, _ = invert_page_png(page, zoom, floor, ceil, warmth,
                                     images, photo_dim)
            page.insert_image(page.rect, stream=png, overlay=True)
            rasterized += 1
        if verbose:
            print(f"  page {i + 1}/{doc.page_count}", end="\r", flush=True)

    doc.save(out_path, deflate=True, garbage=4)
    doc.close()
    if verbose:
        size_mb = os.path.getsize(out_path) / 1e6
        note = f"{preserved} page(s) keep selectable text"
        if rasterized:
            note += f", {rasterized} rasterized (scanned/unhandled)"
        print(f"\n  done: {out_path}  ({size_mb:.1f} MB, text mode, {note})")


def convert(src_path, out_path, dpi, theme, images, photo_dim,
            mode="image", verbose=True):
    if mode == "text":
        convert_text(src_path, out_path, dpi, theme, images, photo_dim, verbose)
    else:
        convert_image(src_path, out_path, dpi, theme, images, photo_dim, verbose)


def main():
    p = argparse.ArgumentParser(
        description="Convert a PDF to an eye-friendly dark mode for night reading.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Themes: " + ", ".join(THEMES.keys()))
    p.add_argument("input", help="path to the source PDF")
    p.add_argument("-o", "--output", help="output path "
                   "(default: <input>_dark.pdf)")
    p.add_argument("--theme", default="charcoal", choices=list(THEMES),
                   help="color theme (default: charcoal)")
    p.add_argument("--mode", default="image", choices=["image", "text"],
                   help="image = rasterize every page, works on anything incl. "
                        "scans (default); text = keep selectable text by "
                        "recolouring content (born-digital PDFs)")
    p.add_argument("--dpi", type=int, default=150,
                   help="render resolution for image mode / scanned pages; "
                        "higher = crisper but bigger (default: 150)")
    p.add_argument("--images", default="smart",
                   choices=["smart", "keep", "invert"],
                   help="smart = restore only photos (default); "
                        "keep = all images natural; invert = invert everything")
    p.add_argument("--photo-dim", type=float, default=0.9,
                   help="brightness of restored photos, 0-1 "
                        "(default: 0.9, lower = dimmer/less glare)")
    args = p.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"File not found: {args.input}")

    out = args.output
    if not out:
        base, _ = os.path.splitext(args.input)
        out = base + "_dark.pdf"

    print(f"Dark-mode: {args.input}  ->  {args.mode} mode, theme '{args.theme}'")
    convert(args.input, out, args.dpi, args.theme, args.images,
            max(0.0, min(1.0, args.photo_dim)), mode=args.mode)


if __name__ == "__main__":
    main()
