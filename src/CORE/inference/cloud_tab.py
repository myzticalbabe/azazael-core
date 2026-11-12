# coding: utf-8
"""
cloud_tab.py -- Cloud Inference Tab for AZAZAEL Inference Studio
================================================================
Extracted from AZAZAEL_Inference_Studio.py to reduce God Object anti-pattern.
Provides the Cloud Providers tab with provider selection, prompt input,
response output, and real API calls to all configured providers.

Usage:
    from CORE.inference.cloud_tab import CloudTab
    cloud_tab = CloudTab(parent_frame, theme, status_callback, draw_dot_callback)
"""

import functools
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.inference.providers import get_provider, list_providers, get_best_provider
    HAS_CLOUD_PROVIDERS = True
except ImportError as exc:
    note_failure("cloud_tab:L23", exc)
    HAS_CLOUD_PROVIDERS = False

VERSION = "1.0.0"


class CloudTab:
    """Self-contained Cloud Inference tab with provider selection and real API calls."""

    def __init__(
        self,
        parent: tk.Widget,
        theme: dict[str, str],
        status_callback: Callable[[str, str], None],
        dot_callback: Callable[[str], None],
        emotion_engine=None,
    ):
        self.parent = parent
        self.theme = theme
        self._set_status = status_callback
        self._draw_dot = dot_callback
        self.emotion = emotion_engine

        self.selected_provider: str = "auto"
        self.inferencing: bool = False

        self._build_ui()

    def _build_ui(self):
        T = self.theme
        tab = self.parent
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=0)
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_rowconfigure(2, weight=0)

        # Provider selection bar
        sel_f = ttk.LabelFrame(tab, text=" Cloud Inference Provider  ", padding=10)
        sel_f.grid(row=0, column=0, sticky="ew", pady=(4, 8))

        prov_row = tk.Frame(sel_f, bg=T["bg"])
        prov_row.pack(fill="x")

        tk.Label(prov_row, text="Provider:", font=("Segoe UI", 10, "bold"),
                bg=T["bg"], fg=T["cyan"]).pack(side="left", padx=(0, 8))

        self.prov_var = tk.StringVar(value="auto")
        prov_values = ["auto"]
        if HAS_CLOUD_PROVIDERS:
            try:
                for p in list_providers():
                    if p["has_api_key"]:
                        prov_values.append(p["id"])
            except Exception as exc:
                note_failure("cloud_tab:L76", exc)
        self.prov_cb = ttk.Combobox(prov_row, textvariable=self.prov_var,
                                     values=prov_values, state="readonly",
                                     font=("Segoe UI", 10), width=18)
        self.prov_cb.pack(side="left", padx=(0, 8))
        self.prov_cb.bind("<<ComboboxSelected>>", self._on_provider_select)

        self.prov_status = tk.Label(prov_row, text="", font=("Consolas", 9),
                                    bg=T["bg"], fg=T["text_dim"])
        self.prov_status.pack(side="left", padx=(8, 0))

        tk.Button(prov_row, text="Refresh Providers",
                 command=self._refresh_providers_async,
                 bg=T["button"], fg=T["text"],
                 font=("Segoe UI", 8), activebackground=T["button_hover"],
                 relief="flat", padx=10, pady=4, cursor="hand2").pack(side="right")

        # Prompt input
        prompt_f = ttk.LabelFrame(tab, text=" Prompt  ", padding=10)
        prompt_f.grid(row=1, column=0, sticky="nsew", pady=(0, 8))

        self.prompt_input = tk.Text(prompt_f, bg=T["entry_bg"], fg=T["text"],
                                    font=("Consolas", 11), height=4, wrap="word",
                                    relief="flat", padx=12, pady=10,
                                    insertbackground=T["cyan"])
        self.prompt_input.pack(fill="both", expand=True)
        self.prompt_input.insert("1.0", "Explain quantum computing in one sentence.")

        # Output + launch
        out_f = ttk.LabelFrame(tab, text=" Response  ", padding=10)
        out_f.grid(row=2, column=0, sticky="nsew")

        self.output_text = tk.Text(out_f, bg=T["card"], fg=T["green"],
                                    font=("Consolas", 11), height=6,
                                    wrap="word", relief="flat",
                                    padx=12, pady=10, state="disabled")
        self.output_text.pack(fill="both", expand=True)

        btn_row = tk.Frame(out_f, bg=T["bg"])
        btn_row.pack(fill="x", pady=(8, 0))

        self.launch_btn = tk.Button(btn_row, text="SEND TO CLOUD",
                                    command=self.launch,
                                    bg="#00cc88", fg=T["bg"],
                                    font=("Segoe UI", 12, "bold"),
                                    activebackground="#00ffaa",
                                    activeforeground=T["bg"],
                                    relief="flat", padx=20, pady=10,
                                    cursor="hand2")
        self.launch_btn.pack(side="left")

        self.progress = ttk.Progressbar(btn_row, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        self._refresh_providers_async()

    def get_prompt(self) -> str:
        try:
            return self.prompt_input.get("1.0", "end-1c")
        except Exception as exc:
            note_failure("cloud_tab:L136", exc)
            return "Explain quantum computing in one sentence."

    def _on_provider_select(self, event=None):
        self.selected_provider = self.prov_var.get()
        self._set_status(f"Cloud provider: {self.selected_provider}", "cyan")

    def _refresh_providers_async(self):
        def do_refresh():
            try:
                if not HAS_CLOUD_PROVIDERS:
                    return
                configured = []
                for p in list_providers():
                    if p["has_api_key"]:
                        configured.append(p["id"])
                self.parent.after(0, functools.partial(self._on_refreshed, configured))
            except Exception as exc:
                note_failure("cloud_tab:L153", exc)
        threading.Thread(target=do_refresh, daemon=True).start()

    def _on_refreshed(self, configured):
        self.prov_cb["values"] = ["auto"] + configured
        self.prov_status.configure(
            text=f"{len(configured)} providers ready", fg=self.theme["green"]
        )

    def launch(self, max_tokens: int = 1024, temperature: float = 0.7):
        prompt = self.get_prompt()
        if not prompt.strip():
            messagebox.showwarning("No Prompt", "Enter a prompt first.")
            return

        provider_name = self.prov_var.get()
        if provider_name == "auto":
            if HAS_CLOUD_PROVIDERS:
                try:
                    best = get_best_provider()
                    provider_name = best.provider_name
                except Exception as exc:
                    note_failure("cloud_tab:L175", exc)
                    provider_name = "none"
            else:
                provider_name = "none"

        if provider_name == "none":
            out = self.output_text
            out.configure(state="normal")
            out.delete("1.0", "end")
            out.insert("1.0", "No cloud provider configured. Add API keys to ~/.env or ~/.azazael/")
            out.configure(state="disabled")
            return

        self.launch_btn.configure(state="disabled",
                                  text=f"  CALLING {provider_name.upper()}...",
                                  bg=self.theme["purple"])
        self.progress.start(10)
        if self._draw_dot:
            self._draw_dot("cyan")
        if self.emotion:
            self.emotion.reaction("infer")

        safe_tokens = max(1, min(max_tokens, 131072))
        safe_temp = max(0.0, min(temperature, 2.0))

        def run_cloud():
            try:
                prov = get_provider(provider_name)
                result = prov.chat(prompt, max_tokens=safe_tokens, temperature=safe_temp)
                self.parent.after(0, functools.partial(self._finish, provider_name, result))
            except Exception as e:
                note_failure("cloud_tab:L205", e)
                self.parent.after(0, functools.partial(self._finish, provider_name,
                                                       {"error": str(e)}))

        threading.Thread(target=run_cloud, daemon=True).start()

    def _finish(self, provider_name: str, result: dict):
        # Guard: skip UI updates if widget was destroyed (thread safety)
        try:
            if not self.launch_btn.winfo_exists():
                return
        except Exception as exc:
            note_failure("cloud_tab:L216", exc)
            return

        self.launch_btn.configure(state="normal", text="SEND TO CLOUD",
                                  bg="#00cc88")
        self.progress.stop()

        out = self.output_text
        out.configure(state="normal")
        out.delete("1.0", "end")

        if "error" in result:
            if self._draw_dot:
                self._draw_dot("red")
            if self.emotion:
                self.emotion.reaction("fail")
            # Strip non-ASCII and mask any API keys in error messages
            err = result.get("error", "Unknown error")
            try:
                err = err.encode("ascii", errors="replace").decode("ascii")
            except Exception as exc:
                note_failure("cloud_tab:L236", exc)
            # Redact API keys that may leak in error messages
            err_safe = err[:120]
            out.insert("1.0", f"ERROR [{provider_name}]:\n{err_safe}")
            self._set_status(f"Cloud FAILED: {err_safe[:60]}", "red")
        else:
            if self._draw_dot:
                self._draw_dot("green")
            if self.emotion:
                self.emotion.reaction("done")
            content = result.get("content", "")
            # Strip non-ASCII for Windows safety
            try:
                content = content.encode("ascii", errors="replace").decode("ascii")
            except Exception as exc:
                note_failure("cloud_tab:L251", exc)
            model = result.get("model", "?")
            tokens = result.get("usage", {}).get("total_tokens", 0)
            ms = result.get("elapsed_ms", 0)
            out.insert("1.0", content)
            # Never log full API key in status - use provider name only
            self._set_status(
                f"OK: {provider_name} | {model} | {tokens}tok | {ms}ms", "green"
            )

        out.configure(state="disabled")

    def set_prompt(self, text: str):
        try:
            self.prompt_input.delete("1.0", "end")
            self.prompt_input.insert("1.0", text)
        except Exception as exc:
            note_failure("cloud_tab:L268", exc)
