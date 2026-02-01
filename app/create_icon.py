"""Generate app icon (clipboard) as PNG and ICO."""
from PIL import Image, ImageDraw

from app.config import ICON_PATH, ICO_PATH


def create_icon():
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Clipboard board (rounded rect)
    board_x1, board_y1 = 10, 8
    board_x2, board_y2 = 54, 58
    draw.rounded_rectangle(
        [board_x1, board_y1, board_x2, board_y2],
        radius=5, fill="#2a2a5e", outline="#7c83ff", width=2
    )

    # Clip at top
    clip_w = 18
    clip_x1 = (size - clip_w) // 2
    draw.rounded_rectangle(
        [clip_x1, 2, clip_x1 + clip_w, 14],
        radius=3, fill="#7c83ff", outline="#9999ff", width=1
    )

    # Text lines on clipboard
    for i, y in enumerate([22, 30, 38, 46]):
        w = 28 if i < 3 else 18
        x_start = 18
        draw.rounded_rectangle(
            [x_start, y, x_start + w, y + 4],
            radius=1, fill="#7c83ff"
        )

    img.save(ICON_PATH, "PNG")
    img.save(ICO_PATH, format="ICO", sizes=[(64, 64)])


if __name__ == "__main__":
    create_icon()
    print("Icon created.")
