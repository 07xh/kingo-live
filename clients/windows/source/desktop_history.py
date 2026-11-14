"""Local history diagnostics window. Never modifies a journal or sends HTTP."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core.history_audit import audit_history, format_history


def open_history_window(parent, *, default_sqlite: Path | None = None):
    """Show a user-invoked history inspector with explicitly selected files.

    No bulk migration, no 'clear history' button, and no implied enrollment
    confirmation. Missing files are reported but never created.
    """
    win = tk.Toplevel(parent)
    win.title("本机历史记录体检（只读）")
    win.geometry("790x410")
    win.minsize(640, 340)
    body = ttk.Frame(win, padding=16)
    body.pack(fill="both", expand=True)
    ttk.Label(
        body, text="本机日志对账（只读）",
        font=("Microsoft YaHei UI", 13, "bold")
    ).pack(anchor="w")
    ttk.Label(
        body,
        text="分别选择历史文件。不会上传、修复、合并或清空记录；不同格式的指纹不可直接匹配。",
        wraplength=710,
    ).pack(anchor="w", pady=(5, 12))

    fields: dict[str, tk.StringVar] = {}
    sources = [
        ("Windows V1/V2 JSON", "windows_path", "", (("JSON 文件", "*.json"), ("全部文件", "*.*"))),
        ("Kingo Live JSONL", "live_jsonl_path", "", (("JSONL 文件", "*.jsonl"), ("全部文件", "*.*"))),
        ("跨执行器 SQLite", "shared_sqlite_path",
         str(default_sqlite) if default_sqlite else "",
         (("SQLite 数据库", "*.sqlite3 *.db *.sqlite"), ("全部文件", "*.*"))),
    ]
    for title, key, default, filters in sources:
        row = ttk.Frame(body)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=title, width=20).pack(side="left")
        var = tk.StringVar(value=default)
        fields[key] = var
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=7)

        def browse(v=var, f=filters):
            selected = filedialog.askopenfilename(
                parent=win, title="选择本机历史文件（只读）", filetypes=f)
            if selected:
                v.set(selected)

        ttk.Button(row, text="选择…", command=browse).pack(side="left")

    output = tk.Text(body, height=9, wrap="word", state="disabled")
    output.pack(fill="both", expand=True, pady=(12, 8))

    def refresh():
        try:
            inputs = {
                key: Path(var.get().strip()) if var.get().strip() else None
                for key, var in fields.items()
            }
            result = format_history(audit_history(**inputs))
        except (OSError, ValueError):
            messagebox.showwarning(
                "历史文件检查", "文件路径不可安全读取；请确认所选文件不是符号链接或受限目录。",
                parent=win)
            return
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", result)
        output.configure(state="disabled")

    footer = ttk.Frame(body)
    footer.pack(fill="x")
    ttk.Button(footer, text="只读检查", command=refresh).pack(side="left")
    ttk.Button(footer, text="关闭", command=win.destroy).pack(side="right")
    return win
