#!/usr/bin/env python3
#
# Set-up ssh X11 forwarding for copy-paste support
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# # Requires Python package "xerox-0.4.1"
# xhost +
# ssh -Y username@hostname
# export DISPLAY=localhost:10.0
#
# Notes
# ~~~~~
# Debugging approach #1
#   aiko.process.message.publish("$AIKO_NAMESPACE/DASHBOARD", f"Debug message")
#
#   mosquitto_sub -h $AIKO_MQTT_HOST -t $AIKO_NAMESPACE/DASHBOARD -v
#
# Debugging approach #2
#   from aiko_services.utilities import *
#   print_error("I've got a bad feeling about this !")
#
#   aiko_dashboard 1>/dev/null  # Debug messages only
#   aiko_dashboard 2>/dev/null  # TUI only
#
# Debugging approach #3
#   import logging
#   logging.basicConfig(format='%(message)s')
#   log = logging.getLogger(__name__)
#   log.warning("It's a trap !\r")
#
#   aiko_dashboard 1>/dev/null  # Debug messages only
#   aiko_dashboard 2>/dev/null  # TUI only
#
# To Do Elsewhere !
# ~~~~~~~~~~~~~~~~~
# * Turn Registrar into an ECProducer
# * Integrate into Ray HLActor ... Service, Actor, ECProducer, ECConsumer
#
# To Do
# ~~~~~
# * BUG: Dashboard doesn't display variables that contain whitespace !
#        Variable doesn't appear at all :(
#
# * BUG: When Dashboard exits, must clean-up (unshared) the ECConsumer !
#        Also, check that the ECProducer lease(s) expire as required
#
# * BUG: Dashboard isn't terminating ECConsumer lease extend timer :(
#
# - BUG: If currently selected Service terminates, then Dashboard doesn't
#        update selected Service's veriables section
#
# - BUG: Dashboard "Kill Service" command assumes running on same system
#        as the Service.  Should send "terminate" MQTT message to the
#        Service.  Or find a ProcessManager running on the correct host ?
#
# * FIX: Whenever DashboardFrame or LogFrame is destroyed and recreated
#        due to ResizeScreenError, all handlers need to be removed.
#        Provide DashboardFrame.cleanup() and LogFrame.cleanup(),
#        which are invoked by ResizeScreenError
#
# * FIX: Variable update dialogue replace with "maps.py:EnterLocation(Frame)"
#
# * FIX: Enable Service History selected "topic path" to clipboard
# * FIX: Enable Service History selected "topic path" can show LogFrame
# * FIX: Enable Service History multiple Service selection for logging
#
# - LogFrame should support LogLevelPopupMenu to update selected Service
# - LogFrame display and allow the Service's "log_level" to be updated
#
# * If Registrar isn't available, then display "Waiting for Registrar"
# - Secondary Registrars should periodically send a non-retained message to
#     the TOPIC_REGISTRAR_BOOT topic ... (secondary found ...) for Dashboard
# - Dashboard should show if ...
#   - TOPIC_REGISTRAR_BOOT indicates that there is no primary Registrar
#   - TOPIC_REGISTRAR_BOOT indicates that there is a primary Registrar,
#       but the primary is not responding to (history ...) or (share ...)
#   - TOPIC_REGISTRAR_BOOT indicates there are secondary Registrar(s)
#       and show their details
#
# * Keyboard "H": Toggles between "Services History" and "Service Log"
#
# * Show Services running: time_add and time running
# * Show Services history: tags, time_add, time_remove and time running
#
# - ArchiveService should record "+/+/+/log" and removed Services ...
#   - Dashboard History section can ECConsumer the removed Services
#
# - Consider how to efficiently provide Service summary lifecycle states
#
# - Selecting (mouse or tab key) Service allows ...
#   - Toggle show/hide Services with specific field values (share * * * *)
#   - Subscribe to MQTT messages ("s" key) from topic (/#, /out, /state)
#   - Publish MQTT message ("p" key) to topic (/#, /in, /control, ...)
#
# - Service variable details should sort variable names alphabetically
# - Toggle show/hide of Service variables "services.*" visually redundant
# - Toggle show/hide Service variables with specific names (regex)
# - Allow Service variables to be added and removed
#
# - Dashboard Web browser (JavaScript) implementation using MQTT / WebSockets
#   - Service / ECProducer / ECConsumer JavaScript implementation
#   - Integrate Dashboard into HighLighter Web !
#
# Service variables that Services should have ...
# - lifecycle state: ...
# - log level: info, debug, ...
# - statistics: busy/idle time, mailbox queue size, message count, uptime
#
# Actors that should have interesting variables ...
# - MQTT: statistics / variables ?
# - Host / Containers (root ProcessManager)
# - Registrar(s), LifeCycleManager(s), StorageManager(s), HyperSpace
# - Pipeline(s) / PipelineElement(s)
# - Ray node(s)

import click
from collections import defaultdict, deque
from subprocess import Popen
import xerox  # Clipboard support

from asciimatics.event import KeyboardEvent
from asciimatics.exceptions import (
    NextScene, ResizeScreenError, StopApplication
)
from asciimatics.parsers import AnsiTerminalParser
from asciimatics.scene import Scene
from asciimatics.screen import Screen
from asciimatics.widgets import (
    Frame, Label, Layout, MultiColumnListBox,
    PopUpDialog, PopupMenu, Text, TextBox, Widget
)
from asciimatics.widgets.utilities import THEMES

from aiko_services import *
from aiko_services.utilities import *

__all__ = ["LogUI", "ServiceFrame"]

_HISTORY_LIMIT = 32
_LOG_RING_BUFFER_SIZE = 128
_PLUGINS = {}  # updated by set_plugins()

BLACK = Screen.COLOUR_BLACK
WHITE = Screen.COLOUR_WHITE
GREEN = Screen.COLOUR_GREEN
FONT_BOLD = Screen.A_BOLD
FONT_NORMAL = Screen.A_NORMAL

NICE_COLORS = defaultdict(lambda: (WHITE, FONT_NORMAL, BLACK))
NICE_COLORS["focus_button"] = (GREEN, FONT_BOLD, BLACK)
NICE_COLORS["selected_focus_field"] = (GREEN, FONT_BOLD, BLACK)
NICE_COLORS["title"] = (BLACK, FONT_BOLD, WHITE)
THEMES["nice"] = NICE_COLORS

mqtt_configuration = get_mqtt_configuration()
mqtt_host = mqtt_configuration[0]
mqtt_port = mqtt_configuration[1]

class FrameCommon:
    def __init__(self, screen, height, width, has_border, name):
        super(FrameCommon, self).__init__(
            screen, height, width, has_border=has_border, name=name)
        self.adjust_palette_required = True

    def _add_service_bar(self):
        layout = Layout([1])
        self.add_layout(layout)
        service_title = Text()
        service_title.custom_colour = "title"
        service_title.disabled = True
        layout.add_widget(service_title)
        return service_title

    def _get_title(self, name=None, context=""):
        if name:
            title = f"{name}: "
        else:
            title = "AikoServices: "
        if context:
            title += context
        else:
            title += f"{mqtt_host[0:40]}:{mqtt_port}"
        return title

    def _add_title_bar(self):
        layout = Layout([3, 1])
        self.add_layout(layout)
        layout.add_widget(Label(self._get_title()), 0)
        layout.add_widget(Label('Press "?" for help', align=">"), 1)

    def _adjust_palette(self):
        self.palette = NICE_COLORS
        self.adjust_palette_required = False

    BLACK = 0
    RED = 1
    GREEN = 2
    YELLOW = 3
    BLUE = 4
    MAGENTA = 5
    CYAN = 6
    WHITE = 7

    # Reset: \x1b[0m  Color: \x1b[3?n

    def _color_text(self, color, text):
        return f"\x1b[3{color}m{text}"

    @property
    def frame_update_count(self):
        return 5  # assuming 20 FPS, then refresh screen at 4 Hz

    def _short_name(self, path):
        after_slash = path.rfind("/") + 1
        return path[after_slash:]

    def _update_ecproducer_variable(self, topic_path, name, value):
        topic_path_control = topic_path + "/control"
        payload_out = f"(update {name} {value})"
        aiko.message.publish(topic_path_control, payload_out)

# TODO: Replace _process_event_common() with the following ...
# https://asciimatics.readthedocs.io/en/stable/widgets.html#global-key-handling
    def _process_event_common(self, event):
        if isinstance(event, KeyboardEvent):
            if event.key_code in [ord("?")]:
                text = " Help   Aiko Services Dashboard\n"        \
                       " ----   -----------------------\n"        \
                       " Enter  Update variable value\n"          \
                       " Tab    Move to next section\n"           \
                       " c      Copy topic path to clipboard \n"  \
                       " l      Log level change\n"               \
                       " s      Select Service (toggle)\n"        \
                       " C      Clear selection\n"                \
                       " D      Show Dashboard page\n"            \
                       " K      Kill Service \n"                  \
                       " L      Show Log page\n"                  \
                       " S      Show Service specific page\n"     \
                       " x      Exit"
                self.scene.add_effect(
                    PopUpDialog(self._screen, text, ["OK"], theme="nice"))
            if event.key_code in [ord("x"), ord("X"), Screen.ctrl("c")]:
                self.services_cache = None
                services_cache_delete()
                raise StopApplication("User quit request")

    def _update_field(self, list, name, value, width):  # wrap long lines
        value = str(value)
        padding = 0
        while len(value):
            if isinstance(name, str):
                field = (name, " "*padding + value[0:width-padding])
                name = ""
            else:
                field = (" "*padding + value[0:width-padding],)
            list.append(field)
            value = value[width-padding:]
            if padding == 0:
                padding = 2

class DashboardFrame(FrameCommon, Frame):
    dashboard_singleton = None  # Instance reference for ServiceFrames plugins

    @classmethod
    def get_singleton(cls):
        return DashboardFrame.dashboard_singleton

    def __init__(self, screen, _):
        if not DashboardFrame.dashboard_singleton:
            DashboardFrame.dashboard_singleton = self

        super(DashboardFrame, self).__init__(
            screen, screen.height, screen.width, has_border=False,
            name="dashboard_frame"
        )

        self.ec_consumer = None
        self._ec_consumer_reset()
        self.services_row = -1
        self.selected_service = None
        self.selected_services = {}
        self.subscribed_service = None

        self.services_cache = services_cache_create_singleton(
            aiko.process, True, history_limit=_HISTORY_LIMIT)
    #   filter = ServiceFilter("*", "*", "*", "*", "*", "*")

        self._services_widget = MultiColumnListBox(
            screen.height * 1 // 3,
            ["<28", "<14", "<8", "<20", ">0"],  # Transport: <10 columns
            options=[],
            titles=["Service Topic", "Name", "Owner", "Protocol", "Transport"],
            on_change=self._on_change_services,
            parser=AnsiTerminalParser()
        )
        self._service_widget = MultiColumnListBox(
            screen.height * 1 // 2,
            ["<24", "<0"],
            options=[],
            titles=["Variable name", "Value"],
            on_select=self._on_select_variable
        )
        self._history_widget = MultiColumnListBox(  # Transport: <10 columns
            Widget.FILL_FRAME,
            ["<28", "<14", "<8", "<20", ">0"],
            options=[],
            titles=["Service history: Topic",
                    "Name", "Owner", "Protocol", "Transport"]
        )
        self._add_title_bar()
        layout_0 = Layout([1], fill_frame=True)
        self.add_layout(layout_0)
        layout_0.add_widget(self._services_widget)
        layout_0.add_widget(self._service_widget)
        layout_0.add_widget(self._history_widget)
        self.fix()  # Prepare Frame for use
        self._value_width = self._service_widget.width - 16

    def _ec_consumer_set(self, index):
        self._ec_consumer_reset()
        self.services_row = -1
        self.selected_service = None

        services = self.services_cache.get_services()
        services_topic_paths = services.get_topic_paths()
        if len(services_topic_paths) > index:
            self.services_row = index
            service_topic_path = services_topic_paths[index]
            self.selected_service = services.get_service(service_topic_path)
            self.service_tags = self.selected_service[5]
            if ServiceTags.match_tags(self.service_tags, ["ec=true"]):
                topic_control = f"{service_topic_path}/control"
                self.ec_consumer = ECConsumer(
                    aiko.process, 0, self.service_cache, topic_control)

    def _ec_consumer_reset(self):
        if self.ec_consumer:
            self.ec_consumer.terminate()
        self.ec_consumer = None
        self.service_cache = {}
        self.service_tags = None

    def _kill_service(self, service_topic_path):
        if service_topic_path.count("/") == 3:
            process_id = ServiceTopicPath.parse(service_topic_path).process_id
            if process_id.isnumeric():
                command_line = ["kill", "-9", process_id]
                Popen(command_line, bufsize=0, shell=False)
                self._ec_consumer_set(self.services_row + 1)

    def _service_selection_clear(self):
        self.selected_services = {}

    def _service_selection_color(self, color, topic_path, topic_path_show):
        topic_path_colored = topic_path_show
        if topic_path in self.selected_services:
            topic_path_colored = self._color_text(color, topic_path_show)
        return topic_path_colored

    def _service_selection_toggle(self, service):
        topic_path = service[0]
        if topic_path in self.selected_services:
            del self.selected_services[topic_path]
        else:
            self.selected_services[topic_path] = service

    def _on_change_services(self):
        row = self._services_widget.value
        if row is not None and row != self.services_row:
            self._ec_consumer_set(row)

    def _on_select_variable(self):
        text_box = TextBox(1, None, None, False, False)
        variable_name = None

        def _on_close(button_index):
            if button_index == 1:
                self._update_ecproducer_variable(
                    self.selected_service[0], variable_name, text_box.value[0])

        row = self._service_widget.value
        variable = self._service_widget.options[row]
        variable_name = variable[0][0]
        if len(variable_name) > 0:
            if variable_name != "Tag:" and not variable_name.endswith(" ..."):
                variable_value = variable[0][1]
                text_box.value[0] = variable_value
                title = f"Update {variable_name}" + " "*32
            # TODO: Replace with "maps.py:EnterLocation(Frame)"
                popup_dialog = PopUpDialog(
                    self._screen, title, ["Cancel", "OK"],
                    on_close=_on_close, theme="nice")
                layout = Layout([1])
                popup_dialog.add_layout(layout)
                layout.add_widget(text_box)
                popup_dialog.fix()
                self.scene.add_effect(popup_dialog)

    def _update(self, frame_no):
        if self.adjust_palette_required:
            self._adjust_palette()

        services = self.services_cache.get_services().copy()
        services_formatted = []
        for service in services:
            topic_path = ServiceTopicPath.parse(service[0])
            topic_path = self._service_selection_color(
                self.YELLOW, str(topic_path), topic_path.terse)
            protocol = self._short_name(service[2])
            services_formatted.append(
                (topic_path, service[1], service[4], protocol, service[3]))
        self._services_widget.options = [
            (service_info, row_index)
            for row_index, service_info in enumerate(services_formatted)
        ]

        variables = []
        if self.ec_consumer:
            service_variables = list(self.service_cache.items())
            for variable_name, variable_value in service_variables:
                if isinstance(variable_value, dict):
                    variables.append((f"{variable_name} ...", ""))
                    for name, value in variable_value.items():
                        variables.append((f"  {name}", f"{value}"))
                else:
                #   variables.append((variable_name, variable_value))
                    self._update_field(
                        variables, variable_name,
                        variable_value, self._value_width)
            variables.append(("", ""))

        if self.service_tags:
            for service_tag in self.service_tags:
            #   variables.append(("Tag:", service_tag))
                self._update_field(
                    variables, "Tag:", service_tag, self._value_width)

        self._service_widget.options = [
            (variable, row_index)
            for row_index, variable in enumerate(variables)
        ]

        service_history = list(self.services_cache.get_history())
        services_formatted = []
        for service in service_history:
            topic_path = ServiceTopicPath.parse(service[0]).terse
            protocol = self._short_name(service[2])
            services_formatted.append(
                (topic_path, service[1], service[4], protocol, service[3]))
        self._history_widget.options = [
            (service_info, row_index)
            for row_index, service_info in enumerate(services_formatted)
        ]

        super(DashboardFrame, self)._update(frame_no)

    def process_event(self, event):
        if isinstance(event, KeyboardEvent):
            if event.key_code in [ord("c")] and self.selected_service:
                xerox.copy(self.selected_service[0])
            if event.key_code in [ord("l")] and self.selected_service:
                self.scene.add_effect(LogLevelPopupMenu(self._screen,
                    self._services_widget, self.selected_service[0]))
            if event.key_code in [ord("s")] and self.selected_service:
                self._service_selection_toggle(self.selected_service)
            if event.key_code in [ord("C")]:
                self._service_selection_clear()
            if event.key_code in [ord("K")] and self.selected_service:
                self._kill_service(self.selected_service[0])
            if event.key_code in [ord("L")] and self.selected_service:
                raise NextScene("Log")
            if event.key_code in [ord("S")] and self.selected_service:
                self._raise_next_scene(self.selected_service)
        self._process_event_common(event)
        return super(DashboardFrame, self).process_event(event)

    def _raise_next_scene(self, service):
        service_name = service[1]
        service_protocol = self._short_name(service[2]).split(":")[0]
        names = [service_name, service_protocol]
        scene_name = [name for name in names if name in _PLUGINS]

        if scene_name:
            raise NextScene(scene_name[0])
        else:
            text = f" Service: {service_name} \n Does not have a custom page "
            self.scene.add_effect(
                PopUpDialog(self._screen, text, ["OK"], theme="nice"))

# ServiceFrame subclass __init__() must include "self.fix()"

class ServiceFrame(FrameCommon, Frame):
    def __init__(self, screen, dashboard, name="service_frame"):
        super(ServiceFrame, self).__init__(
            screen, screen.height, screen.width, has_border=False, name=name
        )

        self.dashboard = dashboard
        self.service = None
        self._add_title_bar()
        self._service_title = self._add_service_bar()
    #   self._value_width = self._service_widget.width

    def _update(self, frame_no):
        if self.adjust_palette_required:
            self._adjust_palette()

        if self.dashboard.selected_service != self.dashboard.subscribed_service:
            self.dashboard.subscribed_service = self.dashboard.selected_service
            self.service = self.dashboard.selected_service
            topic_path = self.service[0]
            name = self._short_name(self.service[1])
            self._service_title.value = f"Service: {topic_path}: {name}"
            self._service_frame_start(self.service, self.dashboard.ec_consumer)

        super(ServiceFrame, self)._update(frame_no)

    def _service_frame_start(self, service, service_ec_consumer):
        pass

    def _service_frame_stop(self, service):
        pass

    def process_event(self, event):
        if isinstance(event, KeyboardEvent):
            if event.key_code in [ord("D")]:
                self._service_frame_stop(self.service)
                self.dashboard.subscribed_service = None
                raise NextScene("Dashboard")
        self._process_event_common(event)
        return super(ServiceFrame, self).process_event(event)

# Re-usable logging user interface  # TODO: Consider turning into a Widget ?
# https://asciimatics.readthedocs.io/en/stable/widgets.html#custom-widgets

class LogUI:
    def __init__(self, parent, height=Widget.FILL_FRAME):
        self.parent = parent
        self.log_buffer = None
        self.recorder = None
        self.topic_log = None

        self._log_widget = MultiColumnListBox(
            height, ["<0"], options=[],
            titles=["Date       Time            Level Message"]
        )
        layout_0 = Layout([1])
        parent.add_layout(layout_0)
        recorder_label = Label(f"Log Recorder: TOPIC_PATH")
        recorder_label.custom_colour = "title"
        layout_0.add_widget(recorder_label)
        layout_1 = Layout([1], fill_frame=True)
        parent.add_layout(layout_1)
        layout_1.add_widget(self._log_widget)

    def _topic_log_handler(self, _aiko, topic, payload_in):
        self.log_buffer.append(payload_in)

    def _service_frame_start(self, service, service_ec_consumer):
        topic_path, _, _ = service[0].rpartition("/")
        topic_path += "/0"  # TODO: Use correct Service Id
        self.log_buffer = deque(maxlen=_LOG_RING_BUFFER_SIZE)
        self.topic_log = f"{topic_path}/log"
        aiko.process.add_message_handler(
            self._topic_log_handler, self.topic_log)

    def _service_frame_stop(self, service):
        aiko.process.remove_message_handler(
            self._topic_log_handler, self.topic_log)
        self.log_buffer = None
        self.topic_log = None

    def _update(self, frame_no):
        log_records = []
        if self.log_buffer:
            for log_record in self.log_buffer:
            #   log_records.append((log_record,))
                self.parent._update_field(
                    log_records, None, log_record, self._log_widget.width)
        self._log_widget.options = [
            (log_record, row_index)
            for row_index, log_record in enumerate(log_records)
        ]

class LogFrame(ServiceFrame):
    def __init__(self, screen, dashboard):
        super(LogFrame, self).__init__(screen, dashboard, name="log_frame")
        self.log_ui = LogUI(self)
        self.fix()  # Prepare Frame for use

    def _service_frame_start(self, service, service_ec_consumer):
        self.log_ui._service_frame_start(service, service_ec_consumer)

    def _service_frame_stop(self, service):
        self.log_ui._service_frame_stop(service)

    def _update(self, frame_no):
        super(LogFrame, self)._update(frame_no)
        self.log_ui._update(frame_no)

class LogLevelPopupMenu(PopupMenu):
    def __init__(self, screen, parent_widget, service_selected):
        self._dashboard = DashboardFrame.get_singleton()
        self._screen = screen
        self._parent_widget = parent_widget
        self._service_selected = service_selected

        menu_items = [
            ("Cancel", self._parent_widget.focus),
            ("Debug", self._button_handler),
            ("Error", self._button_handler),
            ("Info", self._button_handler),
            ("Warning", self._button_handler)]
        x = screen.width // 2 - 4
        y = screen.height // 3

        super().__init__(self._screen, menu_items, x, y)
        self.palette = NICE_COLORS

    def _button_handler(self):
        log_level = self.focussed_widget.text.upper()
        self._set_log_level(log_level)

    def _set_log_level(self, log_level):
        self._dashboard._update_ecproducer_variable(
            self._service_selected, "log_level", log_level)
        self._parent_widget.focus()

    def process_event(self, event):
        if isinstance(event, KeyboardEvent):
            if event.key_code in [ord("c")]:
                self._destroy()
            if self._dashboard.selected_service:
                if event.key_code in [ord("d")]:
                    self._set_log_level("DEBUG")
                    self._destroy()
                elif event.key_code in [ord("e")]:
                    self._set_log_level("ERROR")
                    self._destroy()
                elif event.key_code in [ord("i")]:
                    self._set_log_level("INFO")
                    self._destroy()
                elif event.key_code in [ord("w")]:
                    self._set_log_level("WARNING")
                    self._destroy()
        return super(LogLevelPopupMenu, self).process_event(event)

def run_dashboard(screen, start_scene):
    scenes = []
    dashboard = None
    for scene_name, scene_class in _PLUGINS.items():
        scene = Scene([scene_class(screen, dashboard)], -1, name=scene_name)
        if scene_name == "Dashboard":
            dashboard = DashboardFrame.get_singleton()
        scenes.append(scene)
    screen.play(scenes, stop_on_resize=True, start_scene=start_scene)

def update_plugins(plugins):
    if not _PLUGINS:
        _PLUGINS["Dashboard"] = DashboardFrame
        _PLUGINS["Log"] = LogFrame
    _PLUGINS.update(plugins)

@click.command()
@click.option("--history_limit", "-hl", type=click.INT, default=32,
    help="History length requested from Registrar")
@click.option("--plugin", "-p", multiple=True, required=False,
    default=["aiko_services.dashboard_plugins"])
def main(plugin, history_limit):
    global _HISTORY_LIMIT
    _HISTORY_LIMIT = history_limit

    for plugin_name in plugin:
        try:
            module = load_module(plugin_name) if plugin_name else None
        except ModuleNotFoundError:
            module = None
        update_plugins(module.plugins if module else {})

    scene = None
    while True:
        try:
            Screen.wrapper(
                run_dashboard, catch_interrupt=True, arguments=[scene]
            )
            break
        except ResizeScreenError as exception:
            scene = exception.scene

if __name__ == "__main__":
    main()
