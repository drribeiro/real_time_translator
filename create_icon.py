"""Generate app icon for RealtimeTranslator."""
import struct
import zlib
import os


def create_png(width, height, pixels):
    """Create a minimal PNG file from RGBA pixel data."""
    def chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))

    raw = b''
    for y in range(height):
        raw += b'\x00'  # filter none
        for x in range(width):
            raw += bytes(pixels[y][x])

    idat = chunk(b'IDAT', zlib.compress(raw))
    iend = chunk(b'IEND', b'')

    return header + ihdr + idat + iend


def generate_icon(size=256):
    """Generate a simple translator icon."""
    pixels = []
    cx, cy = size // 2, size // 2
    r = size // 2 - 4

    for y in range(size):
        row = []
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5

            if dist <= r:
                # Blue circle background
                alpha = 255
                if dist > r - 2:
                    alpha = int(255 * (r - dist) / 2)
                    alpha = max(0, min(255, alpha))

                # Gradient from dark blue to light blue
                t = dist / r
                r_c = int(30 + t * 15)
                g_c = int(100 + t * 40)
                b_c = int(220 + t * 35)

                row.append((r_c, g_c, min(255, b_c), alpha))
            else:
                row.append((0, 0, 0, 0))
        pixels.append(row)

    # Draw "T" letter in white
    t_top = size // 4
    t_bottom = size * 3 // 4
    t_left = size // 4
    t_right = size * 3 // 4
    bar_h = size // 10
    stem_w = size // 8

    for y in range(size):
        for x in range(size):
            # Top bar of T
            if t_top <= y <= t_top + bar_h and t_left <= x <= t_right:
                pixels[y][x] = (255, 255, 255, 240)
            # Stem of T
            stem_left = cx - stem_w // 2
            stem_right = cx + stem_w // 2
            if t_top + bar_h < y <= t_bottom and stem_left <= x <= stem_right:
                pixels[y][x] = (255, 255, 255, 240)

    return create_png(size, size, pixels)


if __name__ == "__main__":
    icon_dir = os.path.dirname(__file__)

    # Generate PNG
    png_path = os.path.join(icon_dir, "icon.png")
    png_data = generate_icon(256)
    with open(png_path, "wb") as f:
        f.write(png_data)
    print(f"Created {png_path}")

    # Convert to .icns for macOS app bundle
    icns_path = os.path.join(icon_dir, "icon.icns")
    iconset_dir = os.path.join(icon_dir, "icon.iconset")
    os.makedirs(iconset_dir, exist_ok=True)

    for s in [16, 32, 64, 128, 256, 512]:
        png = generate_icon(s)
        with open(os.path.join(iconset_dir, f"icon_{s}x{s}.png"), "wb") as f:
            f.write(png)
        with open(os.path.join(iconset_dir, f"icon_{s//2}x{s//2}@2x.png"), "wb") as f:
            f.write(png)

    os.system(f"iconutil -c icns {iconset_dir} -o {icns_path}")
    os.system(f"rm -rf {iconset_dir}")
    print(f"Created {icns_path}")
