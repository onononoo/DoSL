"""
dosl.ui.gui -- the public counter.

A tkinter window that looks like a form you would be handed and then told
to fill in again. The left half is generated entirely from
:class:`~dosl.bureau.forms.Form27B6`: adding a field to the form adds a
widget here, with the right kind of control, automatically.

The right half is where the Department explains itself: the certificate,
the long report, a per-department breakdown, the disassembled bytecode that
produced it, the Permanent Record, and a diagnostics page for when none of
it works.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from ..bureau import Authority
from ..bureau import certificate as cert  # the module, not the function
from ..bureau.departments import Department, Verdict
from ..bureau.forms import Form27B6, FormField, ValidationError
from ..formats import sdwx

MONO = ("Consolas", 9)
MONO_SMALL = ("Consolas", 8)
STAMP_FONT = ("Consolas", 16, "bold")

#: Rubber-stamp ink colours. Government green, government red.
VERDICT_COLOUR = {
    Verdict.APPROVED: "#1c6b34",
    Verdict.CONDITIONAL: "#5c6b1c",
    Verdict.PROVISIONAL: "#8a6d1f",
    Verdict.REFERRED: "#8a4a1f",
    Verdict.DENIED: "#8f1d1d",
}

PAPER = "#f4f1e8"
INK = "#2b2b2b"


class FieldWidget:
    """Binds one :class:`FormField` to whichever Tk control suits it."""

    def __init__(self, parent: tk.Widget, field: FormField, row: int) -> None:
        self.field = field
        label = ttk.Label(parent, text=field.label + ":")
        label.grid(row=row, column=0, sticky="w", padx=(4, 8), pady=2)
        if field.help:
            _tooltip(label, field.help)

        self.var: tk.Variable
        if field.kind == "bool":
            self.var = tk.BooleanVar(value=bool(field.default))
            widget = ttk.Checkbutton(parent, variable=self.var, text="yes")
        elif field.kind == "choice":
            self.var = tk.StringVar(value=str(field.default))
            widget = ttk.Combobox(parent, textvariable=self.var,
                                  values=field.choices(), state="readonly", width=24)
        elif field.kind == "number":
            self.var = tk.StringVar(value=str(field.default))
            widget = ttk.Spinbox(parent, textvariable=self.var, from_=field.low,
                                 to=field.high, width=8, increment=1)
        elif field.kind == "list":
            self.var = tk.StringVar(value=", ".join(field.default or []))
            widget = ttk.Combobox(parent, textvariable=self.var,
                                  values=field.choices(), width=26)
        else:
            self.var = tk.StringVar(value=str(field.default))
            suggestions = field.choices()
            widget = (ttk.Combobox(parent, textvariable=self.var,
                                   values=suggestions, width=26)
                      if suggestions else
                      ttk.Entry(parent, textvariable=self.var, width=28))

        widget.grid(row=row, column=1, sticky="ew", padx=(0, 6), pady=2)
        self.widget = widget

    def value(self):
        return self.var.get()

    def set(self, value) -> None:
        if self.field.kind == "bool":
            self.var.set(bool(value))
        elif self.field.kind == "list":
            self.var.set(", ".join(value) if isinstance(value, list) else str(value))
        else:
            self.var.set(str(value))


def _tooltip(widget: tk.Widget, text: str) -> None:
    """A tooltip, because the help text has to live somewhere."""
    window: list[tk.Toplevel] = []

    def show(_event=None):
        if window:
            return
        top = tk.Toplevel(widget)
        top.wm_overrideredirect(True)
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 2
        top.wm_geometry(f"+{x}+{y}")
        tk.Label(top, text=text, background="#ffffe0", relief="solid",
                 borderwidth=1, font=("Segoe UI", 8), justify="left",
                 wraplength=320).pack()
        window.append(top)

    def hide(_event=None):
        while window:
            window.pop().destroy()

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)


class Counter(tk.Tk):
    """The main window. Counter 4. Counters 1 through 3 are closed."""

    def __init__(self, authority: Authority) -> None:
        super().__init__()
        self.authority = authority
        self.adjudication = None
        self.entry = None

        self.title("Department of Sandwich Legitimacy -- Counter 4")
        self.geometry("1180x760")
        self.minsize(900, 600)
        try:
            self.call("tk", "scaling", 1.25)
        except tk.TclError:
            pass

        self._build_menu()
        self._build_layout()
        self._refresh_record()
        self._refresh_diagnostics()
        self.status.set(
            f"Ready. Entropy engine: {authority.engine.backend}. "
            f"Please do not lean on the counter.")

    # ------------------------------------------------------------ chrome

    def _build_menu(self) -> None:
        menu = tk.Menu(self)

        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Save dossier as .sdwx...",
                              command=self.save_dossier, accelerator="Ctrl+S")
        file_menu.add_command(label="Open dossier...", command=self.open_dossier,
                              accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menu.add_cascade(label="File", menu=file_menu)

        record_menu = tk.Menu(menu, tearoff=False)
        record_menu.add_command(label="Refresh", command=self._refresh_record)
        record_menu.add_command(label="Verify hash chain", command=self.verify_record)
        record_menu.add_command(label="Statistics", command=self.show_statistics)
        menu.add_cascade(label="Permanent Record", menu=record_menu)

        engine_menu = tk.Menu(menu, tearoff=False)
        engine_menu.add_command(label="Rebuild bureau_entropy.dll",
                                command=self.rebuild_dll)
        engine_menu.add_command(label="Recompile all policies",
                                command=self.recompile_policies)
        engine_menu.add_command(label="Refresh diagnostics",
                                command=self._refresh_diagnostics)
        menu.add_cascade(label="Engine", menu=engine_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="About the Department", command=self.about)
        menu.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menu)
        self.bind("<Control-s>", lambda _e: self.save_dossier())
        self.bind("<Control-o>", lambda _e: self.open_dossier())
        self.bind("<Control-Return>", lambda _e: self.submit())
        self.bind("<F5>", lambda _e: self.submit())

    def _build_layout(self) -> None:
        outer = ttk.Panedwindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True, padx=6, pady=(6, 0))

        left = ttk.Frame(outer)
        outer.add(left, weight=0)
        self._build_form(left)

        right = ttk.Frame(outer)
        outer.add(right, weight=1)
        self._build_tabs(right)

        self.status = tk.StringVar(value="")
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        ttk.Separator(bar, orient="horizontal").pack(fill="x")
        ttk.Label(bar, textvariable=self.status, anchor="w",
                  font=("Segoe UI", 8)).pack(fill="x", padx=8, pady=3)

    def _build_form(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text=f"FORM {Form27B6.code}", font=("Consolas", 11, "bold")
                  ).pack(anchor="w", padx=6, pady=(2, 0))
        ttk.Label(parent, text=Form27B6.title, font=("Segoe UI", 9)
                  ).pack(anchor="w", padx=6)
        ttk.Label(parent, text="All fields are compulsory, including the optional ones.",
                  font=("Segoe UI", 8, "italic"), foreground="#666"
                  ).pack(anchor="w", padx=6, pady=(0, 6))

        self.widgets: dict[str, FieldWidget] = {}
        blank = Form27B6()
        for section, fields in blank.sections().items():
            box = ttk.LabelFrame(parent, text=section)
            box.pack(fill="x", padx=6, pady=3)
            box.columnconfigure(1, weight=1)
            for row, field in enumerate(fields):
                self.widgets[field.name] = FieldWidget(box, field, row)

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", padx=6, pady=8)
        submit = ttk.Button(buttons, text="SUBMIT FOR ADJUDICATION  (F5)",
                            command=self.submit)
        submit.pack(fill="x")
        ttk.Button(buttons, text="Reset form", command=self.reset
                   ).pack(fill="x", pady=(4, 0))
        ttk.Button(buttons, text="Surprise the Department", command=self.randomise
                   ).pack(fill="x", pady=(4, 0))

    def _build_tabs(self, parent: ttk.Frame) -> None:
        self.stamp = tk.Label(parent, text="AWAITING SUBMISSION", font=STAMP_FONT,
                              foreground="#999", background=PAPER, pady=6)
        self.stamp.pack(fill="x")

        self.tabs = ttk.Notebook(parent)
        self.tabs.pack(fill="both", expand=True, pady=(4, 0))

        self.certificate_view = self._text_tab("Certificate")
        self.report_view = self._text_tab("Full report")

        breakdown = ttk.Frame(self.tabs)
        self.tabs.add(breakdown, text="Departments")
        columns = ("score", "weight", "veto", "findings", "adjustments")
        self.tree = ttk.Treeview(breakdown, columns=columns, show="tree headings")
        self.tree.heading("#0", text="Department")
        self.tree.column("#0", width=300, stretch=True)
        for column, width in zip(columns, (70, 60, 50, 70, 90)):
            self.tree.heading(column, text=column.title())
            self.tree.column(column, width=width, anchor="center", stretch=False)
        self.tree.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(breakdown, orient="vertical", command=self.tree.yview)
        scroll.pack(fill="y", side="right")
        self.tree.configure(yscrollcommand=scroll.set)

        disassembly = ttk.Frame(self.tabs)
        self.tabs.add(disassembly, text="Bytecode")
        picker = ttk.Frame(disassembly)
        picker.pack(fill="x", pady=2)
        ttk.Label(picker, text="Policy:").pack(side="left", padx=(4, 4))
        self.policy_choice = tk.StringVar(value=Department.all()[0].slug)
        chooser = ttk.Combobox(
            picker, textvariable=self.policy_choice, state="readonly", width=20,
            values=[d.slug for d in Department.all()])
        chooser.pack(side="left")
        chooser.bind("<<ComboboxSelected>>", lambda _e: self._show_disassembly())
        self.show_source = tk.BooleanVar(value=False)
        ttk.Checkbutton(picker, text="show source", variable=self.show_source,
                        command=self._show_disassembly).pack(side="left", padx=8)
        self.disassembly_view = _make_text(disassembly)
        self._show_disassembly()

        self.record_view = self._text_tab("Permanent Record")
        self.diagnostics_view = self._text_tab("Diagnostics")

    def _text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text=title)
        return _make_text(frame)

    # ------------------------------------------------------------ actions

    def collect(self) -> Form27B6:
        return Form27B6(**{name: widget.value()
                           for name, widget in self.widgets.items()})

    def submit(self) -> None:
        try:
            form = self.collect()
            form.require_valid()
        except ValidationError as exc:
            self.stamp.configure(text="RETURNED FOR CORRECTION", foreground="#8a4a1f")
            self.status.set("The form has been returned. See the Certificate tab.")
            _replace(self.certificate_view,
                     "FORM 27-B/6 RETURNED FOR CORRECTION\n"
                     + "=" * 60 + f"\n\n{exc}\n\n"
                     + "Please correct the above and resubmit. The Department\n"
                       "appreciates your continued cooperation.\n")
            self.tabs.select(0)
            return

        self.status.set("Convening departments...")
        self.update_idletasks()

        self.adjudication, self.entry = self.authority.adjudicate(form)
        adjudication = self.adjudication

        self.stamp.configure(text=adjudication.verdict.label,
                             foreground=VERDICT_COLOUR[adjudication.verdict])
        _replace(self.certificate_view, cert.render(adjudication))
        _replace(self.report_view, cert.full_report(adjudication))
        self._fill_tree(adjudication)
        self._refresh_record()
        self.tabs.select(0)
        self.status.set(
            f"{adjudication.reference} -- {adjudication.verdict.label} "
            f"at {adjudication.score:.2f}. "
            f"Sealed into the Permanent Record as entry {self.entry.index}.")

    def _fill_tree(self, adjudication) -> None:
        self.tree.delete(*self.tree.get_children())
        for result in adjudication.results:
            parent = self.tree.insert(
                "", "end", text=result.title,
                values=(f"{result.score:.2f}" if result.outcome else "--",
                        f"{result.weight:.1f}",
                        "yes" if result.veto else "",
                        len(result.findings),
                        len(result.outcome.adjustments) if result.outcome else 0))
            if result.error:
                self.tree.insert(parent, "end", text=f"[{result.error}]")
                continue
            for finding in result.outcome.findings:
                self.tree.insert(
                    parent, "end",
                    text=f"{finding.kind.name} sev {finding.severity}: {finding.message}")
            for adjustment in result.outcome.adjustments:
                self.tree.insert(
                    parent, "end",
                    text=f"{adjustment.delta:+g}  {adjustment.reason}")
            for note in result.outcome.notes:
                self.tree.insert(parent, "end", text=f"note: {note}")
            self.tree.item(parent, open=True)

    def reset(self) -> None:
        blank = Form27B6()
        for name, widget in self.widgets.items():
            widget.set(getattr(blank, name))
        self.status.set("Form reset. The previous form has not been forgotten.")

    def randomise(self) -> None:
        """Fill the form with something the Department will regret seeing."""
        import random

        from ..bureau.forms import (
            ACCOMPANIMENTS, BREADS, CHEESES, COMMON_CONDIMENTS, COMMON_FILLINGS,
            CUTS, VENUES,
        )
        names = ["R. Milquetoast", "D. Chaosworth", "P. Fenwick-Bland",
                 "T. Burrito", "M. Underhill", "Q. Pemberton-Snape"]
        purposes = ["lunch", "breakfast", "an emergency", "a working lunch",
                    "spite", "a second breakfast, unapologetically"]

        def draw() -> dict:
            layers = random.randint(1, 4)
            return {
                "applicant": random.choice(names),
                "declared_purpose": random.choice(purposes),
                "clearance": random.randint(0, 9),
                "bread": random.choice(BREADS),
                "layers": layers,
                # A single slice cannot be 40mm tall; the form's own
                # cross-check says so, so do not roll one.
                "height_mm": random.randint(20, 40 if layers == 1 else 110),
                "cut": random.choice(CUTS),
                "crusts_removed": random.random() < 0.3,
                "toasted": random.random() < 0.5,
                "fillings": random.sample(COMMON_FILLINGS, random.randint(1, 5)),
                "condiments": random.sample(COMMON_CONDIMENTS, random.randint(0, 4)),
                "cheese": random.choice(CHEESES),
                "consumed_at": random.choice(VENUES),
                "hour": random.randint(5, 23),
                "urgency": random.randint(0, 9),
                "accompaniment": random.choice(ACCOMPANIMENTS),
            }

        # The button promises an absurd sandwich, not an invalid form: keep
        # drawing until the paperwork at least survives its own validation.
        picks = draw()
        for _ in range(24):
            if not Form27B6(**picks).problems():
                break
            picks = draw()

        for name, value in picks.items():
            self.widgets[name].set(value)
        self.status.set("Form completed at random. This is also how it is assessed.")

    # -------------------------------------------------------------- views

    def _show_disassembly(self) -> None:
        from ..bureau.departments import DepartmentMeta

        slug = self.policy_choice.get()
        try:
            source_path = self.authority.policy_sources()[slug]
            code = DepartmentMeta.registry[slug].policy()
        except Exception as exc:
            _replace(self.disassembly_view, f"cannot load {slug}: {exc}")
            return

        text: list[str] = []
        if self.show_source.get():
            for number, line in enumerate(
                source_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                text.append(f"{number:4d} | {line}")
            text.append("")
        text.extend(code.listing())
        _replace(self.disassembly_view, "\n".join(text))

    def _refresh_record(self) -> None:
        ledger = self.authority.ledger
        lines = [f"Permanent Record: {ledger.path}", ""]
        entries = ledger.tail(60)
        if not entries:
            lines.append("  (empty -- no sandwich has yet been adjudicated)")
        else:
            lines.append(f"  {'#':>4}  {'reference':<26} {'verdict':<12} "
                         f"{'score':>6}  {'hash':<14} applicant")
            for entry in entries:
                lines.append(
                    f"  {entry.index:>4}  {entry.reference:<26} {entry.verdict:<12} "
                    f"{entry.score:>6.2f}  {entry.short:<14} {entry.applicant}")
        _replace(self.record_view, "\n".join(lines))

    def _refresh_diagnostics(self) -> None:
        _replace(self.diagnostics_view, "\n".join(self.authority.diagnostics()))

    # ------------------------------------------------------------ actions

    def save_dossier(self) -> None:
        if self.adjudication is None:
            messagebox.showinfo("Nothing to file",
                                "Submit a form before attempting to file it.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".sdwx", initialfile=f"{self.adjudication.reference}.sdwx",
            filetypes=[("Sandwich Dossier Exchange", "*.sdwx"), ("All files", "*.*")])
        if not path:
            return
        size = self.authority.save_dossier(self.adjudication, path, self.entry)
        self.status.set(f"Dossier filed: {path} ({size} bytes).")

    def open_dossier(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Sandwich Dossier Exchange", "*.sdwx"), ("All files", "*.*")])
        if not path:
            return
        try:
            dossier = sdwx.Dossier.read(Path(path))
        except (sdwx.SdwxError, OSError) as exc:
            messagebox.showerror("Unreadable dossier", str(exc))
            return
        text = ["\n".join(dossier.describe()), "", "=" * 74, ""]
        try:
            text.append(dossier.one(name="certificate.txt").decode("utf-8", "replace"))
        except sdwx.SdwxError:
            text.append("(no certificate section)")
        _replace(self.report_view, "\n".join(text))
        self.tabs.select(1)
        self.status.set(f"Opened {path}: {len(dossier)} sections, contents verified.")

    def verify_record(self) -> None:
        ok, complaints = self.authority.ledger.verify()
        if ok:
            messagebox.showinfo(
                "Permanent Record",
                f"Chain intact over {len(self.authority.ledger)} entries.")
        else:
            messagebox.showerror(
                "Permanent Record",
                "The chain is broken:\n\n" + "\n".join(complaints[:10]))
        self.status.set("Hash chain verified." if ok else "HASH CHAIN BROKEN.")

    def show_statistics(self) -> None:
        stats = self.authority.ledger.statistics()
        if not stats["count"]:
            messagebox.showinfo("Statistics", "The Record is empty.")
            return
        body = [f"Entries:     {stats['count']}",
                f"Applicants:  {stats['applicants']}",
                f"Mean score:  {stats['mean_score']}", ""]
        body += [f"{verdict:<14} {count}" for verdict, count in stats["verdicts"].items()]
        body += ["", f"Highest: {stats['best'].score:.2f}  {stats['best'].applicant}",
                 f"Lowest:  {stats['worst'].score:.2f}  {stats['worst'].applicant}"]
        messagebox.showinfo("Statistics", "\n".join(body))

    def rebuild_dll(self) -> None:
        from ..native import bridge

        report = bridge.load_engine(force_rebuild=True)
        self.authority.report = report
        self.authority.engine = report.engine
        self.authority.tribunal.engine = report.engine
        self.authority.tribunal.machine.engine = report.engine
        self._refresh_diagnostics()
        self.tabs.select(5)
        messagebox.showinfo(
            "Entropy engine",
            f"Backend: {report.engine.backend}\n{report.engine.detail}\n\n"
            f"{report.error or 'The DLL was rebuilt and loaded successfully.'}")

    def recompile_policies(self) -> None:
        problems = []
        for department in Department.all():
            try:
                self.authority.recompile(department.slug)
            except Exception as exc:
                problems.append(f"{department.slug}: {exc}")
        self._show_disassembly()
        self._refresh_diagnostics()
        if problems:
            messagebox.showerror("Recompilation", "\n".join(problems))
        else:
            self.status.set("All policies recompiled from source.")

    def about(self) -> None:
        from .. import MOTTO, __version__

        messagebox.showinfo(
            "About",
            f"Department of Sandwich Legitimacy, version {__version__}\n"
            f"{MOTTO}\n\n"
            f"Entropy engine: {self.authority.engine.backend}\n"
            f"{self.authority.engine.detail}\n\n"
            "No sandwich was consulted during the drafting of these regulations.")


def _make_text(parent: tk.Widget) -> tk.Text:
    text = tk.Text(parent, wrap="none", font=MONO, background=PAPER,
                   foreground=INK, relief="flat", padx=10, pady=8)
    yscroll = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
    xscroll = ttk.Scrollbar(parent, orient="horizontal", command=text.xview)
    text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set,
                   state="disabled")
    yscroll.pack(side="right", fill="y")
    xscroll.pack(side="bottom", fill="x")
    text.pack(side="left", fill="both", expand=True)
    return text


def _replace(widget: tk.Text, content: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    widget.configure(state="disabled")


def run(authority: Authority | None = None) -> int:
    """Open the counter. Returns a process exit code."""
    app = Counter(authority or Authority())
    app.mainloop()
    return 0
