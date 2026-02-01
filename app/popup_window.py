import customtkinter
import ctypes
import ctypes.wintypes
import io
import time

from PIL import Image as PILImage, ImageTk

from app.config import POPUP_WIDTH, POPUP_HEIGHT, IMAGE_THUMB_SIZE, IMAGE_PREVIEW_SIZE, IMAGE_PREVIEW_DELAY

user32 = ctypes.windll.user32

# Color palette
BG = "#0f0f0f"
SURFACE = "#1a1a1a"
SURFACE_HOVER = "#252525"
SURFACE_PINNED = "#1a1f1a"
SURFACE_PINNED_HOVER = "#222822"
SURFACE_SELECTED = "#2a2a2a"
BORDER = "#2a2a2a"
ACCENT_DIM = "#3d4f8a"
TEXT_PRIMARY = "#e8e8e8"
TEXT_SECONDARY = "#888888"
TEXT_DIM = "#555555"
PIN_COLOR = "#e8b931"
DANGER = "#c44"
IMAGE_BADGE = "#4a6fa5"
SEARCH_BG = "#161616"

PREVIEW_MAX_CHARS = 120
LARGE_TEXT_THRESHOLD = 500


def relative_time(timestamp):
    diff = time.time() - timestamp
    if diff < 60:
        return "now"
    elif diff < 3600:
        return f"{int(diff // 60)}m"
    elif diff < 86400:
        return f"{int(diff // 3600)}h"
    elif diff < 172800:
        return "1d"
    else:
        return f"{int(diff // 86400)}d"


def _get_cursor_pos():
    point = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


class PopupWindow(customtkinter.CTkToplevel):
    def __init__(self, master, database, paste_engine, monitor=None):
        super().__init__(master)
        self.db = database
        self.paste_engine = paste_engine
        self.monitor = monitor
        self._master = master
        self._closed = False

        self._prev_hwnd = user32.GetForegroundWindow()
        self._selected_index = -1
        self._item_frames = []
        self._item_data = []
        self._current_entries = []
        self._search_after_id = None
        self._drag_x = 0
        self._drag_y = 0
        self._thumb_cache = {}
        self._preview_window = None
        self._preview_after_id = None
        self._preview_photo = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG)

        cx, cy = _get_cursor_pos()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(10, min(cx - POPUP_WIDTH // 2, sw - POPUP_WIDTH - 10))
        y = max(10, min(cy - 40, sh - POPUP_HEIGHT - 40))
        self.geometry(f"{POPUP_WIDTH}x{POPUP_HEIGHT}+{x}+{y}")

        self._border_frame = customtkinter.CTkFrame(
            self, fg_color=BG, border_color=BORDER, border_width=1, corner_radius=12
        )
        self._border_frame.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_ui()
        self._load_items()

        self.bind("<Escape>", lambda e: self.close())
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Up>", lambda e: self._navigate(-1))
        self.bind("<Down>", lambda e: self._navigate(1))
        self.bind("<Return>", lambda e: self._paste_selected())

        self.after(10, self._focus_window)

    def _focus_window(self):
        if self._closed:
            return
        try:
            self.focus_force()
            self.search_entry.focus_set()
        except Exception:
            pass

    def _build_ui(self):
        c = self._border_frame

        # Header
        header = customtkinter.CTkFrame(c, fg_color="transparent", height=40)
        header.pack(fill="x", padx=14, pady=(10, 0))
        header.pack_propagate(False)

        title = customtkinter.CTkLabel(
            header, text="Clipboard",
            font=("Segoe UI Semibold", 15), text_color=TEXT_PRIMARY
        )
        title.pack(side="left")

        close_btn = customtkinter.CTkButton(
            header, text="", width=26, height=26,
            font=("Segoe UI", 12), fg_color="transparent",
            hover_color=SURFACE_HOVER, text_color=TEXT_DIM,
            corner_radius=6, command=self.close
        )
        close_btn.pack(side="right")

        for w in [header, title]:
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        # Search
        self.search_entry = customtkinter.CTkEntry(
            c, placeholder_text="Search...",
            font=("Segoe UI", 12), height=34,
            fg_color=SEARCH_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_PRIMARY, placeholder_text_color=TEXT_DIM,
            corner_radius=8
        )
        self.search_entry.pack(fill="x", padx=14, pady=(8, 4))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)

        # List
        self.items_frame = customtkinter.CTkScrollableFrame(
            c, fg_color="transparent",
            scrollbar_button_color="#333333",
            scrollbar_button_hover_color="#444444"
        )
        self.items_frame.pack(fill="both", expand=True, padx=6, pady=(2, 2))

        # Footer
        footer = customtkinter.CTkFrame(c, fg_color="transparent", height=30)
        footer.pack(fill="x", padx=14, pady=(0, 8))
        footer.pack_propagate(False)

        self.count_label = customtkinter.CTkLabel(
            footer, text="", font=("Segoe UI", 10), text_color=TEXT_DIM
        )
        self.count_label.pack(side="left")

        clear_btn = customtkinter.CTkButton(
            footer, text="Clear all", width=60, height=22,
            font=("Segoe UI", 10), fg_color="transparent",
            hover_color=SURFACE_HOVER, text_color=TEXT_SECONDARY,
            corner_radius=4, command=self._clear_all
        )
        clear_btn.pack(side="right")

    def _load_items(self, search_query=None):
        if self._closed:
            return

        for widget in self.items_frame.winfo_children():
            widget.destroy()

        self._item_frames = []
        self._item_data = []
        self._selected_index = -1
        self._thumb_cache = {}

        entries = self.db.get_history(limit=100, search_query=search_query)
        self._current_entries = entries

        if not entries:
            empty = customtkinter.CTkLabel(
                self.items_frame,
                text="Nothing here yet\nCopy something to get started",
                font=("Segoe UI", 12), text_color=TEXT_DIM, justify="center"
            )
            empty.pack(pady=50)
            self.count_label.configure(text="0 items")
            return

        has_pinned = any(e["pinned"] for e in entries)
        shown_unpinned_header = False

        for entry in entries:
            if has_pinned and not entry["pinned"] and not shown_unpinned_header:
                shown_unpinned_header = True
                customtkinter.CTkLabel(
                    self.items_frame, text="HISTORY",
                    font=("Segoe UI", 8), text_color=TEXT_DIM, anchor="w"
                ).pack(fill="x", padx=10, pady=(6, 2))

            if has_pinned and entry["pinned"] and not shown_unpinned_header and len(self._item_frames) == 0:
                customtkinter.CTkLabel(
                    self.items_frame, text="PINNED",
                    font=("Segoe UI", 8), text_color=PIN_COLOR, anchor="w"
                ).pack(fill="x", padx=10, pady=(2, 2))

            idx = len(self._item_frames)
            frame = self._create_item_widget(entry, idx)
            self._item_frames.append(frame)
            self._item_data.append(entry)

        n = len(entries)
        self.count_label.configure(text=f"{n} item{'s' if n != 1 else ''}")

    def _create_item_widget(self, entry, index):
        is_pinned = entry["pinned"]
        is_image = entry["content_type"] == "image"
        normal_bg = SURFACE_PINNED if is_pinned else SURFACE
        hover_bg = SURFACE_PINNED_HOVER if is_pinned else SURFACE_HOVER

        frame = customtkinter.CTkFrame(
            self.items_frame, fg_color=normal_bg,
            corner_radius=6, cursor="hand2"
        )
        frame.pack(fill="x", padx=3, pady=1)

        clickable = [frame]

        if is_image:
            # Image: thumbnail + info in one row
            row = customtkinter.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=5)
            clickable.append(row)

            thumb = self._create_thumbnail(row, entry["id"])
            if thumb:
                thumb.pack(side="left", padx=(0, 8))
                clickable.append(thumb)

            info = customtkinter.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            clickable.append(info)

            badge = customtkinter.CTkLabel(
                info, text=f"IMAGE  ·  {entry['preview'] or 'Image'}",
                font=("Segoe UI", 11), text_color=IMAGE_BADGE, anchor="w"
            )
            badge.pack(anchor="w")
            clickable.append(badge)

            # Bottom: time + buttons
            bot = customtkinter.CTkFrame(info, fg_color="transparent")
            bot.pack(fill="x")
            clickable.append(bot)
        else:
            # Text: preview
            preview_text = entry["preview"] or ""
            content_len = entry.get("content_len", 0) or 0

            if len(preview_text) > PREVIEW_MAX_CHARS:
                preview_text = preview_text[:PREVIEW_MAX_CHARS] + "..."

            preview = customtkinter.CTkLabel(
                frame, text=preview_text,
                font=("Segoe UI", 11), text_color=TEXT_PRIMARY,
                anchor="w", justify="left",
                wraplength=POPUP_WIDTH - 120
            )
            preview.pack(fill="x", padx=10, pady=(5, 0))
            clickable.append(preview)

            # Bottom row: time + chars + buttons
            bot = customtkinter.CTkFrame(frame, fg_color="transparent")
            bot.pack(fill="x", padx=10, pady=(1, 4))
            clickable.append(bot)

            if content_len >= LARGE_TEXT_THRESHOLD:
                customtkinter.CTkLabel(
                    bot, text=f"  ·  {content_len:,} chars",
                    font=("Segoe UI", 9), text_color=TEXT_DIM
                ).pack(side="left")

        # Time label (shared for both types)
        time_text = relative_time(entry["timestamp"])
        if is_pinned:
            time_text = "Pinned · " + time_text

        time_lbl = customtkinter.CTkLabel(
            bot, text=time_text,
            font=("Segoe UI", 9),
            text_color=PIN_COLOR if is_pinned else TEXT_DIM
        )
        time_lbl.pack(side="left")
        clickable.append(time_lbl)

        # Action buttons - inline on the right of the bottom row
        del_btn = customtkinter.CTkButton(
            bot, text="Del", width=28, height=18,
            font=("Segoe UI", 9), fg_color="transparent",
            hover_color=DANGER, text_color=TEXT_DIM,
            corner_radius=3,
            command=lambda eid=entry["id"]: self._delete_item(eid)
        )
        del_btn.pack(side="right")

        pin_btn = customtkinter.CTkButton(
            bot, text="Unpin" if is_pinned else "Pin",
            width=32, height=18,
            font=("Segoe UI", 9), fg_color="transparent",
            hover_color=ACCENT_DIM, text_color=TEXT_SECONDARY,
            corner_radius=3,
            command=lambda eid=entry["id"]: self._toggle_pin(eid)
        )
        pin_btn.pack(side="right", padx=(0, 2))

        # Hover
        def on_enter(_e):
            if index != self._selected_index:
                frame.configure(fg_color=hover_bg)
            if is_image:
                self._hide_image_preview()
                self._preview_after_id = self.after(
                    IMAGE_PREVIEW_DELAY,
                    lambda: self._show_image_preview(entry["id"], frame)
                )

        def on_leave(_e):
            if index != self._selected_index:
                frame.configure(fg_color=normal_bg)
            if is_image:
                self._hide_image_preview()

        frame.bind("<Enter>", on_enter)
        frame.bind("<Leave>", on_leave)

        for w in clickable:
            w.bind("<Button-1>", lambda _e, eid=entry["id"]: self._on_item_click(eid))

        return frame

    def _create_thumbnail(self, parent, entry_id):
        try:
            image_data = self.db.get_image_data(entry_id)
            if not image_data:
                return None

            img = PILImage.open(io.BytesIO(image_data))
            img.thumbnail(IMAGE_THUMB_SIZE, PILImage.Resampling.LANCZOS)
            tk_img = ImageTk.PhotoImage(img)

            label = customtkinter.CTkLabel(parent, image=tk_img, text="")
            self._thumb_cache[entry_id] = tk_img
            return label
        except Exception:
            return None

    def _show_image_preview(self, entry_id, widget):
        self._hide_image_preview()
        try:
            image_data = self.db.get_image_data(entry_id)
            if not image_data:
                return

            img = PILImage.open(io.BytesIO(image_data))
            img.thumbnail(IMAGE_PREVIEW_SIZE, PILImage.Resampling.LANCZOS)

            preview_win = customtkinter.CTkToplevel(self)
            preview_win.overrideredirect(True)
            preview_win.attributes("-topmost", True)
            preview_win.configure(fg_color=BG)

            border = customtkinter.CTkFrame(
                preview_win, fg_color=BG, border_color=BORDER,
                border_width=1, corner_radius=8
            )
            border.pack(fill="both", expand=True, padx=1, pady=1)

            tk_img = ImageTk.PhotoImage(img)
            self._preview_photo = tk_img

            label = customtkinter.CTkLabel(border, image=tk_img, text="")
            label.pack(padx=6, pady=6)

            # Position: right of popup, or left if no space
            self.update_idletasks()
            preview_win.update_idletasks()
            pw = preview_win.winfo_reqwidth()
            ph = preview_win.winfo_reqheight()
            popup_x = self.winfo_x()
            popup_w = self.winfo_width()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()

            if popup_x + popup_w + pw + 10 < sw:
                px = popup_x + popup_w + 8
            else:
                px = popup_x - pw - 8

            # Vertically align with the hovered widget
            try:
                wy = widget.winfo_rooty()
            except Exception:
                wy = self.winfo_y()
            py = max(10, min(wy, sh - ph - 10))

            preview_win.geometry(f"+{px}+{py}")
            self._preview_window = preview_win
        except Exception:
            self._hide_image_preview()

    def _hide_image_preview(self):
        if self._preview_after_id:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        if self._preview_window:
            try:
                self._preview_window.destroy()
            except Exception:
                pass
            self._preview_window = None
        self._preview_photo = None

    def _on_search_change(self, event=None):
        if self._closed:
            return
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(
            150, lambda: self._load_items(self.search_entry.get().strip() or None)
        )

    def _navigate(self, direction):
        if not self._item_frames:
            return

        if 0 <= self._selected_index < len(self._item_frames):
            e = self._item_data[self._selected_index]
            bg = SURFACE_PINNED if e["pinned"] else SURFACE
            self._item_frames[self._selected_index].configure(fg_color=bg)

        self._selected_index += direction
        self._selected_index = max(0, min(self._selected_index, len(self._item_frames) - 1))
        self._item_frames[self._selected_index].configure(fg_color=SURFACE_SELECTED)

    def _paste_selected(self):
        if 0 <= self._selected_index < len(self._current_entries):
            entry_id = self._current_entries[self._selected_index]["id"]
            self._on_item_click(entry_id)

    def _on_item_click(self, entry_id):
        entry = self.db.get_entry(entry_id)
        if not entry:
            return

        prev_hwnd = self._prev_hwnd
        content = entry["content"]
        content_type = entry.get("content_type", "text")
        image_data = entry.get("image_data") if content_type == "image" else None
        monitor = self.monitor
        paste_engine = self.paste_engine

        self.close()

        self._master.after(50, lambda: paste_engine.paste(
            content, content_type, prev_hwnd, monitor, image_data=image_data
        ))

    def _toggle_pin(self, entry_id):
        self.db.toggle_pin(entry_id)
        search = self.search_entry.get().strip() or None
        self._load_items(search)

    def _delete_item(self, entry_id):
        self.db.delete_entry(entry_id)
        search = self.search_entry.get().strip() or None
        self._load_items(search)

    def _clear_all(self):
        self.db.clear_all()
        self._load_items()

    def _on_focus_out(self, event):
        if self._closed:
            return
        self.after(100, self._check_focus)

    def _check_focus(self):
        if self._closed:
            return
        try:
            focused = self.focus_get()
            if focused is None:
                self.close()
        except Exception:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._hide_image_preview()
        if self._search_after_id:
            try:
                self.after_cancel(self._search_after_id)
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.winfo_x() + event.x - self._drag_x
        y = self.winfo_y() + event.y - self._drag_y
        self.geometry(f"+{x}+{y}")

    def focus(self):
        self._focus_window()
        self.lift()
