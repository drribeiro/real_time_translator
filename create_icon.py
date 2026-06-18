"""Generate app icons from SVG for RealtimeTranslator."""
import os
import subprocess
import sys


def svg_to_png(svg_path, png_path, size):
    """Convert SVG to PNG using sips (macOS built-in)."""
    # First try with qlmanage (handles SVG well on macOS)
    tmp = png_path + ".tmp.png"
    subprocess.run(
        ["qlmanage", "-t", "-s", str(size), "-o", os.path.dirname(png_path), svg_path],
        capture_output=True,
    )
    # qlmanage adds .png to the filename
    ql_output = svg_path + ".png"
    if os.path.exists(ql_output):
        subprocess.run(["sips", "-z", str(size), str(size), ql_output, "--out", png_path],
                       capture_output=True)
        os.unlink(ql_output)
        return True
    return False


def generate_icons():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    svg_path = os.path.join(script_dir, "icon.svg")

    if not os.path.exists(svg_path):
        print("icon.svg not found!")
        return

    # Generate main icon PNG
    icon_png = os.path.join(script_dir, "icon.png")
    svg_to_png(svg_path, icon_png, 256)
    print(f"Created {icon_png}")

    # Generate iconset for .icns
    iconset_dir = os.path.join(script_dir, "icon.iconset")
    os.makedirs(iconset_dir, exist_ok=True)

    sizes = [16, 32, 64, 128, 256, 512]
    for s in sizes:
        png = os.path.join(iconset_dir, f"icon_{s}x{s}.png")
        svg_to_png(svg_path, png, s)
        # @2x version
        png2x = os.path.join(iconset_dir, f"icon_{s//2}x{s//2}@2x.png")
        if s >= 32:
            svg_to_png(svg_path, png2x, s)

    # Generate .icns
    icns_path = os.path.join(script_dir, "icon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset_dir, "-o", icns_path],
                   capture_output=True)

    # Cleanup iconset
    subprocess.run(["rm", "-rf", iconset_dir], capture_output=True)
    print(f"Created {icns_path}")

    # Generate tray icon (small, white on transparent for menu bar)
    tray_png = os.path.join(script_dir, "tray_icon.png")
    svg_to_png(svg_path, tray_png, 22)
    print(f"Created {tray_png}")


if __name__ == "__main__":
    generate_icons()
