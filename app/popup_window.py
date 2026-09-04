import customtkinter
import ctypes
import ctypes.wintypes
import io
import logging
import tkinter as tk
import time

from PIL import Image as PILImage, ImageTk

from app.config import (
    IMAGE_PREVIEW_DELAY,
    IMAGE_PREVIEW_SIZE,
    IMAGE_THUMB_SIZE,
    MAX_CONTENT_LENGTH,
    POPUP_HEIGHT,
    POPUP_WIDTH,
)
from app.runtime_status import format_popup_status

user32 = ctypes.windll.user32
log = logging.getLogger(__name__)

# Fix ctypes prototypes for by-value struct and correct return types
user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
user32.MonitorFromPoint.restype = ctypes.wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL
user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
user32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
user32.GetCursorPos.restype = ctypes.wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

# Color palette
BG = "#0f0f0f"
SURFACE = "#1a1a1a"
SURFACE_HOVER = "#252525"
SURFACE_PINNED = "#1a1f1a"
SURFACE_PINNED_HOVER = "#222822"
SURFACE_SELECTED = "#2a2a2a"
BORDER = "#2a2a2a"
TEXT_PRIMARY = "#e8e8e8"
TEXT_SECONDARY = "#888888"
TEXT_DIM = "#555555"
PIN_COLOR = "#e8b931"
DANGER = "#c44"
IMAGE_BADGE = "#4a6fa5"
SEARCH_BG = "#161616"

PREVIEW_MAX_CHARS = 120
LARGE_TEXT_THRESHOLD = 500

# Fonts (created once, reused)
_FONT_ITEM = ("Segoe UI", 11)
_FONT_SMALL = ("Segoe UI", 9)
_FONT_SECTION = ("Segoe UI", 8)
HISTORY_PAGE_SIZE = 30
PREVIEW_MARGIN = 10
PREVIEW_GAP = 8
CLEAR_UNPINNED_ACTION = "clear_unpinned"
DELETE_ALL_ACTION = "delete_all"
CLEAR_UNPINNED_LABEL = "Clear unpinned"
DELETE_ALL_LABEL = "Delete all"


def relative_time(timestamp):
    diff = time.time() - timestamp
    if diff < 0:
        return "now"
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


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


def _get_monitor_work_area(x, y):
    """Return (left, top, right, bottom) of the work area on the monitor containing (x, y)."""
    MONITOR_DEFAULTTONEAREST = 2
    point = ctypes.wintypes.POINT(x, y)
    hmon = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        rc = info.rcWork
        return rc.left, rc.top, rc.right, rc.bottom
    # Fallback: use primary monitor via SystemMetrics
    w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
    h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
    if w <= 0 or h <= 0:
        w, h = 1920, 1080
    return 0, 0, w, h


def _clamp_window_position(value, size, area_start, area_end, margin):
    if size + margin * 2 <= area_end - area_start:
        lower = area_start + margin
        upper = area_end - size - margin
    else:
        lower = area_start
        upper = area_end - size
    if upper < lower:
        return lower
    return max(lower, min(value, upper))


def _calculate_preview_position(
    popup_x,
    popup_width,
    anchor_y,
    preview_width,
    preview_height,
    work_area,
    margin=PREVIEW_MARGIN,
    gap=PREVIEW_GAP,
):
    left, top, right, bottom = work_area
    right_x = popup_x + popup_width + gap
    left_x = popup_x - preview_width - gap

    if right_x + preview_width + margin <= right:
        preferred_x = right_x
    elif left_x >= left + margin:
        preferred_x = left_x
    else:
        right_room = right - (popup_x + popup_width)
        left_room = popup_x - left
        preferred_x = right_x if right_room >= left_room else left_x

    return (
        _clamp_window_position(preferred_x, preview_width, left, right, margin),
        _clamp_window_position(anchor_y, preview_height, top, bottom, margin),
    )


def _clamp_history_limit(limit, total, page_size=HISTORY_PAGE_SIZE):
    limit = max(page_size, limit)
    if total <= page_size:
        return page_size
    return min(limit, total)


def _format_history_count(loaded, total):
    if total <= 0:
        return "0 items"
    loaded = min(max(loaded, 0), total)
    if loaded >= total:
        return f"{total} item{'s' if total != 1 else ''}"
    return f"{loaded}/{total} items"


def _should_show_load_more(loaded, total):
    return total > 0 and loaded < total


def _format_text_metadata(entry):
    content_len = entry.get("content_len", 0) or 0
    if entry.get("truncated"):
        return f"First {MAX_CONTENT_LENGTH:,} of {content_len:,} chars"
    if content_len >= LARGE_TEXT_THRESHOLD:
        return f"{content_len:,} chars"
    return ""


def _set_bg_recursive(widget, bg):
    """Set background color on widget and all descendants."""
    try:
        widget.configure(bg=bg)
    except Exception:
        pass
    for child in widget.winfo_children():
        _set_bg_recursive(child, bg)


class PopupWindow(customtkinter.CTkToplevel):
    """Persistent popup window — created once, shown/hidden on demand."""

    def __init__(self, master, database, paste_engine, monitor=None):
        super().__init__(master)
        self.db = database
        self.paste_engine = paste_engine
        self.monitor = monitor
        self._visible = False

        self._prev_hwnd = None
        self._selected_index = -1
        self._hovered_index = -1
        self._item_frames = []
        self._item_data = []
        self._search_after_id = None
        self._last_search_text = ""
        self._loaded_limit = HISTORY_PAGE_SIZE
        self._current_search_query = None
        self._total_items = 0
        self._drag_x = 0
        self._drag_y = 0
        self._thumb_cache = {}
        self._preview_window = None
        self._preview_after_id = None
        self._preview_photo = None
        self._preview_entry_id = None
        self._pending_clear_action = None
        self._clear_reset_after_id = None
        self._load_more_btn = None
        self._clear_unpinned_btn = None
        self._delete_all_btn = None
        self._focus_check_id = None
        self._status_label = None
        self._status_label_visible = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG)
        self.geometry(f"{POPUP_WIDTH}x{POPUP_HEIGHT}+0+0")

        self._border_frame = customtkinter.CTkFrame(
            self, fg_color=BG, border_color=BORDER, border_width=1, corner_radius=12
        )
        self._border_frame.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_ui()

        self.bind("<Escape>", lambda e: self.close())
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Up>", lambda e: self._navigate(-1))
        self.bind("<Down>", lambda e: self._navigate(1))
        self.bind("<Return>", lambda e: self._paste_selected())
        self.bind("<Delete>", self._delete_selected)
        self.bind("<Control-p>", lambda e: self._pin_selected())

        # Start hidden — show() will make it visible
        self.withdraw()

    # ------------------------------------------------------------------
    # Show / Close (hide)
    # ------------------------------------------------------------------

    def show(self, prev_hwnd=None):
        """Position near cursor, refresh items, and show the popup."""
        self._prev_hwnd = prev_hwnd
        self._visible = True

        self._position_popup(cursor=_get_cursor_pos())

        # Reset search
        self._last_search_text = ""
        try:
            self.search_entry.delete(0, "end")
        except Exception:
            pass

        self._reset_clear_confirm(force=True)

        # Load fresh data
        self._load_items(reset=True)

        # Reset scroll to top
        try:
            self._canvas.yview_moveto(0)
        except Exception:
            pass

        # Show and focus
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(10, self._focus_window)

    def _position_popup(self, *, cursor=None, position=None, work_area=None):
        if cursor is not None:
            cx, cy = cursor
            work_area = _get_monitor_work_area(cx, cy)
        left, top, right, bottom = work_area
        scaling = self._get_window_scaling()
        # CTk scales dimensions, but Windows cursor/work-area coordinates and
        # geometry offsets are physical pixels. Reduce size on small displays.
        width = min(POPUP_WIDTH, max(1, int((right - left - 20) / scaling)))
        height = min(POPUP_HEIGHT, max(1, int((bottom - top - 20) / scaling)))
        physical_width = round(width * scaling)
        physical_height = round(height * scaling)
        if position is None:
            position = (cx - physical_width // 2, cy - 40)
        x = _clamp_window_position(position[0], physical_width, left, right, 10)
        y = _clamp_window_position(position[1], physical_height, top, bottom, 10)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _set_scaling(self, new_widget_scaling, new_window_scaling):
        position = None
        if getattr(self, "_visible", False):
            position = (self.winfo_x(), self.winfo_y())
            work_area = _get_monitor_work_area(
                position[0] + self.winfo_width() // 2,
                position[1] + self.winfo_height() // 2,
            )
        super()._set_scaling(new_widget_scaling, new_window_scaling)
        # Release CTk's temporary fixed size: show() may immediately need a
        # different size for the target monitor, even while currently hidden.
        self._set_scaled_min_max()
        if position is not None:
            self._position_popup(position=position, work_area=work_area)

    def close(self):
        """Hide the popup (does not destroy it)."""
        if not self._visible:
            return
        self._visible = False

        # Cancel pending timers
        self._hide_image_preview()
        if self._focus_check_id:
            try:
                self.after_cancel(self._focus_check_id)
            except Exception:
                pass
            self._focus_check_id = None
        if self._search_after_id:
            try:
                self.after_cancel(self._search_after_id)
            except Exception:
                pass
            self._search_after_id = None
        self._reset_clear_confirm(force=True)

        self.withdraw()

    @property
    def is_visible(self):
        return self._visible

    # ------------------------------------------------------------------
    # Focus
    # ------------------------------------------------------------------

    def _focus_window(self):
        if not self._visible:
            return
        try:
            self.focus_force()
            self.search_entry.focus_set()
        except Exception:
            pass

    def focus(self):
        if not self._visible:
            return
        self._focus_window()
        self.lift()

    # ------------------------------------------------------------------
    # UI (shell built once with CTk; list items use plain tk)
    # ------------------------------------------------------------------

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

        self._status_label = customtkinter.CTkLabel(
            header, text="", font=("Segoe UI", 10), text_color=DANGER
        )

        close_btn = customtkinter.CTkButton(
            header, text="\u00d7", width=26, height=26,
            font=("Segoe UI", 14), fg_color="transparent",
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

        # Scrollable list — plain tk Canvas + Frame (much faster than CTkScrollableFrame)
        list_container = tk.Frame(self._border_frame, bg=BG)
        list_container.pack(fill="both", expand=True, padx=6, pady=(2, 2))

        self._canvas = tk.Canvas(
            list_container, bg=BG, highlightthickness=0, borderwidth=0
        )
        self._scrollbar = tk.Scrollbar(
            list_container, orient="vertical", command=self._canvas.yview,
            bg="#1a1a1a", troughcolor=BG, width=8,
            activebackground="#444444", highlightthickness=0, borderwidth=0,
        )
        self._items_inner = tk.Frame(self._canvas, bg=BG)
        self._items_inner_id = self._canvas.create_window(
            (0, 0), window=self._items_inner, anchor="nw"
        )

        def _on_inner_configure(_e):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _on_canvas_configure(e):
            self._canvas.itemconfigure(self._items_inner_id, width=e.width)

        self._items_inner.bind("<Configure>", _on_inner_configure)
        self._canvas.bind("<Configure>", _on_canvas_configure)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self._canvas.bind("<MouseWheel>", _on_mousewheel)
        self._items_inner.bind("<MouseWheel>", _on_mousewheel)
        # Also bind on the toplevel so wheel works everywhere
        self.bind("<MouseWheel>", _on_mousewheel)

        self._canvas.configure(yscrollincrement=20)

        # Footer
        footer = customtkinter.CTkFrame(c, fg_color="transparent", height=30)
        footer.pack(fill="x", padx=14, pady=(0, 8))
        footer.pack_propagate(False)

        self.count_label = customtkinter.CTkLabel(
            footer, text="", font=("Segoe UI", 10), text_color=TEXT_DIM
        )
        self.count_label.pack(side="left")

        self._load_more_btn = customtkinter.CTkButton(
            footer, text="Load more", width=76, height=22,
            font=("Segoe UI", 10), fg_color="transparent",
            hover_color=SURFACE_HOVER, text_color=TEXT_SECONDARY,
            corner_radius=4, command=self._load_more_items
        )

        self._delete_all_btn = customtkinter.CTkButton(
            footer, text=DELETE_ALL_LABEL, width=68, height=22,
            font=("Segoe UI", 10), fg_color="transparent",
            hover_color=SURFACE_HOVER, text_color=TEXT_SECONDARY,
            corner_radius=4, command=self._delete_all
        )
        self._delete_all_btn.pack(side="right")

        self._clear_unpinned_btn = customtkinter.CTkButton(
            footer, text=CLEAR_UNPINNED_LABEL, width=96, height=22,
            font=("Segoe UI", 10), fg_color="transparent",
            hover_color=SURFACE_HOVER, text_color=TEXT_SECONDARY,
            corner_radius=4, command=self._clear_unpinned
        )
        self._clear_unpinned_btn.pack(side="right", padx=(0, 6))

    def set_status_snapshot(self, snapshot, recording_paused=False):
        text = format_popup_status(snapshot, recording_paused=recording_paused)
        if not self._status_label:
            return
        text_color = DANGER if snapshot else PIN_COLOR
        self._status_label.configure(text=text, text_color=text_color)
        if text and not self._status_label_visible:
            self._status_label.pack(side="left", padx=(8, 0))
            self._status_label_visible = True
        elif not text and self._status_label_visible:
            self._status_label.pack_forget()
            self._status_label_visible = False

    # ------------------------------------------------------------------
    # Item list (all plain tk for speed)
    # ------------------------------------------------------------------

    def _load_items(self, search_query=None, reset=False, preserve_scroll=False):
        if not self._visible:
            return

        self._hide_image_preview()
        scroll_position = None
        if preserve_scroll:
            try:
                scroll_position = self._canvas.yview()[0]
            except Exception:
                scroll_position = None

        if reset:
            self._current_search_query = search_query
            self._loaded_limit = HISTORY_PAGE_SIZE

        for widget in self._items_inner.winfo_children():
            widget.destroy()

        self._item_frames = []
        self._item_data = []
        self._selected_index = -1
        self._hovered_index = -1
        old_cache = self._thumb_cache
        self._thumb_cache = {}

        entries, self._total_items = self.db.get_history_page(
            limit=max(HISTORY_PAGE_SIZE, self._loaded_limit),
            search_query=self._current_search_query,
        )
        self._loaded_limit = _clamp_history_limit(self._loaded_limit, self._total_items)

        if not entries:
            empty = tk.Label(
                self._items_inner,
                text=("No matches\nTry a different search" if self._current_search_query
                      else "Nothing here yet\nCopy something to get started"),
                font=("Segoe UI", 12), fg=TEXT_DIM, bg=BG, justify="center"
            )
            empty.pack(pady=50)
            self._update_history_footer(0)
            return

        has_pinned = any(e["pinned"] for e in entries)
        shown_unpinned_header = False

        for entry in entries:
            if has_pinned and not entry["pinned"] and not shown_unpinned_header:
                shown_unpinned_header = True
                tk.Label(
                    self._items_inner, text="HISTORY",
                    font=_FONT_SECTION, fg=TEXT_DIM, bg=BG, anchor="w"
                ).pack(fill="x", padx=10, pady=(6, 2))

            if has_pinned and entry["pinned"] and not shown_unpinned_header and len(self._item_frames) == 0:
                tk.Label(
                    self._items_inner, text="PINNED",
                    font=_FONT_SECTION, fg=PIN_COLOR, bg=BG, anchor="w"
                ).pack(fill="x", padx=10, pady=(2, 2))

            idx = len(self._item_frames)
            frame = self._create_item_widget(entry, idx, old_cache)
            self._item_frames.append(frame)
            self._item_data.append(entry)

        self._update_history_footer(len(entries))
        if scroll_position is not None:
            try:
                self._canvas.update_idletasks()
                self._canvas.yview_moveto(scroll_position)
            except Exception:
                pass

    def _update_history_footer(self, loaded):
        self.count_label.configure(text=_format_history_count(loaded, self._total_items))
        if _should_show_load_more(loaded, self._total_items):
            try:
                self._load_more_btn.configure(state="normal")
                if not self._load_more_btn.winfo_manager():
                    self._load_more_btn.pack(side="right", padx=(0, 6))
            except Exception:
                pass
        else:
            try:
                self._load_more_btn.pack_forget()
            except Exception:
                pass

    def _load_more_items(self):
        if not self._visible:
            return
        if self._loaded_limit >= self._total_items:
            return
        self._loaded_limit += HISTORY_PAGE_SIZE
        self._load_items(reset=False, preserve_scroll=True)

    def _create_item_widget(self, entry, index, old_thumb_cache=None):
        is_pinned = entry["pinned"]
        is_image = entry["content_type"] == "image"
        normal_bg = SURFACE_PINNED if is_pinned else SURFACE
        hover_bg = SURFACE_PINNED_HOVER if is_pinned else SURFACE_HOVER

        frame = tk.Frame(self._items_inner, bg=normal_bg, cursor="hand2", padx=0, pady=0)
        frame.pack(fill="x", padx=3, pady=1)

        clickable = [frame]

        if is_image:
            row = tk.Frame(frame, bg=normal_bg)
            row.pack(fill="x", padx=10, pady=5)
            clickable.append(row)

            thumb = self._create_thumbnail(row, entry["id"], normal_bg, old_thumb_cache)
            if thumb:
                thumb.pack(side="left", padx=(0, 8))
                clickable.append(thumb)

            info = tk.Frame(row, bg=normal_bg)
            info.pack(side="left", fill="x", expand=True)
            clickable.append(info)

            badge = tk.Label(
                info, text=f"IMAGE  \u00b7  {entry['preview'] or 'Image'}",
                font=_FONT_ITEM, fg=IMAGE_BADGE, bg=normal_bg, anchor="w"
            )
            badge.pack(anchor="w")
            clickable.append(badge)

            bot = tk.Frame(info, bg=normal_bg)
            bot.pack(fill="x")
            clickable.append(bot)
        else:
            preview_text = entry["preview"] or ""

            if len(preview_text) > PREVIEW_MAX_CHARS:
                preview_text = preview_text[:PREVIEW_MAX_CHARS] + "..."

            preview = tk.Label(
                frame, text=preview_text,
                font=_FONT_ITEM, fg=TEXT_PRIMARY, bg=normal_bg,
                anchor="w", justify="left",
                wraplength=POPUP_WIDTH - 120
            )
            preview.pack(fill="x", padx=10, pady=(5, 0))
            clickable.append(preview)

            bot = tk.Frame(frame, bg=normal_bg)
            bot.pack(fill="x", padx=10, pady=(1, 4))
            clickable.append(bot)

        # Time label
        time_text = relative_time(entry["timestamp"])
        if is_pinned:
            time_text = "Pinned \u00b7 " + time_text

        time_lbl = tk.Label(
            bot, text=time_text, font=_FONT_SMALL,
            fg=PIN_COLOR if is_pinned else TEXT_DIM, bg=normal_bg
        )
        time_lbl.pack(side="left")
        clickable.append(time_lbl)

        metadata = _format_text_metadata(entry) if not is_image else ""
        if metadata:
            chars_lbl = tk.Label(
                bot, text="  \u00b7  " + metadata,
                font=_FONT_SMALL,
                fg=TEXT_SECONDARY if entry.get("truncated") else TEXT_DIM,
                bg=normal_bg
            )
            chars_lbl.pack(side="left")
            clickable.append(chars_lbl)

        # Action "buttons" — plain tk labels with hover effects
        del_btn = tk.Label(
            bot, text="Del", font=_FONT_SMALL,
            fg=TEXT_DIM, bg=normal_bg, cursor="hand2", padx=4
        )
        del_btn.pack(side="right")
        del_btn.bind("<Enter>", lambda _e, w=del_btn: w.configure(fg=DANGER))
        del_btn.bind("<Leave>", lambda _e, w=del_btn: w.configure(fg=TEXT_DIM))
        del_btn.bind("<Button-1>", lambda _e, eid=entry["id"]: self._delete_item(eid))

        pin_text = "Unpin" if is_pinned else "Pin"
        pin_btn = tk.Label(
            bot, text=pin_text, font=_FONT_SMALL,
            fg=TEXT_SECONDARY, bg=normal_bg, cursor="hand2", padx=4
        )
        pin_btn.pack(side="right")
        pin_btn.bind("<Enter>", lambda _e, w=pin_btn: w.configure(fg=TEXT_PRIMARY))
        pin_btn.bind("<Leave>", lambda _e, w=pin_btn: w.configure(fg=TEXT_SECONDARY))
        pin_btn.bind("<Button-1>", lambda _e, eid=entry["id"]: self._toggle_pin(eid))

        # Hover
        def on_enter(_e):
            self._hovered_index = index
            if index != self._selected_index:
                _set_bg_recursive(frame, hover_bg)
            if is_image:
                if self._preview_entry_id != entry["id"]:
                    self._hide_image_preview()
                    self._preview_after_id = self.after(
                        IMAGE_PREVIEW_DELAY,
                        lambda: self._show_image_preview(entry["id"], frame)
                    )

        def on_leave(_e):
            # Check if cursor is still inside the frame
            try:
                w = self.winfo_containing(_e.x_root, _e.y_root)
                while w is not None:
                    if w is frame:
                        return
                    w = w.master
            except Exception:
                pass
            self._hovered_index = -1
            if index != self._selected_index:
                _set_bg_recursive(frame, normal_bg)
            if is_image:
                self._hide_image_preview()

        frame.bind("<Enter>", on_enter)
        frame.bind("<Leave>", on_leave)

        for w in clickable:
            w.bind("<Button-1>", lambda _e, eid=entry["id"]: self._on_item_click(eid))

        return frame

    # ------------------------------------------------------------------
    # Thumbnails & image preview
    # ------------------------------------------------------------------

    def _create_thumbnail(self, parent, entry_id, bg_color, old_cache=None):
        try:
            if old_cache and entry_id in old_cache:
                tk_img = old_cache[entry_id]
                label = tk.Label(parent, image=tk_img, bg=bg_color, borderwidth=0)
                self._thumb_cache[entry_id] = tk_img
                return label

            image_data = self.db.get_image_data(entry_id)
            if not image_data:
                return None

            img = PILImage.open(io.BytesIO(image_data))
            try:
                img.thumbnail(IMAGE_THUMB_SIZE, PILImage.Resampling.LANCZOS)
                tk_img = ImageTk.PhotoImage(img)
            finally:
                img.close()

            label = tk.Label(parent, image=tk_img, bg=bg_color, borderwidth=0)
            self._thumb_cache[entry_id] = tk_img
            return label
        except Exception:
            return None

    def _show_image_preview(self, entry_id, widget):
        self._hide_image_preview()
        if not self._visible:
            return
        try:
            image_data = self.db.get_image_data(entry_id)
            if not image_data:
                return

            img = PILImage.open(io.BytesIO(image_data))
            try:
                img.thumbnail(IMAGE_PREVIEW_SIZE, PILImage.Resampling.LANCZOS)
                tk_img = ImageTk.PhotoImage(img)
            finally:
                img.close()

            preview_win = tk.Toplevel(self)
            self._preview_window = preview_win
            self._preview_entry_id = entry_id
            preview_win.overrideredirect(True)
            preview_win.attributes("-topmost", True)
            preview_win.configure(bg=BG)

            border = tk.Frame(
                preview_win, bg=BG, highlightbackground=BORDER,
                highlightthickness=1
            )
            border.pack(fill="both", expand=True, padx=1, pady=1)

            self._preview_photo = tk_img

            label = tk.Label(border, image=tk_img, bg=BG, borderwidth=0)
            label.pack(padx=6, pady=6)

            self.update_idletasks()
            preview_win.update_idletasks()
            pw = preview_win.winfo_reqwidth()
            ph = preview_win.winfo_reqheight()
            popup_x = self.winfo_x()
            popup_w = self.winfo_width()

            ml, mt, mr, mb = _get_monitor_work_area(popup_x + popup_w // 2, self.winfo_y())

            try:
                wy = widget.winfo_rooty()
            except Exception:
                wy = self.winfo_y()
            px, py = _calculate_preview_position(
                popup_x,
                popup_w,
                wy,
                pw,
                ph,
                (ml, mt, mr, mb),
            )

            preview_win.geometry(f"+{px}+{py}")
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
        self._preview_entry_id = None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search_change(self, event=None):
        if not self._visible:
            return
        try:
            current = self.search_entry.get()
        except Exception:
            return
        if current == self._last_search_text:
            return
        self._last_search_text = current
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(150, self._do_search)

    def _do_search(self):
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
            self._search_after_id = None
        if not self._visible:
            return
        try:
            query = self.search_entry.get().strip() or None
        except Exception:
            return
        self._load_items(query, reset=True)

    def _ensure_current_search(self):
        """Refresh stale rows and cancel the action that targeted their old selection."""
        if not self._visible:
            return False
        query = self.search_entry.get().strip() or None
        if query != self._current_search_query:
            self._do_search()
            return False
        return True

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _get_item_normal_bg(self, index):
        if 0 <= index < len(self._item_data):
            return SURFACE_PINNED if self._item_data[index]["pinned"] else SURFACE
        return SURFACE

    def _navigate(self, direction):
        self._ensure_current_search()
        if not self._item_frames:
            return

        if 0 <= self._selected_index < len(self._item_frames):
            _set_bg_recursive(
                self._item_frames[self._selected_index],
                self._get_item_normal_bg(self._selected_index)
            )

        if 0 <= self._hovered_index < len(self._item_frames) and self._hovered_index != self._selected_index:
            _set_bg_recursive(
                self._item_frames[self._hovered_index],
                self._get_item_normal_bg(self._hovered_index)
            )

        self._selected_index += direction
        self._selected_index = max(0, min(self._selected_index, len(self._item_frames) - 1))
        _set_bg_recursive(self._item_frames[self._selected_index], SURFACE_SELECTED)

        # Scroll selected item into view
        try:
            frame = self._item_frames[self._selected_index]
            self._canvas.update_idletasks()
            bbox = self._canvas.bbox("all")
            if bbox is None:
                return
            total_height = bbox[3]
            if total_height <= 0:
                return
            fy = frame.winfo_y()
            fh = frame.winfo_height()
            canvas_h = self._canvas.winfo_height()
            visible_top = self._canvas.canvasy(0)
            visible_bottom = visible_top + canvas_h
            if fy < visible_top:
                self._canvas.yview_moveto(fy / total_height)
            elif fy + fh > visible_bottom:
                self._canvas.yview_moveto((fy + fh - canvas_h) / total_height)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _paste_selected(self):
        if not self._ensure_current_search():
            return
        if 0 <= self._selected_index < len(self._item_data):
            entry_id = self._item_data[self._selected_index]["id"]
            self._on_item_click(entry_id)

    def _delete_selected(self, event=None):
        # Entry class bindings run before the toplevel binding: Delete should
        # edit the query without also deleting a selected history row.
        if event is not None and event.widget.winfo_class() in ("Entry", "TEntry", "Text"):
            return
        if not self._ensure_current_search():
            return
        if 0 <= self._selected_index < len(self._item_data):
            entry_id = self._item_data[self._selected_index]["id"]
            self._delete_item(entry_id)

    def _pin_selected(self):
        if not self._ensure_current_search():
            return
        if 0 <= self._selected_index < len(self._item_data):
            entry_id = self._item_data[self._selected_index]["id"]
            self._toggle_pin(entry_id)

    def _on_item_click(self, entry_id):
        if not self._ensure_current_search():
            return
        entry = self.db.get_entry(entry_id)
        if not entry:
            return

        prev_hwnd = self._prev_hwnd
        content = entry["content"]
        content_type = entry.get("content_type", "text")
        image_data = entry.get("image_data") if content_type == "image" else None

        self.close()

        start_result = self.paste_engine.paste(
            content,
            content_type,
            prev_hwnd,
            self.monitor,
            image_data=image_data,
            on_complete=lambda completion: self._schedule_paste_completion(
                entry_id,
                completion,
            ),
        )
        if not start_result.started:
            log.warning(
                "Paste did not start for entry %s: %s",
                entry_id,
                start_result.reason,
            )

    def _schedule_paste_completion(self, entry_id, completion):
        try:
            self.after(0, lambda: self._handle_paste_completion(entry_id, completion))
        except Exception:
            log.exception("Failed to schedule paste completion for entry %s", entry_id)

    def _handle_paste_completion(self, entry_id, completion):
        if completion.success:
            self.db.touch_entry(entry_id)
        else:
            log.warning(
                "Paste attempt failed for entry %s: sent %s/%s input events",
                entry_id,
                completion.send_input_count,
                completion.expected_input_count,
            )

    def _toggle_pin(self, entry_id):
        if not self._ensure_current_search():
            return
        self.db.toggle_pin(entry_id)
        self._load_items(reset=False)

    def _delete_item(self, entry_id):
        if not self._ensure_current_search():
            return
        self.db.delete_entry(entry_id)
        self._load_items(reset=False)

    def _clear_unpinned(self):
        self._confirm_clear_action(CLEAR_UNPINNED_ACTION)

    def _delete_all(self):
        self._confirm_clear_action(DELETE_ALL_ACTION)

    def _confirm_clear_action(self, action):
        if self._pending_clear_action == action:
            self._run_clear_action(action)
            self._reset_clear_confirm(force=True)
            self._load_items(reset=False)
            return

        self._pending_clear_action = action
        self._cancel_clear_reset_timer()
        self._configure_clear_buttons()
        try:
            self._clear_reset_after_id = self.after(2000, self._reset_clear_confirm)
        except Exception:
            log.debug("Failed to schedule clear confirmation reset", exc_info=True)
            self._clear_reset_after_id = None

    def _run_clear_action(self, action):
        if action == CLEAR_UNPINNED_ACTION:
            return self.db.clear_unpinned()
        if action == DELETE_ALL_ACTION:
            return self.db.clear_all()
        log.warning("Unknown clear action: %s", action)
        return 0

    def _cancel_clear_reset_timer(self):
        if not self._clear_reset_after_id:
            return
        try:
            self.after_cancel(self._clear_reset_after_id)
        except Exception:
            pass
        self._clear_reset_after_id = None

    def _reset_clear_confirm(self, force=False):
        if not force and not self._visible:
            return
        self._pending_clear_action = None
        self._cancel_clear_reset_timer()
        self._configure_clear_buttons()

    def _configure_clear_buttons(self):
        states = {
            CLEAR_UNPINNED_ACTION: (CLEAR_UNPINNED_LABEL, TEXT_SECONDARY),
            DELETE_ALL_ACTION: (DELETE_ALL_LABEL, TEXT_SECONDARY),
        }
        if self._pending_clear_action == CLEAR_UNPINNED_ACTION:
            states[CLEAR_UNPINNED_ACTION] = ("Clear?", DANGER)
        elif self._pending_clear_action == DELETE_ALL_ACTION:
            states[DELETE_ALL_ACTION] = ("Delete all?", DANGER)

        if self._clear_unpinned_btn:
            try:
                text, color = states[CLEAR_UNPINNED_ACTION]
                self._clear_unpinned_btn.configure(text=text, text_color=color)
            except Exception:
                pass
        if self._delete_all_btn:
            try:
                text, color = states[DELETE_ALL_ACTION]
                self._delete_all_btn.configure(text=text, text_color=color)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Focus management
    # ------------------------------------------------------------------

    def _on_focus_out(self, event):
        if not self._visible:
            return
        if self._focus_check_id is not None:
            self.after_cancel(self._focus_check_id)
        self._focus_check_id = self.after(80, lambda: self._check_focus(0))

    @staticmethod
    def _get_tk_hwnd(widget):
        """Get native window handle for a Tk widget, or 0 on failure."""
        try:
            return widget.winfo_id()
        except Exception:
            return 0

    def _check_focus(self, attempt):
        self._focus_check_id = None
        if not self._visible:
            return
        try:
            focused = self.focus_get()
            if focused is not None:
                return
            if self._preview_window is not None:
                try:
                    preview_focused = self._preview_window.focus_get()
                    if preview_focused is not None:
                        return
                except Exception:
                    pass
            foreground = user32.GetForegroundWindow()
            own_hwnd = self._get_tk_hwnd(self)
            if own_hwnd and foreground == own_hwnd:
                return
            if self._preview_window is not None:
                preview_hwnd = self._get_tk_hwnd(self._preview_window)
                if preview_hwnd and foreground == preview_hwnd:
                    return
            if attempt < 1:
                self._focus_check_id = self.after(80, lambda: self._check_focus(attempt + 1))
                return
            self.close()
        except Exception:
            self.close()

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.winfo_x() + event.x - self._drag_x
        y = self.winfo_y() + event.y - self._drag_y
        self.geometry(f"+{x}+{y}")
