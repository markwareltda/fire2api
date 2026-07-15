from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from nicegui import ui

THEME_CSS = """
@font-face {
  font-family: Inter;
  src: url('/assets/fonts/InterVariable.woff2') format('woff2');
  font-display: swap;
}
:root {
  --f2-bg: #071226;
  --f2-surface: #0c1932;
  --f2-surface-raised: #112241;
  --f2-surface-muted: #081329;
  --f2-border: #223552;
  --f2-border-strong: #365177;
  --f2-primary: #3b82f6;
  --f2-primary-soft: #132d56;
  --f2-text: #f1f5f9;
  --f2-muted: #9aa9bc;
  --f2-subtle: #718096;
  --f2-positive: #20a575;
  --f2-negative: #fb7185;
  --f2-warning: #fbbf24;
}
html { scroll-behavior: smooth; }
body, .q-layout, .q-page, .nicegui-content {
  font-family: Inter, system-ui, -apple-system, sans-serif;
  background: var(--f2-bg) !important;
  color: var(--f2-text) !important;
}
body { font-size: 14px; line-height: 1.5; }
.nicegui-content { padding: 0 !important; }
.f2-page { width: 100%; padding: 32px; }
.f2-card {
  background: var(--f2-surface) !important;
  border: 1px solid var(--f2-border);
  border-radius: 10px;
  box-shadow: 0 8px 28px rgb(0 0 0 / 12%);
  color: var(--f2-text) !important;
}
.f2-card-flat { box-shadow: none; }
.f2-dialog {
  background: var(--f2-surface) !important;
  border: 1px solid var(--f2-border);
  border-radius: 12px;
  box-shadow: 0 24px 80px rgb(0 0 0 / 38%);
  color: var(--f2-text) !important;
}
.f2-sidebar {
  background: #050d1c !important;
  border-right: 1px solid var(--f2-border) !important;
}
.f2-mobile-header {
  background: rgb(8 14 25 / 94%) !important;
  border-bottom: 1px solid var(--f2-border);
  backdrop-filter: blur(14px);
}
.f2-nav-button {
  width: 100%; min-height: 42px; justify-content: flex-start;
  border-radius: 8px; color: var(--f2-muted) !important;
}
.f2-nav-button .q-btn__content { width: 100%; justify-content: flex-start; gap: 12px; }
.f2-nav-button:hover { background: var(--f2-surface-raised) !important; color: var(--f2-text) !important; }
.f2-nav-active { background: var(--f2-primary-soft) !important; color: #dbeafe !important; }
.f2-sticky-bar {
  position: sticky; top: 0; z-index: 20;
  margin: -8px -8px 20px; padding: 12px 8px;
  background: linear-gradient(to bottom, var(--f2-bg) 75%, transparent);
}
.f2-editor {
  border: 1px solid var(--f2-border);
  border-radius: 9px;
  overflow: hidden;
  min-height: 280px;
}
.f2-editor .cm-editor { min-height: 280px; max-height: 480px; font-size: 14px; }
.f2-editor .cm-scroller { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.f2-param-card { transition: border-color .15s ease, background .15s ease; }
.f2-param-card:hover { border-color: var(--f2-border-strong); }
.f2-empty {
  min-height: 180px; border: 1px dashed var(--f2-border-strong);
  border-radius: 10px; background: var(--f2-surface-muted);
}
.f2-alert { border-radius: 8px; border: 1px solid var(--f2-border); }
.f2-alert-info { background: #10213b; border-color: #294c7a; color: #dbeafe; }
.f2-alert-error { background: #32151c; border-color: #71313f; color: #fecdd3; }
.f2-alert-success { background: #102a22; border-color: #28604d; color: #d1fae5; }
.f2-alert-warning { background: #30240d; border-color: #6f5319; color: #fef3c7; }
.f2-metric { min-height: 124px; }
.f2-route-card { transition: background .15s ease, border-color .15s ease; }
.f2-route-card:hover { background: var(--f2-surface-raised) !important; border-color: var(--f2-border-strong); }
.f2-key-card { transition: background .15s ease, border-color .15s ease; }
.f2-key-card:hover { background: var(--f2-surface-raised) !important; border-color: var(--f2-border-strong); }
.f2-key-prefix {
  display: inline-flex; width: fit-content; padding: 4px 8px;
  border: 1px solid var(--f2-border); border-radius: 6px;
  background: var(--f2-surface-muted); color: #cbd5e1;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
}
.f2-system-card { min-height: 146px; }
.f2-table, .f2-table .q-table__container, .f2-table .q-table,
.f2-table .q-table__middle, .f2-table .q-table__bottom, .f2-table thead,
.f2-table tbody, .f2-table tr, .f2-table th, .f2-table td {
  background: var(--f2-surface) !important; color: var(--f2-text) !important;
}
.f2-table th { color: var(--f2-muted) !important; font-weight: 600; }
.f2-table td, .f2-table th { border-color: var(--f2-border) !important; }
.q-field--dark .q-field__control, .q-field__control {
  background: var(--f2-surface-muted) !important;
  color: var(--f2-text) !important;
  border-radius: 8px;
  min-height: 42px;
}
.q-field__native, .q-field__input, .q-field__prefix, .q-field__suffix,
.q-field__label, .q-field__marginal, .q-select__dropdown-icon,
.q-checkbox__label, .q-toggle__label { color: var(--f2-text) !important; opacity: 1 !important; }
.q-field__label { color: #bdc9d9 !important; }
.q-field__native::placeholder, .q-field__input::placeholder { color: var(--f2-subtle) !important; opacity: 1; }
.q-field--focused .q-field__control:before, .q-field--focused .q-field__control:after {
  border-color: var(--f2-primary) !important;
}
.q-menu, .q-dialog__inner .q-card { background: var(--f2-surface) !important; color: var(--f2-text) !important; }
.q-btn { min-height: 40px; border-radius: 8px; letter-spacing: 0; }
.q-btn .q-btn__content { text-transform: none; font-weight: 600; }
.q-btn--round { min-width: 40px; }
.q-badge { font-weight: 600; letter-spacing: .01em; }
.q-expansion-item { border-radius: 8px; background: var(--f2-surface-muted); }
.q-expansion-item__container > .q-item { min-height: 44px; }
.text-slate-400 { color: var(--f2-muted) !important; }
*:focus-visible { outline: 2px solid #60a5fa !important; outline-offset: 2px; }
@media (max-width: 1023px) {
  .f2-page { padding: 24px; }
  .f2-sticky-bar { top: 64px; }
}
@media (max-width: 639px) {
  .f2-page { padding: 16px; }
  .f2-editor, .f2-editor .cm-editor { min-height: 220px; }
  .f2-sticky-bar { margin: -6px -4px 16px; padding: 8px 4px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; }
}
"""


METHOD_COLORS = {
    "GET": "blue",
    "POST": "positive",
    "PUT": "orange",
    "PATCH": "purple",
    "DELETE": "negative",
}

STATUS_COLORS = {
    "queued": "grey",
    "running": "blue",
    "cancel_requested": "orange",
    "completed": "positive",
    "failed": "negative",
    "canceled": "grey",
    "timeout": "orange",
}

STATUS_LABELS = {
    "queued": "Na fila",
    "running": "Em execução",
    "cancel_requested": "Cancelamento solicitado",
    "completed": "Concluída",
    "failed": "Falhou",
    "canceled": "Cancelada",
    "timeout": "Tempo esgotado",
}


def install_theme() -> None:
    ui.add_css(THEME_CSS)
    ui.dark_mode().enable()
    ui.colors(
        primary="#3b82f6",
        secondary="#7c8da5",
        positive="#20a575",
        negative="#fb7185",
        warning="#fbbf24",
    )


def brand(*, compact: bool = False) -> None:
    with ui.row().classes("items-center gap-3 flex-nowrap"):
        ui.image("/assets/fire2api-logo.svg").props(
            'alt="Fire2API" fit=contain'
        ).classes("w-9 h-9 shrink-0")
        with ui.column().classes("gap-0 min-w-0"):
            ui.label("Fire2API").classes("text-lg font-semibold tracking-tight text-slate-100")
            if not compact:
                ui.link(
                    "By Markware",
                    "https://markware.com.br",
                    new_tab=True,
                ).props('aria-label="Visitar o site da Markware"').classes(
                    "text-xs text-slate-400 no-underline hover:text-blue-400"
                )


@contextmanager
def section_card(classes: str = "") -> Iterator[Any]:
    with ui.card().classes(f"f2-card f2-card-flat w-full p-5 gap-4 {classes}") as card:
        yield card


def page_header(
    title: str,
    subtitle: str,
    *,
    action_label: str | None = None,
    action_icon: str = "add",
    on_action: Callable[..., Any] | None = None,
) -> Any | None:
    with ui.row().classes("w-full items-start justify-between gap-4 flex-wrap mb-6"):
        with ui.column().classes("gap-1 min-w-0"):
            ui.label(title).classes("text-2xl sm:text-3xl font-semibold tracking-tight")
            ui.label(subtitle).classes("text-sm text-slate-400 max-w-3xl")
        if action_label and on_action:
            return (
                ui.button(action_label, icon=action_icon, on_click=on_action)
                .props("unelevated no-caps")
                .classes("shrink-0")
            )
    return None


def empty_state(title: str, description: str, *, icon: str = "inbox") -> None:
    with ui.column().classes("f2-empty w-full items-center justify-center text-center gap-2 p-8"):
        ui.icon(icon).classes("text-4xl text-slate-500")
        ui.label(title).classes("text-base font-semibold")
        ui.label(description).classes("text-sm text-slate-400 max-w-lg")


def alert(message: str, *, kind: str = "info", icon: str | None = None) -> None:
    icons = {
        "info": "info",
        "error": "error_outline",
        "success": "check_circle",
        "warning": "warning_amber",
    }
    with ui.row().classes(f"f2-alert f2-alert-{kind} w-full items-start gap-3 px-4 py-3"):
        ui.icon(icon or icons[kind]).classes("text-xl shrink-0")
        ui.label(message).classes("text-sm grow whitespace-normal")


def method_badge(method: str) -> Any:
    return ui.badge(method, color=METHOD_COLORS.get(method, "grey")).props("outline")


def status_badge(status: str) -> Any:
    return ui.badge(
        STATUS_LABELS.get(status, status.replace("_", " ").title()),
        color=STATUS_COLORS.get(status, "grey"),
    ).props("outline")


def confirm_dialog(
    title: str,
    message: str,
    *,
    confirm_label: str,
    on_confirm: Callable[[], Any],
    danger: bool = True,
) -> None:
    with ui.dialog() as dialog, ui.card().classes(
        "f2-dialog w-[calc(100vw-2rem)] max-w-md p-0 gap-0"
    ):
        with ui.column().classes("w-full gap-2 p-6"):
            ui.label(title).classes("text-xl font-semibold")
            ui.label(message).classes("text-sm text-slate-400 whitespace-normal")
        with ui.row().classes("w-full justify-end gap-2 px-6 py-4 border-t border-slate-700"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")

            def apply() -> None:
                dialog.close()
                on_confirm()

            props = "unelevated no-caps color=negative" if danger else "unelevated no-caps"
            ui.button(confirm_label, on_click=apply).props(props)
    dialog.open()


def format_timestamp(value: Any) -> str:
    if value in (None, ""):
        return "Nunca"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return text
