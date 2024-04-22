# Copyright 2024 Vlad Krupinskii <mrvladus@yandex.ru>
# SPDX-License-Identifier: MIT

import time
from datetime import datetime

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # type:ignore
from icalendar import Calendar, Todo

from errands.lib.data import TaskData, TaskListData, UserData
from errands.lib.gsettings import GSettings
from errands.lib.logging import Log
from errands.lib.sync.sync import Sync
from errands.state import State
from errands.widgets.shared.components.boxes import ErrandsBox
from errands.widgets.shared.components.dialogs import ConfirmDialog
from errands.widgets.shared.components.menus import ErrandsMenuItem, ErrandsSimpleMenu
from errands.widgets.task import Task
from errands.widgets.task_list.task_list import TaskList


class TaskListSidebarRow(Gtk.ListBoxRow):
    def __init__(self, list_dict: TaskListData) -> None:
        super().__init__()
        self.uid: str = list_dict.uid
        self.name: str = list_dict.name
        self.__add_actions()
        self.__build_ui()
        # Add Task List page
        self.task_list: TaskList = TaskList(self)
        self.stack_page: Adw.ViewStackPage = State.view_stack.add_titled(
            child=self.task_list, name=self.name, title=self.name
        )
        self.update_ui(False)

    def __add_actions(self) -> None:
        group: Gio.SimpleActionGroup = Gio.SimpleActionGroup()
        self.insert_action_group(name="list_row", group=group)

        def _create_action(name: str, callback: callable) -> None:
            action: Gio.SimpleAction = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            group.add_action(action)

        def _delete(*args):
            def __confirm(_, res):
                if res == "cancel":
                    Log.debug("ListItem: Deleting list is cancelled")
                    return

                Log.info(f"Lists: Delete list '{self.uid}'")
                UserData.delete_list(self.uid)
                State.sidebar.remove_task_list(self)
                Sync.sync()

            ConfirmDialog(
                _("List will be permanently deleted"),
                _("Delete"),
                Adw.ResponseAppearance.DESTRUCTIVE,
                __confirm,
            )

        def _rename(*args):
            def _entry_activated(_, dialog: Adw.MessageDialog):
                if dialog.get_response_enabled("save"):
                    dialog.response("save")
                    dialog.close()

            def _entry_changed(entry: Gtk.Entry, _, dialog: Adw.MessageDialog):
                text = entry.props.text.strip(" \n\t")
                names = [i.name for i in UserData.get_lists_as_dicts()]
                dialog.set_response_enabled("save", text and text not in names)

            def _confirm(_, res, entry: Gtk.Entry):
                if res == "cancel":
                    Log.debug("ListItem: Editing list name is cancelled")
                    return
                Log.info(f"ListItem: Rename list '{self.uid}'")

                text: str = entry.props.text.rstrip().lstrip()
                UserData.update_list_props(self.uid, ["name", "synced"], [text, False])
                self.update_ui()
                State.trash_sidebar_row.update_ui()
                State.today_sidebar_row.update_ui()
                Sync.sync()

            entry: Gtk.Entry = Gtk.Entry(placeholder_text=_("New Name"))
            entry.get_buffer().props.text = self.label.get_label()
            dialog: Adw.MessageDialog = Adw.MessageDialog(
                transient_for=State.main_window,
                hide_on_close=True,
                heading=_("Rename List"),
                default_response="save",
                close_response="cancel",
                extra_child=entry,
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("save", _("Save"))
            dialog.set_response_enabled("save", False)
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect("response", _confirm, entry)
            entry.connect("activate", _entry_activated, dialog)
            entry.connect("notify::text", _entry_changed, dialog)
            dialog.present()

        def _export(*args):
            def _confirm(dialog, res):
                try:
                    file = dialog.save_finish(res)
                except Exception as e:
                    Log.debug(f"List: Export cancelled. {e}")
                    return

                Log.info(f"List: Export '{self.uid}'")

                tasks: list[TaskData] = UserData.get_tasks_as_dicts(self.uid)
                calendar: Calendar = Calendar()
                calendar.add("x-wr-calname", self.label.get_label())
                for task in tasks:
                    event = Todo()
                    event.add("uid", task.uid)
                    event.add("related-to", task.parent)
                    event.add("summary", task.text)
                    if task.notes:
                        event.add("description", task.notes)
                    event.add("priority", task.priority)
                    if task.tags:
                        event.add("categories", ",".join(task.tags))
                    event.add("percent-complete", task.percent_complete)
                    if task.color:
                        event.add("x-errands-color", task.color)
                    event.add(
                        "dtstart",
                        (
                            datetime.fromisoformat(task.start_date)
                            if task.start_date
                            else datetime.now()
                        ),
                    )
                    if task.due_date:
                        event.add(
                            "due",
                            (
                                datetime.fromisoformat(task.due_date)
                                if task.due_date
                                else datetime.now()
                            ),
                        )
                    calendar.add_component(event)

                try:
                    with open(file.get_path(), "wb") as f:
                        f.write(calendar.to_ical())
                except Exception as e:
                    Log.error(f"List: Export failed. {e}")
                    State.main_window.add_toast(_("Export failed"))

                State.main_window.add_toast(_("Exported"))

            filter: Gtk.FileFilter = Gtk.FileFilter()
            filter.add_pattern("*.ics")
            dialog: Gtk.FileDialog = Gtk.FileDialog(
                initial_name=f"{self.uid}.ics", default_filter=filter
            )
            dialog.save(State.main_window, None, _confirm)

        _create_action("delete", _delete)
        _create_action("rename", _rename)
        _create_action("export", _export)

    def __build_ui(self) -> None:
        self.props.height_request = 50
        self.add_css_class("sidebar-item")
        self.connect("activate", self._on_row_activated)

        # Drop controller
        drop_ctrl: Gtk.DropTarget = Gtk.DropTarget.new(
            type=Task, actions=Gdk.DragAction.MOVE
        )
        drop_ctrl.connect("drop", self._on_task_drop)
        self.add_controller(drop_ctrl)

        # Drag Hover controller
        drag_hover_ctrl: Gtk.DropControllerMotion = Gtk.DropControllerMotion()
        drag_hover_ctrl.connect("enter", self._on_drop_hover)
        self.add_controller(drag_hover_ctrl)

        # Title
        self.label: Gtk.Label = Gtk.Label(
            hexpand=True, halign=Gtk.Align.START, ellipsize=3
        )

        # Counter
        self.size_counter = Gtk.Label(css_classes=["dim-label", "caption"])

        self.set_child(
            ErrandsBox(
                spacing=12,
                margin_start=6,
                children=[
                    self.label,
                    self.size_counter,
                    Gtk.MenuButton(
                        menu_model=ErrandsSimpleMenu(
                            (
                                ErrandsMenuItem(_("Rename"), "list_row.rename"),
                                ErrandsMenuItem(_("Delete"), "list_row.delete"),
                                ErrandsMenuItem(_("Export"), "list_row.export"),
                            )
                        ),
                        icon_name="errands-more-symbolic",
                        css_classes=["flat"],
                        valign=Gtk.Align.CENTER,
                    ),
                ],
            )
        )

    def update_ui(self, update_task_list_ui: bool = True):
        Log.debug(f"Task List Row: Update UI '{self.uid}'")

        # Update title
        self.name = UserData.get_list_prop(self.uid, "name")
        self.label.set_label(self.name)
        self.stack_page.set_name(self.name)
        self.stack_page.set_title(self.name)

        # Update task list
        if update_task_list_ui:
            self.task_list.update_ui()

    def _on_drop_hover(self, ctrl: Gtk.DropControllerMotion, _x, _y):
        """
        Switch list on dnd hover after DELAY_SECONDS
        """

        DELAY_SECONDS: float = 0.7
        entered_at: float = time.time()

        def _switch_delay():
            if ctrl.contains_pointer():
                if time.time() - entered_at >= DELAY_SECONDS:
                    self.activate()
                    return False
                else:
                    return True
            else:
                return False

        GLib.timeout_add(100, _switch_delay)

    def _on_task_drop(self, _drop, task: Task, _x, _y):
        """
        Move task and sub-tasks to new list
        """

        if task.list_uid == self.uid:
            return
        old_task_list = task.task_list

        Log.info(f"Lists: Move '{task.uid}' to '{self.uid}' list")

        UserData.move_task_to_list(task.uid, task.list_uid, self.uid, "")

        if isinstance(task.parent, Task):
            task.parent.update_progress_bar()
            task.parent.update_title()

        task.purge()

        old_task_list.update_title()
        self.task_list.update_ui(False)

        Sync.sync()

    def _on_row_activated(self, *args) -> None:
        Log.debug(f"Sidebar: Switch to list '{self.uid}'")

        State.view_stack.set_visible_child_name(self.label.get_label())
        State.split_view.set_show_content(True)
        GSettings.set("last-open-list", "s", self.name)
