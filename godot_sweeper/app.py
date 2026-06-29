"""
app.py — godot-sweeper GUI (PySide6)

Flow: pick a folder -> scan for projects -> see what cache exists ->
tick what you want gone -> preview total -> clean.

Nothing gets deleted without an explicit click on Clean and a confirm
dialog. The UI never deletes directly; it calls core.delete_items, which
re-checks every path against the safety gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from godot_sweeper import core

# role used to stash the CacheItem on a tree row
ITEM_ROLE = Qt.ItemDataRole.UserRole


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("godot-sweeper")
        self.resize(720, 480)

        # state: project_root -> ProjectScan
        self._scans: dict[Path, core.ProjectScan] = {}

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # --- top bar: folder picker -----------------------------------
        top = QHBoxLayout()
        self.path_label = QLabel("No folder selected")
        self.path_label.setStyleSheet("color: palette(mid);")
        pick_btn = QPushButton("Choose folder…")
        pick_btn.clicked.connect(self.on_pick_folder)
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self.on_scan)
        self.scan_btn.setEnabled(False)
        top.addWidget(self.path_label, stretch=1)
        top.addWidget(pick_btn)
        top.addWidget(self.scan_btn)
        layout.addLayout(top)

        # --- the tree of projects/items -------------------------------
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Cache", "Size"])
        self.tree.setColumnCount(2)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self.on_item_changed)
        layout.addWidget(self.tree, stretch=1)

        # --- bottom bar: select-all, total, clean ---------------------
        bottom = QHBoxLayout()
        self.select_all = QCheckBox("Select all")
        self.select_all.stateChanged.connect(self.on_select_all)
        self.total_label = QLabel("Nothing selected")
        self.clean_btn = QPushButton("Clean selected")
        self.clean_btn.clicked.connect(self.on_clean)
        self.clean_btn.setEnabled(False)
        bottom.addWidget(self.select_all)
        bottom.addStretch(1)
        bottom.addWidget(self.total_label)
        bottom.addWidget(self.clean_btn)
        layout.addLayout(bottom)

        self._search_root: Path | None = None
        self._suppress_item_signal = False

    # --- folder picking ----------------------------------------------
    def on_pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Pick a folder to scan")
        if not folder:
            return
        self._search_root = Path(folder)
        self.path_label.setText(folder)
        self.path_label.setStyleSheet("")  # reset to default palette color
        self.scan_btn.setEnabled(True)

    # --- scanning -----------------------------------------------------
    def on_scan(self) -> None:
        if not self._search_root:
            return
        self.tree.clear()
        self._scans.clear()
        self.select_all.setChecked(False)

        projects = core.find_projects(self._search_root)
        if not projects:
            QMessageBox.information(
                self, "Nothing found",
                "No Godot projects (project.godot) under that folder.",
            )
            self._update_total()
            return

        self._suppress_item_signal = True
        any_items = False
        for proj in projects:
            scan = core.scan_project(proj)
            if not scan.items:
                continue  # project with no cache — skip, keeps list clean
            any_items = True
            self._scans[proj] = scan

            parent = QTreeWidgetItem(self.tree)
            parent.setText(0, str(proj))
            parent.setText(1, scan.human_total)
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            parent.setCheckState(0, Qt.CheckState.Unchecked)
            parent.setExpanded(True)

            for item in scan.items:
                row = QTreeWidgetItem(parent)
                row.setText(0, item.path.name)
                row.setText(1, item.human_size)
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(0, Qt.CheckState.Unchecked)
                row.setData(0, ITEM_ROLE, item)
        self._suppress_item_signal = False

        if not any_items:
            QMessageBox.information(
                self, "All clean",
                "Found projects, but no cache to remove. Nice.",
            )
        self._update_total()

    # --- checkbox plumbing -------------------------------------------
    def on_item_changed(self, changed: QTreeWidgetItem, _col: int) -> None:
        if self._suppress_item_signal:
            return
        self._suppress_item_signal = True
        # if a parent toggled, cascade to children
        if changed.childCount() > 0:
            state = changed.checkState(0)
            for i in range(changed.childCount()):
                changed.child(i).setCheckState(0, state)
        else:
            # a child toggled — sync parent to reflect children
            parent = changed.parent()
            if parent is not None:
                states = [parent.child(i).checkState(0)
                          for i in range(parent.childCount())]
                if all(s == Qt.CheckState.Checked for s in states):
                    parent.setCheckState(0, Qt.CheckState.Checked)
                elif all(s == Qt.CheckState.Unchecked for s in states):
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self._suppress_item_signal = False
        self._update_total()

    def on_select_all(self, state: int) -> None:
        if self._suppress_item_signal:
            return
        check = (Qt.CheckState.Checked if state == Qt.CheckState.Checked.value
                 else Qt.CheckState.Unchecked)
        self._suppress_item_signal = True
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setCheckState(0, check)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, check)
        self._suppress_item_signal = False
        self._update_total()

    # --- total + clean -----------------------------------------------
    def _selected_by_project(self) -> dict[Path, list[core.CacheItem]]:
        out: dict[Path, list[core.CacheItem]] = {}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            proj = Path(top.text(0))
            chosen = []
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    item = child.data(0, ITEM_ROLE)
                    if item is not None:
                        chosen.append(item)
            if chosen:
                out[proj] = chosen
        return out

    def _update_total(self) -> None:
        selected = self._selected_by_project()
        total = sum(i.size_bytes for items in selected.values() for i in items)
        count = sum(len(items) for items in selected.values())
        if count == 0:
            self.total_label.setText("Nothing selected")
            self.clean_btn.setEnabled(False)
        else:
            self.total_label.setText(
                f"{count} item(s) · {core.human_bytes(total)} to free"
            )
            self.clean_btn.setEnabled(True)

    def on_clean(self) -> None:
        selected = self._selected_by_project()
        if not selected:
            return
        total = sum(i.size_bytes for items in selected.values() for i in items)
        count = sum(len(items) for items in selected.values())

        confirm = QMessageBox.question(
            self,
            "Delete cache?",
            f"This will permanently delete {count} cached item(s) "
            f"({core.human_bytes(total)}).\n\n"
            "Godot regenerates these next time you open the project. "
            "Source files are never touched.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        freed = 0
        errors: list[tuple[Path, str]] = []
        for proj, items in selected.items():
            scan = self._scans.get(proj)
            if scan is None:
                continue
            res = core.delete_items(scan, items)
            freed += res.freed_bytes
            errors.extend(res.errors)

        if errors:
            detail = "\n".join(f"{p}: {msg}" for p, msg in errors[:10])
            QMessageBox.warning(
                self, "Done with some errors",
                f"Freed {core.human_bytes(freed)}, but some items failed:\n\n{detail}",
            )
        else:
            QMessageBox.information(
                self, "Done",
                f"Cleaned up {core.human_bytes(freed)}. Good as new.",
            )
        # rescan to refresh the view
        self.on_scan()


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
